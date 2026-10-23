#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_prepare_note.py — Prepare all assets for Obsidian note generation.

One-shot command that:
  1. Checks if note already exists (skip if so)
  2. Fetches metadata (title, authors, year, venue, DOI, tags)
  3. Parses full.md to extract image→figure mapping
  4. Preserves independent source blocks for PDF identity review
  5. Generates semantic filenames from captions
  6. Creates asset directory + copies/renames images
  7. Builds YAML frontmatter from Zotero tags
  8. Outputs structured JSON for agent consumption

This eliminates 20-30 agent tool calls down to 1.
NOT responsible for: note body writing (that's the LLM's job).
"""

from __future__ import annotations

import json
import os
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

from ..api import ZoteroAPI, ZoteroAPIError
from ..mineru_lookup import MineruLookup
from ..config import ZoteroConfig
from ..frontmatter_tags import (
    ensure_tags_have_status,
    find_note_by_zotero_key,
    note_filename_for_title,
)
from ..link_sync import resolve_pdf_key

# ─── Constants ────────────────────────────────────────────────────────────────────────────────────────────────────────

kMaxCaptionWordsForName = 4
# Asset names are stable block identifiers. They intentionally do not encode a
# printed Figure number: MinerU block order and paper Figure numbering are
# different namespaces. The verified number remains in the JSON ledger and in
# the Markdown alt text.
kDefaultFigPrefix = "asset-"
# Used when a title slugifies to nothing (see `_Slugify`).
kFallbackSlugPrefix = "paper"
kCompositeVerticalGap = 24  # MinerU coordinates are normalized to 0..1000.
kCompositePaddingX = 4
kCompositePaddingTop = 4
# Bottom padding is deliberately smaller than the top. The old value (22) was
# sized to catch a panel caption that MinerU places just under the crop, but it
# also dragged in the next paragraph on the page, so every figure carried a
# conspicuous band of body text under it. MinerU's own captions are re-attached
# from `content_list.json` (`_ContentListCaptions`), so the crop does not need a
# wide net: 10 still reaches a caption line, without the following prose.
kCompositePaddingBottom = 10
kCompositeDpi = 180
# Figures are cropped from a 180 DPI page render, so a full-width diagram is
# already ~1500px wide. Quality 95 on top of that was spending bytes on detail
# nobody sees; 88 is visually identical at reading size and roughly a third
# smaller, which matters because the vault's asset tree is over 100 MB.
kFigureJpegQuality = 88


# ─── Public ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'prepare-note' subcommand."""
    p = subparsers.add_parser(
        "prepare-note",
        help="Prepare assets + metadata for Obsidian note generation",
    )
    p.add_argument("key", help="Zotero parent item key")
    p.add_argument("--attachment-key", help="Exact PDF attachment selected by mineru resolve")
    p.add_argument("--json", action="store_true", help="Output JSON (default: human summary)")
    p.add_argument("--force", action="store_true", help="Proceed even if note already exists")
    p.add_argument(
        "--with-captions",
        action="store_true",
        help=(
            "Include the full per-figure `caption` and `caption_short` in the JSON. "
            "Off by default: `md_ref` already embeds the caption as its alt text, so "
            "emitting both pays for the same Chinese sentence twice (measured ~0.3-0.5K "
            "tokens on a 10-figure paper). Pass this only when the caller needs the raw "
            "caption for something other than the alt text."
        ),
    )
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute prepare-note. Returns exit code."""
    api = ZoteroAPI(config.api_url, config.user_id)
    cache = MineruLookup(config, api)

    # ─── 1. Fetch metadata ────────────────────────────────────────────────────────────────────────────────────────────
    try:
        item = api.get_item(args.key)
    except ZoteroAPIError as e:
        _Error(f"Cannot fetch item: {e}")
        return 1

    data = item.get("data", {})
    title = data.get("title", "").strip()
    if not title:
        _Error("Item has no title")
        return 1

    # ─── 2. Check if note exists ──────────────────────────────────────────────────────────────────────────────────────
    # Sanitize title for filename (remove chars illegal in filesystems).
    # NOTE: `$` is legal on POSIX but breaks Obsidian wikilinks ([[M$^2$E-UAV ...]]
    # does not resolve), so strip LaTeX-math dollar signs as well.
    note_filename = note_filename_for_title(title)
    # Prefer an existing note for this key even if its filename differs, so a
    # rename in Obsidian (or a note written before this sanitiser) is not
    # reported as "new" and then duplicated.
    note_path = (
        find_note_by_zotero_key(config.literature_notes, args.key)
        or os.path.join(config.literature_notes, note_filename)
    )
    note_filename = os.path.basename(note_path)

    if os.path.isfile(note_path) and not args.force:
        result = {
            "status": "exists",
            "note_path": note_path,
            "message": f"Note already exists: {note_filename}",
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"[SKIP] Note already exists: {note_filename}")
        return 0

    # ─── 3. Check MinerU cache ────────────────────────────────────────────────────────────────────────────────────────
    source_key = getattr(args, "attachment_key", None) or args.key
    if getattr(args, "attachment_key", None):
        try:
            attachment = api.get_item(source_key).get("data", {})
        except ZoteroAPIError:
            _Error("Cannot resolve selected attachment")
            return 1
        if attachment.get("parentItem") != args.key or attachment.get("contentType") != "application/pdf":
            _Error("Selected PDF does not belong to the parent item")
            return 1
    cache_dir = cache.find_cache_dir(source_key)
    if not cache_dir:
        _Error(
            f"No MinerU cache for {args.key}. Run: "
            f"python3 ~/scholar-agent/scripts/literature/zotero_cli.py mineru-parse {args.key}"
        )
        return 2

    full_md_path = os.path.join(cache_dir, "full.md")
    if not os.path.isfile(full_md_path):
        _Error(f"full.md not found in cache: {cache_dir}")
        return 2

    # ─── 4. Parse figures from full.md ────────────────────────────────────────────────────────────────────────────────
    with open(full_md_path, "r", encoding="utf-8") as f:
        full_md_content = f.read()

    # MinerU has emitted two cache shapes in practice:
    #   1. legacy: full.md contains ![](images/...) image references;
    #   2. current: full.md contains captions only, while manifest.json holds
    #      the authoritative image paths and figure metadata.
    #
    # Prefer the structured manifest when available. Falling back to Markdown
    # keeps compatibility with legacy caches that have no manifest.
    manifest = _ReadManifest(cache_dir)
    raw_figures = _PrepareFigureEntries(manifest, cache_dir, full_md_content)
    content_chars = len(full_md_content)

    # ─── 5. Build slug + asset dir ────────────────────────────────────────────────────────────────────────────────────
    slug = _Slugify(title)
    asset_rel_dir = f"assets/{slug}/"
    asset_abs_dir = os.path.join(config.literature_notes, asset_rel_dir)
    os.makedirs(asset_abs_dir, exist_ok=True)

    # ─── 6. Copy + rename images ──────────────────────────────────────────────────────────────────────────────────────
    figures = []
    # Captions are duplicated inside `md_ref`; see the per-figure entry below.
    with_captions = bool(getattr(args, "with_captions", False))
    used_filenames: set[str] = set()
    render_dir = tempfile.mkdtemp(prefix="zotero_prepare_pages_")
    page_cache: dict[int, str] = {}
    pdf_path = _ResolvePdfPath(source_key, api)
    try:
        for i, fig in enumerate(raw_figures):
            source_paths = fig.get("source_paths") or [fig.get("src_path", "")]
            source_paths = [path for path in source_paths if path]
            if not source_paths:
                continue

            source_files = []
            for source_path in source_paths:
                source_file = os.path.join(cache_dir, source_path)
                if not os.path.isfile(source_file):
                    _RestoreImageFromPDF(source_key, source_path, cache_dir, api)
                if os.path.isfile(source_file):
                    source_files.append(source_file)

            if not source_files:
                continue

            # Composite groups are always JPEGs. Single images keep their
            # original extension and the legacy copy behavior.
            is_composite = len(source_files) > 1
            ext = ".jpg" if is_composite else (os.path.splitext(source_files[0])[1] or ".jpg")
            semantic_name = _UniqueFilename(
                _SemanticFilename(
                    i + 1,
                    fig.get("label", ""),
                    fig.get("caption", ""),
                    ext,
                    fig.get("figure_number"),
                ),
                used_filenames,
            )
            dst_path = os.path.join(asset_abs_dir, semantic_name)

            composed = False
            if is_composite:
                composed = _ComposeFigureGroup(
                    fig,
                    source_files,
                    dst_path,
                    pdf_path,
                    render_dir,
                    page_cache,
                )

            if not composed:
                shutil.copy2(source_files[0], dst_path)

            with open(dst_path, "rb") as asset_file:
                asset_hash = hashlib.sha256(asset_file.read()).hexdigest()

            # Build markdown reference. `md_ref` carries the caption as alt text,
            # which is what the note body needs — see the entry below.
            label = fig.get("label", f"Asset {i + 1}")
            caption_short = fig.get("caption_short", "")
            md_ref = f"![{label}. {caption_short}]({asset_rel_dir}{semantic_name})"

            entry = {
                "index": i + 1,
                "asset_id": semantic_name.rsplit(ext, 1)[0],
                "label": label,
                "figure_number": fig.get("figure_number"),
                "figure_number_source": fig.get("figure_number_source", "unknown"),
                "filename": semantic_name,
                "sha256": asset_hash,
                "md_ref": md_ref,
                "composite": is_composite,
                "source_count": len(source_files),
                # Private ledger fields. They preserve the evidence used to
                # assign the printed Figure number; filenames remain block IDs.
                "source_paths": source_paths,
                "page_idx": fig.get("page_idx"),
                "bbox": fig.get("bbox"),
            }
            # The caption text is already inside `md_ref` as its alt text. Repeat
            # it as a separate field only when explicitly asked, otherwise the
            # JSON pays for every Chinese caption twice.
            if with_captions:
                entry["caption"] = fig.get("caption", "")
                entry["caption_short"] = caption_short
            figures.append(entry)
        with open(os.path.join(asset_abs_dir, "figure-candidates.json"), "w", encoding="utf-8") as ledger:
            source_hash = None
            if pdf_path:
                with open(pdf_path, "rb") as source_pdf:
                    source_hash = hashlib.sha256(source_pdf.read()).hexdigest()
            json.dump({"schema": 2, "source_pdf_sha256": source_hash, "assets": [
                {"filename": item["filename"], "sha256": item["sha256"],
                 "figures": [str(item["figure_number"])] if item.get("figure_number") is not None else [],
                 "status": "needs-review",
                 "evidence": {"page": item["page_idx"] + 1 if isinstance(item.get("page_idx"), int) else None,
                              "method": None, "bbox_mineru": item.get("bbox")},
                 "source_paths": item.get("source_paths")}
                for item in figures]}, ledger, ensure_ascii=False, indent=2)

    finally:
        shutil.rmtree(render_dir, ignore_errors=True)

    # ─── 7. Build YAML frontmatter ────────────────────────────────────────────────────────────────────────────────────
    authors = [
        f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
        for c in data.get("creators", [])
    ]
    zotero_tags = [t["tag"] for t in data.get("tags", [])]

    # Ensure minimum required tags
    tags = [t for t in zotero_tags if t.split("/", 1)[0] in {"type", "area", "topic", "method", "task", "software", "hardware", "source"}]
    if not any(t.startswith("type/") for t in tags):
        tags.insert(0, "type/paper")
    tags = ensure_tags_have_status(tags, note_path if os.path.isfile(note_path) else None)

    year_str = (data.get("date", "") or "")[:4]
    year = int(year_str) if year_str.isdigit() else None

    frontmatter_lines = ["---", "note_contract: 2"]
    frontmatter_lines.append("title: " + json.dumps(title, ensure_ascii=False))
    frontmatter_lines.append("tags:")
    for tag in sorted(tags):
        frontmatter_lines.append(f"  - {tag}")
    frontmatter_lines.append(f"authors: {json.dumps(authors, ensure_ascii=False)}")
    if year:
        frontmatter_lines.append(f"year: {year}")
    venue = data.get("publicationTitle") or data.get("repository") or ""
    if venue:
        frontmatter_lines.append("venue: " + json.dumps(venue, ensure_ascii=False))
    from datetime import date
    frontmatter_lines.append(f"date: {date.today().isoformat()}")
    doi = data.get("DOI", "")
    if doi:
        frontmatter_lines.append("doi: " + json.dumps(doi, ensure_ascii=False))
    # The SOP tells the agent to copy this frontmatter verbatim and promises it
    # already carries both Zotero keys. They were in fact added later by
    # write-note/assemble-note link injection, so an agent that hand-wrote the
    # note from this block alone produced a note with no zotero_key. Emit them
    # here too; link injection then simply re-affirms them (it is idempotent).
    frontmatter_lines.append(f"zotero_key: {args.key}")
    # Must be the attachment *key* (8 chars), not a filesystem path:
    # link_sync/verify-note and the ``zotero://open-pdf`` URI all address
    # attachments by key. ``_ResolvePdfPath`` returns an absolute path and is
    # for reading the PDF locally, so it is the wrong helper here.
    pdf_key = getattr(args, "attachment_key", None) or resolve_pdf_key(args.key, api)
    if pdf_key:
        frontmatter_lines.append(f"zotero_pdf_key: {pdf_key}")
    from ..source_links import public_url
    source_url = public_url(data.get("url"))
    if source_url:
        frontmatter_lines.append("url: " + json.dumps(source_url, ensure_ascii=False))
    frontmatter_lines.append("---")
    frontmatter = "\n".join(frontmatter_lines)

    # ─── 8. Output result ─────────────────────────────────────────────────────────────────────────────────────────────
    result = {
        "status": "ready",
        "key": args.key,
        "title": title,
        "note_path": note_path,
        "note_filename": note_filename,
        "asset_dir": asset_rel_dir,
        "asset_abs_dir": asset_abs_dir,
        "slug": slug,
        "frontmatter": frontmatter,
        "figures": figures,
        "figure_count": len(figures),
        "figure_review": "required-before-write",
        "figure_candidates": os.path.join(asset_abs_dir, "figure-candidates.json"),
        "content_chars": content_chars,
        "content_cmd": f"python3 ~/scholar-agent/scripts/literature/zotero_cli.py mineru read {source_key} --section 0",
        "attachment_key": pdf_key,
        "authors": authors,
        "year": year,
        "venue": venue,
        "tags": tags,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"[OK] Prepared note for: {title}")
        print(f"  Note path:  {note_path}")
        print(f"  Asset dir:  {asset_abs_dir}")
        print(f"  Figures:    {len(figures)} images copied")
        print(f"  Content:    {content_chars} chars")
        print(f"  Tags:       {', '.join(tags)}")
        print(f"\n  Next: python3 scripts/literature/zotero_cli.py mineru read {args.key} --section 0")

    return 0


# ─── Private Helpers ──────────────────────────────────────────────────────────────────────────────────────────────────


def _ExtractFigures(md_content: str) -> list[dict]:
    """Parse full.md to extract image references with their captions.

    Pattern: ![](images/HASH.ext) followed by Fig/Figure N. caption on next line.
    """
    lines = md_content.split("\n")
    figures = []
    img_pattern = re.compile(r"!\[\]\((images/[a-f0-9]+\.\w+)\)")
    caption_pattern = re.compile(r"^(Fig(?:ure)?\.?\s*(\d+)[.:]\s*)(.+)", re.IGNORECASE)

    i = 0
    while i < len(lines):
        match = img_pattern.search(lines[i])
        if match:
            src_path = match.group(1)

            # Look ahead for caption (next 1-3 non-empty lines)
            label = ""
            caption = ""
            fig_num = ""
            for offset in range(1, 4):
                if i + offset >= len(lines):
                    break
                next_line = lines[i + offset].strip()
                if not next_line:
                    continue
                cap_match = caption_pattern.match(next_line)
                if cap_match:
                    fig_num = cap_match.group(2)
                    label = f"Fig. {fig_num}"
                    caption = cap_match.group(3).strip()
                    break
                # If line starts with text (not image), might be inline caption
                if not next_line.startswith("!["):
                    # Could be part of a multi-image figure group
                    break

            # Generate short caption for filename
            caption_short = caption[:80] if caption else ""

            figures.append({
                "src_path": src_path,
                "source_paths": [src_path],
                "label": label or f"Image {len(figures) + 1}",
                "fig_num": fig_num,
                "caption": caption,
                "caption_short": caption_short,
            })

        i += 1

    return figures


def _ReadManifest(cache_dir: str) -> dict | None:
    """Read a MinerU manifest from a known cache directory, if present."""
    manifest_path = os.path.join(cache_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        return None

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    return data if isinstance(data, dict) else None


def _ExtractFiguresFromManifest(manifest: dict | None) -> list[dict]:
    """Extract ordered, deduplicated figure entries from a MinerU manifest.

    Newer MinerU exports often omit Markdown image links from ``full.md`` but
    retain complete figure records in ``manifest.json``.  Those records are
    the source of truth for asset copying because they include the image path,
    label, and caption already associated with the paper section.
    """
    if not manifest:
        return []

    entries: list[dict] = []
    for section in manifest.get("sections", []):
        if not isinstance(section, dict):
            continue
        entries.extend(section.get("figures", []))

    # Some manifests expose a flattened list instead of per-section figures.
    if not entries:
        entries = manifest.get("allFigures", [])

    figures: list[dict] = []
    seen_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        src_path = str(entry.get("path", "")).strip()
        if not src_path or src_path in seen_paths:
            continue
        seen_paths.add(src_path)

        label = str(entry.get("label", "")).strip()
        base_label = str(entry.get("baseLabel", "")).strip()
        caption = str(entry.get("caption", "")).strip()
        fig_num_match = re.search(r"(\d+)", label or base_label)
        fig_num = fig_num_match.group(1) if fig_num_match else ""

        figures.append({
            "src_path": src_path,
            "source_paths": [src_path],
            "label": label or base_label or f"Image {len(figures) + 1}",
            "fig_num": fig_num,
            "caption": caption,
            "caption_short": caption[:80] if caption else "",
        })

    return figures


def _ReadContentList(cache_dir: str) -> list[dict]:
    """Read the current or legacy MinerU content-list filename."""
    candidates = []
    preferred = os.path.join(cache_dir, "content_list.json")
    if os.path.isfile(preferred):
        candidates.append(preferred)

    try:
        candidates.extend(
            os.path.join(cache_dir, name)
            for name in sorted(os.listdir(cache_dir))
            if name.endswith("_content_list.json")
        )
    except OSError:
        return []

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            continue
    return []


def _PrepareFigureEntries(
    manifest: dict | None,
    cache_dir: str,
    full_md_content: str,
) -> list[dict]:
    """Return figure entries in printed reading order.

    ``_ExtractFigureGroups`` already returns groups in reading order — it is the
    only source with page coordinates. The manifest is *not* a reliable order for
    figures the group pass also produced: its ``allFigures`` list omits chart
    blocks, so emitting groups in manifest order pushed Figure 6 (a chart) after
    Figure 7. Groups therefore lead, and manifest-only figures (those the content
    list did not place) are appended rather than used to re-seat the rest.
    """
    manifest_figures = _ExtractFiguresFromManifest(manifest)
    legacy_figures = manifest_figures or _ExtractFigures(full_md_content)
    grouped = _ExtractFigureGroups(manifest, cache_dir, full_md_content)
    if not grouped:
        _RepairFigureNumbers(legacy_figures, full_md_content)
        return legacy_figures

    covered = {
        path for group in grouped for path in group.get("source_paths", [])
    }
    ordered: list[dict] = list(grouped)
    for figure in legacy_figures:
        if figure.get("src_path", "") not in covered:
            ordered.append(figure)
    _RepairFigureNumbers(ordered, full_md_content)
    return ordered


def _FigureMetadata(manifest: dict | None) -> dict[str, dict]:
    """Index manifest labels/captions by image path.

    Both manifest shapes must be read. ``sections[].figures`` and the flattened
    ``allFigures`` are alternatives, not layers: a cache that MinerU exported with
    only ``allFigures`` (measured shape for 2 of 254 caches locally) previously
    produced an *empty* index here, so every figure lost its label and caption and
    was renamed ``figN-figure.jpg``. Reading neither is silent — the group code
    treats a missing record as "no caption" rather than as an error.
    """
    metadata: dict[str, dict] = {}
    if not manifest:
        return metadata

    def _record(entry: dict) -> None:
        path = str(entry.get("path", "")).strip()
        if not path:
            return
        existing = metadata.setdefault(
            path, {"label": "", "caption": "", "caption_hints": []}
        )
        label = str(entry.get("label", "")).strip()
        caption = str(entry.get("caption", "")).strip()
        if label and not existing["label"]:
            existing["label"] = label
        if caption and not existing["caption"]:
            existing["caption"] = caption

    for section in manifest.get("sections", []):
        if not isinstance(section, dict):
            continue
        for entry in section.get("figures", []):
            if isinstance(entry, dict):
                _record(entry)

    for entry in manifest.get("allFigures", []) or []:
        if isinstance(entry, dict):
            _record(entry)

    for entry in manifest.get("allTables", []) or []:
        if isinstance(entry, dict):
            _record(entry)

    # figureBlocks are useful when MinerU splits one figure into several
    # image records and only attaches the caption to the block as a whole.
    for block in manifest.get("figureBlocks", []) or []:
        if not isinstance(block, dict):
            continue
        image_paths = [
            str(path).strip()
            for path in block.get("imagePaths", [])
            if str(path).strip()
        ]
        hints = [
            str(hint).strip()
            for hint in block.get("captionHints", [])
            if str(hint).strip()
        ]
        labels = [
            str(label).strip()
            for label in block.get("labelHints", [])
            if str(label).strip()
        ]
        for path in image_paths:
            record = metadata.setdefault(
                path,
                {"label": "", "caption": "", "caption_hints": []},
            )
            if not record.get("label") and labels:
                record["label"] = labels[0]
            record.setdefault("caption_hints", []).extend(hints)

    return metadata


def _ContentListCaptions(cache_dir: str) -> dict[str, str]:
    """Map image path → caption, read from ``content_list.json`` across block types.

    This is the most reliable caption source: it is per-block, so it survives the
    manifest being thin, and it covers ``chart`` blocks — which carry real figure
    captions (Fig. 6, Fig. 8 in the EventCV paper) but were previously skipped
    entirely because only ``type == "image"`` was recognized. 1,011 chart blocks
    exist across the local corpus, so this is not a corner case.
    """
    captions: dict[str, str] = {}
    for item in _ReadContentList(cache_dir):
        path = str(item.get("img_path") or item.get("table_path") or "").strip()
        if not path:
            continue
        for key in (
            "image_caption",
            "chart_caption",
            "table_caption",
            "image_footnote",
            "chart_footnote",
        ):
            value = item.get(key)
            if not value:
                continue
            text = " ".join(str(part) for part in value).strip()
            if text:
                captions[path] = text
                break
    return captions


def _ExtractFigureCaptions(full_md_content: str) -> dict[str, str]:
    """Extract full Figure N captions from MinerU Markdown when available.

    Accepts the ``Figure N:`` form *and* the ``Fig. N.`` form, which is what
    arXiv/venue PDFs overwhelmingly use. Requiring the colon meant a paper set in
    the ``Fig. N.`` style yielded an empty caption map, silently disabling the
    "repair" pass it feeds rather than reporting that it found nothing.
    """
    captions: dict[str, str] = {}
    lines = full_md_content.splitlines()
    label_re = re.compile(r"^\s*(?:Figure|Fig\.?)\s*(\d+)\s*[:.]\s*(.*)$", re.I)
    next_re = re.compile(r"^\s*(?:Figure|Fig\.?)\s*\d+\s*[:.]", re.I)
    for index, line in enumerate(lines):
        match = label_re.match(line)
        if not match:
            continue
        parts = [match.group(2).strip()]
        for following in lines[index + 1:]:
            stripped = following.strip()
            if not stripped or next_re.match(stripped):
                break
            if stripped.startswith("#"):
                break
            parts.append(stripped)
        captions[match.group(1)] = " ".join(part for part in parts if part)
    return captions


def _FigureNumbers(text: str) -> list[int]:
    """Every ``Fig. N`` / ``Figure N`` number mentioned in a caption, in order."""
    return [
        int(value)
        for value in re.findall(r"(?:Figure|Fig\.?)\s*(\d+)", text or "", re.I)
    ]


#: A box narrower than this fraction of the page is treated as a column member.
_COLUMN_WIDTH_RATIO = 0.62


def _PageReadingOrder(page_items: list[dict]) -> list[dict]:
    """Order one page's visual blocks the way a reader scans them.

    Plain ``(top, left)`` sorting is wrong for a two-column layout: it interleaves
    the columns, so a figure spanning the top of both columns is separated from
    the crop directly beneath it. Measured on the EventCV paper's page 6, that
    split the two Figure 7 panels apart and pushed the second one four places
    down the list, which is how a note ended up with two ``fig7-*`` assets and no
    Figure 9.

    Ordering is therefore column-major: blocks are assigned to side-by-side
    columns by horizontal overlap, and each column is read top to bottom. A
    full-width block (a figure spanning both columns) is emitted where it starts
    and does not create a column of its own.

    Each returned block carries an ``order`` key holding its position, so the
    caller can restore this ordering after grouping.
    """
    if not page_items:
        return []

    boxes = [item["bbox"] for item in page_items]
    page_left = min(box[0] for box in boxes)
    page_right = max(box[2] for box in boxes)
    page_width = max(1.0, page_right - page_left)

    # Seed columns from blocks that are clearly narrower than the page, in
    # left-to-right order; each later block joins the column it overlaps most.
    columns: list[list[float]] = []
    for item in sorted(page_items, key=lambda entry: entry["bbox"][0]):
        box = item["bbox"]
        width = box[2] - box[0]
        if width >= _COLUMN_WIDTH_RATIO * page_width:
            continue  # full-width block: belongs to the page flow, not a column
        for column in columns:
            span = min(column[2], box[2]) - max(column[0], box[0])
            if span > 0.5 * min(column[2] - column[0], width):
                column[0] = min(column[0], box[0])
                column[1] = min(column[1], box[1])
                column[2] = max(column[2], box[2])
                column[3] = max(column[3], box[3])
                break
        else:
            columns.append(list(box))

    columns.sort(key=lambda column: column[0])

    def _column_index(box: list[float]) -> int:
        width = box[2] - box[0]
        if width >= _COLUMN_WIDTH_RATIO * page_width or len(columns) <= 1:
            return 0
        best, best_span = 0, -1.0
        for index, column in enumerate(columns):
            span = min(column[2], box[2]) - max(column[0], box[0])
            if span > best_span:
                best, best_span = index, span
        return best

    ordered = sorted(
        page_items,
        key=lambda item: (
            _column_index(item["bbox"]),
            item["bbox"][1],
            item["bbox"][0],
        ),
    )
    for position, item in enumerate(ordered):
        item["order"] = position
    return ordered


def _ExtractFigureGroups(
    manifest: dict | None,
    cache_dir: str,
    full_md_content: str = "",
) -> list[dict]:
    """Return independent visual blocks in reading order, including charts.

    Page coordinates are review evidence. Proximity and caption placement alone
    do not establish whether blocks share a printed figure identity.
    """
    items = []
    metadata = _FigureMetadata(manifest)
    captions = _ContentListCaptions(cache_dir)
    known_paths = set(metadata)

    for item in _ReadContentList(cache_dir):
        # Charts carry real figure captions; tables are handled by the note's
        # table flow and are not figure assets.
        if item.get("type") not in ("image", "chart"):
            continue
        path = str(item.get("img_path", "")).strip()
        bbox = item.get("bbox")
        page_idx = item.get("page_idx")
        if not path or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if not all(isinstance(value, (int, float)) for value in bbox):
            continue
        if not isinstance(page_idx, int) or page_idx < 0:
            continue
        # A chart is never in the manifest, so requiring manifest membership
        # would reintroduce the drop this change fixes.
        items.append({
            "path": path,
            "page_idx": page_idx,
            "bbox": [float(value) for value in bbox],
            "caption": captions.get(path, ""),
        })

    if not items:
        return []

    grouped_by_page: dict[int, list[dict]] = {}
    for item in items:
        grouped_by_page.setdefault(item["page_idx"], []).append(item)

    groups: list[list[dict]] = []
    for page_idx in sorted(grouped_by_page):
        page_items = _PageReadingOrder(grouped_by_page[page_idx])
        # Coordinate proximity cannot establish shared figure identity.
        # A reviewer may supply a PDF crop after inspecting the printed page.
        groups.extend([[item] for item in page_items])

    # Reading order across pages; within a page the grouper already walked
    # column-major, so the page index is all this has to make explicit.
    groups.sort(key=lambda group: (
        group[0]["page_idx"],
        min(item["order"] for item in group),
    ))

    result = []
    for group in groups:
        paths = [item["path"] for item in group]
        records = [metadata.get(path, {}) for path in paths]
        labels = [str(record.get("label", "")).strip() for record in records]
        captions = [str(record.get("caption", "")).strip() for record in records]
        # The content list is per-block and covers charts; the manifest is
        # per-section and sometimes thin. Their union loses nothing.
        block_captions = [
            str(item.get("caption", "")).strip() for item in group
            if item.get("caption")
        ]
        hints = []
        for record in records:
            hints.extend(record.get("caption_hints", []))

        formal_labels = [
            value
            for value in labels
            if re.search(r"^(?:fig(?:ure)?)[ ._-]?\d+", value, re.I)
        ]
        numbered_labels = [
            value
            for value in labels
            if re.search(r"(?:fig(?:ure)?|image)[ ._-]?\d+", value, re.I)
        ]
        label = (
            formal_labels[0]
            if formal_labels
            else numbered_labels[0]
            if numbered_labels
            else next((value for value in labels if value), f"Image {len(result) + 1}")
        )
        # A caption is one that starts with a figure label; the ``Fig. N.`` form
        # is as common as ``Figure N:`` and was previously rejected outright.
        formal_captions = [
            value
            for value in captions + block_captions
            if re.match(r"^(?:fig(?:ure)?)[ ._-]?\d+\s*[:.]", value, re.I)
        ]
        all_caption_parts = list(dict.fromkeys(
            value for value in captions + block_captions + hints if value
        ))
        caption = (
            formal_captions[0]
            if formal_captions
            else "; ".join(all_caption_parts)
        )
        bbox = [
            min(item["bbox"][0] for item in group),
            min(item["bbox"][1] for item in group),
            max(item["bbox"][2] for item in group),
            max(item["bbox"][3] for item in group),
        ]
        result.append({
            "source_paths": paths,
            "placements": group,
            "page_idx": group[0]["page_idx"],
            "bbox": bbox,
            "label": label,
            "caption": caption,
            "caption_short": caption[:80] if caption else "",
        })

    _RepairFigureNumbers(result, full_md_content)
    return result


def _RepairFigureNumbers(groups: list[dict], full_md_content: str) -> None:
    """Record caption candidates without guessing identities from reading order.

    Captions may drift in extraction. Repeated numbers can be panels of the
    same figure; appendix identifiers can restart or use letters. Only a
    hash-bound PDF review in figure-ledger.json confirms an identity.
    """
    for index, group in enumerate(groups, 1):
        match = re.match(r"^\s*(?:Figure|Fig\.?)\s*([A-Z]?(?:\.\d+|\d+)(?:\.\d+)?)\s*[:.]\s", group.get("caption", ""), re.I)
        candidate = match.group(1) if match else None
        group["figure_number"] = int(candidate) if candidate and candidate.isdigit() else candidate
        group["figure_number_source"] = "caption-candidate" if candidate else "unverified"
        group["label"] = f"Asset {index}"  # Never put an unverified Figure N into md_ref.



def _SemanticFilename(
    index: int,
    label: str,
    caption: str,
    ext: str,
    figure_number: int | None = None,
) -> str:
    """Generate a stable asset filename like asset-001-framework-overview.jpg.

    ``figure_number`` is retained in the figure ledger and never enters the
    filename. A MinerU block index is not a printed Figure number.
    """
    num = f"{index:03d}"

    # Extract key words from caption
    if caption:
        # Take first few meaningful words
        words = re.findall(r"[a-zA-Z][a-z]+", caption)
        # Filter common stop words
        stop = {"the", "and", "for", "with", "from", "this", "that", "are", "our", "its", "has"}
        words = [w.lower() for w in words if w.lower() not in stop][:kMaxCaptionWordsForName]
        suffix = "-".join(words) if words else "figure"
    else:
        suffix = "figure"

    return f"{kDefaultFigPrefix}{num}-{suffix}{ext}"


def _UniqueFilename(filename: str, used_filenames: set[str]) -> str:
    """Return a deterministic non-colliding asset filename."""
    if filename not in used_filenames:
        used_filenames.add(filename)
        return filename

    stem, ext = os.path.splitext(filename)
    suffix = 2
    candidate = f"{stem}-{suffix}{ext}"
    while candidate in used_filenames:
        suffix += 1
        candidate = f"{stem}-{suffix}{ext}"

    used_filenames.add(candidate)
    return candidate


def _Slugify(title: str) -> str:
    """Convert title to asset directory slug.

    A title with no ASCII alphanumerics (a Chinese title, or a symbol-only one)
    reduces to the empty string, which would place the assets directly in
    ``assets/`` and make two such papers collide on every filename. The note
    body is written in Chinese by default, so this is reachable; fall back to a
    stable digest of the title instead of an empty or shared directory.
    """
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    # Truncate if too long
    if len(slug) > 80:
        slug = slug[:80].rsplit("-", 1)[0]
    if not slug:
        digest = hashlib.sha1(title.strip().encode("utf-8")).hexdigest()[:10]
        slug = f"{kFallbackSlugPrefix}-{digest}"
    return slug


def _Error(msg: str) -> None:
    """Print error to stderr."""
    print(f"[ERROR] {msg}", file=sys.stderr)


def _ResolvePdfPath(item_key: str, api) -> str | None:
    """Resolve the local Zotero PDF path for a parent item or PDF item."""
    try:
        children = api.get_children(item_key)
    except Exception:
        try:
            item = api.get_item(item_key)
            children = [item] if item.get("data", {}).get("contentType") == "application/pdf" else []
        except Exception:
            children = []

    pdf_child = next(
        (
            child
            for child in children
            if child.get("data", {}).get("contentType") == "application/pdf"
        ),
        None,
    )
    if not pdf_child:
        try:
            candidate = api.get_item(item_key)
            pdf_child = candidate if candidate.get("data", {}).get("contentType") == "application/pdf" else None
        except Exception:
            pdf_child = None
    if not pdf_child:
        return None

    href = pdf_child.get("links", {}).get("enclosure", {}).get("href", "")
    if not href.startswith("file://"):
        return None

    pdf_path = urllib.parse.unquote(urllib.parse.urlparse(href).path)
    if os.name == "nt" and pdf_path.startswith("/"):
        pdf_path = pdf_path[1:]
    if os.path.isfile(pdf_path):
        return pdf_path

    # Keep compatibility with the two common Chinese/English home-folder
    # spellings seen in existing Zotero enclosure URLs.
    alternatives = []
    if "Docs" in pdf_path:
        alternatives.append(pdf_path.replace("Docs", "文档"))
    if "文档" in pdf_path:
        alternatives.append(pdf_path.replace("文档", "Docs"))
    return next((path for path in alternatives if os.path.isfile(path)), None)


def _RenderPdfPage(
    pdf_path: str,
    page_idx: int,
    render_dir: str,
    page_cache: dict[int, str],
) -> str | None:
    """Render one PDF page once and cache the generated page image."""
    cached = page_cache.get(page_idx)
    if cached and os.path.isfile(cached):
        return cached

    prefix = os.path.join(render_dir, f"page_{page_idx}")
    cmd = [
        "pdftoppm",
        "-f",
        str(page_idx + 1),
        "-l",
        str(page_idx + 1),
        "-png",
        "-r",
        str(kCompositeDpi),
        pdf_path,
        prefix,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None

    try:
        candidates = sorted(
            os.path.join(render_dir, name)
            for name in os.listdir(render_dir)
            if name.startswith(f"page_{page_idx}") and name.endswith(".png")
        )
    except OSError:
        return None
    if not candidates:
        return None

    page_cache[page_idx] = candidates[0]
    return candidates[0]


def _CropPdfFigure(
    page_path: str,
    bbox: list[float],
    dst_path: str,
) -> bool:
    """Crop a normalized page bbox, retaining nearby panel captions."""
    try:
        from PIL import Image

        with Image.open(page_path) as page:
            width, height = page.size
            x1 = max(0.0, bbox[0] - kCompositePaddingX) * width / 1000.0
            y1 = max(0.0, bbox[1] - kCompositePaddingTop) * height / 1000.0
            x2 = min(1000.0, bbox[2] + kCompositePaddingX) * width / 1000.0
            y2 = min(1000.0, bbox[3] + kCompositePaddingBottom) * height / 1000.0
            if x2 <= x1 or y2 <= y1:
                return False

            cropped = page.crop((x1, y1, x2, y2)).convert("RGB")
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            cropped.save(dst_path, "JPEG", quality=kFigureJpegQuality, optimize=True)
            return True
    except Exception:
        return False


def _ComposeFromCachedImages(
    fig: dict,
    source_files: list[str],
    dst_path: str,
) -> bool:
    """Fallback compositor when the original PDF cannot be rendered."""
    try:
        from PIL import Image, ImageOps

        placements = fig.get("placements", [])
        if not placements:
            # Legacy data has no coordinates; keep the fallback deterministic.
            images = [Image.open(path).convert("RGB") for path in source_files]
            gap = 8
            width = sum(image.width for image in images) + gap * (len(images) - 1)
            height = max(image.height for image in images)
            canvas = Image.new("RGB", (width, height), "white")
            x = 0
            for image in images:
                canvas.paste(image, (x, (height - image.height) // 2))
                x += image.width + gap
            canvas.save(dst_path, "JPEG", quality=kFigureJpegQuality, optimize=True)
            return True

        bbox = fig.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return False

        scale = 4.0
        padding = 8
        min_x, min_y = bbox[0], bbox[1]
        max_x, max_y = bbox[2], bbox[3]
        width = max(1, int((max_x - min_x) * scale) + 2 * padding)
        height = max(1, int((max_y - min_y) * scale) + 2 * padding)
        canvas = Image.new("RGB", (width, height), "white")

        for placement, source_file in zip(placements, source_files):
            item_bbox = placement.get("bbox")
            if not isinstance(item_bbox, list) or len(item_bbox) != 4:
                continue
            target_size = (
                max(1, int((item_bbox[2] - item_bbox[0]) * scale)),
                max(1, int((item_bbox[3] - item_bbox[1]) * scale)),
            )
            with Image.open(source_file) as source:
                image = ImageOps.contain(source.convert("RGB"), target_size)
            x = padding + int((item_bbox[0] - min_x) * scale)
            y = padding + int((item_bbox[1] - min_y) * scale)
            canvas.paste(image, (x, y))

        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        canvas.save(dst_path, "JPEG", quality=kFigureJpegQuality, optimize=True)
        return True
    except Exception:
        return False


def _ComposeFigureGroup(
    fig: dict,
    source_files: list[str],
    dst_path: str,
    pdf_path: str | None,
    render_dir: str,
    page_cache: dict[int, str],
) -> bool:
    """Create one composite asset for a grouped MinerU figure."""
    page_idx = fig.get("page_idx")
    bbox = fig.get("bbox")
    if pdf_path and isinstance(page_idx, int) and isinstance(bbox, list) and len(bbox) == 4:
        page_path = _RenderPdfPage(pdf_path, page_idx, render_dir, page_cache)
        if page_path and _CropPdfFigure(page_path, bbox, dst_path):
            return True

    return _ComposeFromCachedImages(fig, source_files, dst_path)


def _RestoreImageFromPDF(item_key: str, img_rel_path: str, cache_dir: str, api) -> bool:
    """Attempt to crop a missing figure/table image from the original Zotero PDF."""
    from PIL import Image

    # 1. Load content_list.json to find coordinates of this image path
    items = _ReadContentList(cache_dir)
    if not items:
        return False

    target_item = None
    for item in items:
        if item.get("type") in ("image", "table") and item.get("img_path") == img_rel_path:
            target_item = item
            break

    if not target_item:
        return False

    page_idx = target_item.get("page_idx")
    bbox = target_item.get("bbox")
    if page_idx is None or not bbox:
        return False

    # 2. Resolve the original local PDF.
    pdf_path = _ResolvePdfPath(item_key, api)
    if not pdf_path:
        return False

    # 3. Render page and crop
    tmp_dir = "/tmp/zotero_image_restore"
    os.makedirs(tmp_dir, exist_ok=True)
    page_num = page_idx + 1
    prefix = os.path.join(tmp_dir, f"page_{page_idx}")
    cmd = [
        "pdftoppm",
        "-f", str(page_num),
        "-l", str(page_num),
        "-png",
        "-r", "150",
        pdf_path,
        prefix
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        files = [f for f in os.listdir(tmp_dir) if f.startswith(f"page_{page_idx}")]
        if not files:
            return False
        page_image_path = os.path.join(tmp_dir, files[0])
        
        with Image.open(page_image_path) as img:
            W, H = img.size
            x1 = bbox[0] * W / 1000.0
            y1 = bbox[1] * H / 1000.0
            x2 = bbox[2] * W / 1000.0
            y2 = bbox[3] * H / 1000.0
            
            cropped = img.crop((x1, y1, x2, y2))
            
            dst_path = os.path.join(cache_dir, img_rel_path)
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            
            if dst_path.lower().endswith(".jpg") or dst_path.lower().endswith(".jpeg"):
                cropped.convert("RGB").save(dst_path, "JPEG", quality=kFigureJpegQuality)
            else:
                cropped.save(dst_path)
        
        os.remove(page_image_path)
        return True
    except Exception:
        return False

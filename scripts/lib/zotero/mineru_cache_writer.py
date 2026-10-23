#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write MinerU results as plugin-compatible Zotero caches.

Reverse-engineered from llm-for-zotero v3.9.7 (``mineruCache.ts``). The goal is
that a cache written here is indistinguishable from one the plugin wrote, so the
plugin reuses it instead of re-parsing (and so ``zotero_cli.py mineru read``
finds it).

The decisive fact that makes this possible
------------------------------------------
The plugin's only cache-presence gate is ``hasCachedMineruMd`` — it checks that
``full.md`` exists. It does **not** verify ``manifest.json``, ``_llm_source.json``,
hashes, or mtimes. So writing ``full.md`` (plus provenance) is sufficient.

Layout written::

    <data_dir>/llm-for-zotero-mineru/<attachment_id>/
        full.md              # canonical: local image embeds stripped
        content_list.json    # renamed from *_content_list.json, paths rewritten
        manifest.json        # generated (sections/figures/tables)
        _llm_source.json     # provenance, kind/version/attachmentId/...
        images/*             # flattened

Two fidelity details that are easy to get wrong:

1. The stored ``full.md`` has **local image embeds stripped** — the plugin keeps
   images on disk and references them through ``manifest.json``/``content_list``
   rather than inline markdown embeds.
2. Non-durable artifacts (``layout.json``, ``middle.json``, ``*_model.json``)
   are *pruned* by the plugin, so we never write them.

NOT responsible for: calling the MinerU API, resolving numeric IDs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass

from .mineru_client import MineruResult

__all__ = [
    "CacheWriteResult",
    "write_mineru_cache",
    "build_manifest",
    "normalize_files",
    "strip_local_image_embeds",
    "MINERU_CACHE_DIR_NAME",
]


MINERU_CACHE_DIR_NAME = "llm-for-zotero-mineru"
SOURCE_PROVENANCE_FILE = "_llm_source.json"
SOURCE_PROVENANCE_KIND = "llm-for-zotero/mineru-cache-source"
SOURCE_PROVENANCE_VERSION = 2

FULL_MD = "full.md"
MANIFEST = "manifest.json"
CONTENT_LIST = "content_list.json"

# Durable artifacts only (mineruCache.ts:678-713).
_DURABLE_ROOT_FILES = {FULL_MD, MANIFEST, CONTENT_LIST, SOURCE_PROVENANCE_FILE}
_DURABLE_DIRS = ("images", "figure_crops")

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_HTML_IMG_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)
_EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|#)", re.I)

# Publisher/metadata headings the plugin drops from the manifest
# (mineruCache.ts:1317-1354).
_NOISE_HEADING_RE = re.compile(
    r"^(?:nature|science|cell|article|authors?|affiliations?|highlights?|"
    r"graphical abstract|abstract|keywords?|references|bibliography|"
    r"acknowledg(?:e)?ments?|supplementary|appendix)\b",
    re.I,
)


@dataclass
class CacheWriteResult:
    """What was written, and enough context to report or verify it."""

    cache_dir: str
    cache_id: int
    attachment_key: str | None
    parent_item_key: str | None
    markdown_chars: int
    section_count: int
    figure_count: int
    image_count: int
    written_files: list[str]

    def to_dict(self) -> dict:
        return {
            "cache_dir": self.cache_dir,
            "cache_id": self.cache_id,
            "attachment_key": self.attachment_key,
            "parent_item_key": self.parent_item_key,
            "markdown_chars": self.markdown_chars,
            "section_count": self.section_count,
            "figure_count": self.figure_count,
            "image_count": self.image_count,
            "written_files": sorted(self.written_files),
        }


# -- normalization -------------------------------------------------------


def strip_local_image_embeds(markdown: str) -> str:
    """Remove local image references, keeping external URLs and anchors.

    The plugin stores ``full.md`` without local embeds; images are resolved via
    the manifest instead. External links (http/https) and anchors are preserved.
    """

    def _keep_external(match: re.Match[str]) -> str:
        target = match.group(1)
        return match.group(0) if _EXTERNAL_RE.match(target) else ""

    text = _MD_IMAGE_RE.sub(_keep_external, markdown)
    text = _HTML_IMG_RE.sub(
        lambda m: m.group(0) if _EXTERNAL_RE.match(m.group(1)) else "", text
    )
    # Drop lines that became empty, then collapse the resulting blank runs.
    lines = [line for line in text.split("\n") if line.strip() or line == ""]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


def _shorten_segment(segment: str, source: str) -> str:
    """Shorten one path segment past 80 chars, appending a stable hash."""
    if len(segment) <= 80:
        return segment
    stem, ext = os.path.splitext(segment)
    digest = _fnv1a32(source)
    keep = max(12, 80 - len(ext) - len(digest) - 1)
    return f"{stem[:keep]}-{digest}{ext}"


def _fnv1a32(value: str) -> str:
    """FNV-1a 32-bit hash, matching the plugin's ``stableHash``."""
    hashed = 0x811C9DC5
    for char in value:
        hashed ^= ord(char)
        hashed = (hashed * 0x01000193) & 0xFFFFFFFF
    return f"{hashed:08x}"


def normalize_files(
    files: dict[str, bytes], markdown_name: str
) -> tuple[dict[str, bytes], str]:
    """Flatten a MinerU ZIP into the plugin's on-disk layout.

    Returns ``(normalized_files, canonical_markdown)`` where keys are cache-
    relative paths (``full.md``, ``images/x.png``, ...).
    """
    md_parts = markdown_name.replace("\\", "/").split("/")
    md_dir = md_parts[:-1]

    normalized: dict[str, bytes] = {}
    content_list_bytes: bytes | None = None
    markdown_text = _as_bytes(files[markdown_name]).decode("utf-8", errors="replace")

    for name, data in files.items():
        safe = _safe_parts(name)
        if safe is None:
            continue
        parts = safe

        basename = parts[-1]
        if basename == os.path.basename(markdown_name):
            continue  # written separately as full.md
        if basename.lower().endswith(".md") or basename.lower().endswith(".pdf"):
            continue

        if basename == "content_list.json" or basename.endswith("_content_list.json"):
            content_list_bytes = _as_bytes(data)
            continue
        if basename == "manifest.json":
            continue  # we generate our own

        stripped = _strip_container(parts, md_dir)
        if not stripped:
            continue

        target_parts = stripped
        if len(stripped) > 1 and stripped[0].lower() in (
            "images",
            "image",
            "imgs",
            "figures",
            "tables",
        ):
            target_parts = [stripped[0].lower(), os.path.basename(stripped[-1])]
        elif len(stripped) > 1 and "/".join(stripped) != "/".join(parts):
            # Nested container collapsed to a flat basename.
            target_parts = [os.path.basename(stripped[-1])]

        target = "/".join(_shorten_segment(p, name) for p in target_parts)
        if not _is_durable(target):
            continue
        normalized[target] = _as_bytes(data)

    if content_list_bytes is not None:
        normalized[CONTENT_LIST] = content_list_bytes

    normalized[FULL_MD] = strip_local_image_embeds(markdown_text).encode("utf-8")
    return normalized, markdown_text

def _as_bytes(value: bytes | str) -> bytes:
    """Accept either bytes or text for file payloads.

    ``MineruResult.files`` is bytes in production, but accepting ``str`` keeps
    the normalizer usable from tests and ad-hoc callers without a decode dance.
    """
    return value.encode("utf-8") if isinstance(value, str) else value


def _safe_parts(name: str) -> list[str] | None:
    """Reject absolute, UNC, drive-letter, or dot-dot paths."""
    if not name:
        return None
    if re.match(r"^(?:[A-Za-z]:|[\\/]{2}|[\\/])", name):
        return None
    parts = [p for p in re.split(r"[\\/]+", name) if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None
    return parts


def _strip_container(parts: list[str], md_dir: list[str]) -> list[str]:
    """Drop the markdown's enclosing directory and a leading ``auto``."""
    stripped = list(parts)
    if md_dir and stripped[: len(md_dir)] == md_dir:
        stripped = stripped[len(md_dir) :]
    elif md_dir and stripped and stripped[0] == md_dir[0]:
        stripped = stripped[1:]
    if len(stripped) > 1 and stripped[0].lower() == "auto":
        stripped = stripped[1:]
    return stripped


def _is_durable(target: str) -> bool:
    parts = target.split("/")
    first = parts[0]
    if first == "__MACOSX" or parts[-1] == ".DS_Store":
        return False
    if len(parts) == 1:
        return first in _DURABLE_ROOT_FILES
    return first in _DURABLE_DIRS


# -- manifest ------------------------------------------------------------


def build_manifest(markdown: str, content_list: list | None) -> dict:
    """Generate ``manifest.json`` from markdown headings + content_list.

    Mirrors ``buildManifest`` (mineruCache.ts:1363-1542). Notably: with two or
    fewer sections the plugin emits ``noSections: true`` and empty figure/table
    lists, because such a manifest is not useful for section-aware chunking.
    """
    headings: list[tuple[str, int]] = []
    for match in re.finditer(r"(?m)^#\s+(.+)$", markdown):
        heading = match.group(1).strip()
        if len(heading) < 3 or _NOISE_HEADING_RE.match(heading):
            continue
        headings.append((heading, match.start()))

    if len(headings) <= 2:
        return {
            "sections": [],
            "allFigures": [],
            "allTables": [],
            "totalChars": len(markdown),
            "noSections": True,
        }

    sections = []
    for index, (heading, start) in enumerate(headings):
        end = headings[index + 1][1] if index + 1 < len(headings) else len(markdown)
        sections.append(
            {
                "heading": heading,
                "charStart": start,
                "charEnd": end,
                "figures": [],
                "tables": [],
                "equationCount": 0,
            }
        )

    all_figures: list[dict] = []
    all_tables: list[dict] = []
    total_pages = 0
    figure_counter = 0
    table_counter = 0

    for entry in content_list or []:
        if not isinstance(entry, dict):
            continue
        page_idx = entry.get("page_idx")
        if isinstance(page_idx, int):
            total_pages = max(total_pages, page_idx + 1)

        entry_type = entry.get("type")
        if entry_type == "image":
            figure_counter += 1
            caption = " ".join(entry.get("image_caption") or []) or ""
            all_figures.append(
                {
                    "label": _figure_label(caption, f"image-{figure_counter}"),
                    "baseLabel": "",
                    "path": entry.get("img_path") or "",
                    "caption": caption[:300],
                }
            )
        elif entry_type == "table":
            table_counter += 1
            caption = (
                " ".join(entry.get("table_caption") or [])
                or " ".join(entry.get("table_footnote") or [])
                or ""
            )
            all_tables.append(
                {
                    "label": _figure_label(caption, f"table-{table_counter}"),
                    "baseLabel": "",
                    "path": entry.get("table_path") or entry.get("img_path") or "",
                    "caption": caption[:300],
                }
            )
        elif entry_type == "equation":
            if sections:
                sections[-1]["equationCount"] += 1

    return {
        "sections": sections,
        "allFigures": all_figures,
        "allTables": all_tables,
        "totalPages": total_pages or None,
        "totalChars": len(markdown),
    }


def _figure_label(caption: str, fallback: str) -> str:
    match = re.match(r"^\s*((?:Figure|Fig\.?|Table)\s*[\w.\-]+)", caption, re.I)
    return match.group(1).strip() if match else fallback


# -- writing -------------------------------------------------------------


def write_mineru_cache(
    *,
    cache_root: str,
    attachment_id: int,
    result: MineruResult,
    markdown_name: str,
    attachment_key: str | None = None,
    parent_item_key: str | None = None,
    source_filename: str | None = None,
    overwrite: bool = False,
) -> CacheWriteResult:
    """Write a plugin-compatible cache directory.

    ``overwrite=False`` refuses to touch an existing ``full.md`` so an
    accidental re-parse cannot destroy a good cache.
    """
    cache_dir = os.path.join(cache_root, str(int(attachment_id)))
    full_md_path = os.path.join(cache_dir, FULL_MD)

    if os.path.isfile(full_md_path) and not overwrite:
        raise FileExistsError(
            f"MinerU cache already exists at {cache_dir} (use --overwrite)"
        )

    normalized, canonical_md = normalize_files(result.files, markdown_name)

    if os.path.isdir(cache_dir) and overwrite:
        # Remove only non-durable leftovers; keep the directory itself.
        for entry in os.listdir(cache_dir):
            path = os.path.join(cache_dir, entry)
            if entry not in _DURABLE_ROOT_FILES and entry not in _DURABLE_DIRS:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    os.makedirs(cache_dir, exist_ok=True)

    written: list[str] = []
    for relative, data in normalized.items():
        target = os.path.join(cache_dir, relative.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        written.append(relative)

    manifest = build_manifest(canonical_md, result.content_list)
    with open(os.path.join(cache_dir, MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False)
    written.append(MANIFEST)

    now = _iso_now()
    provenance = {
        "kind": SOURCE_PROVENANCE_KIND,
        "version": SOURCE_PROVENANCE_VERSION,
        "attachmentId": int(attachment_id),
        "origin": "parsed",
        "recordedAt": now,
        "parsedAt": now,
    }
    if attachment_key:
        provenance["attachmentKey"] = attachment_key.upper()
    if parent_item_key:
        provenance["parentItemKey"] = parent_item_key.upper()
    if source_filename:
        provenance["sourceFilename"] = source_filename
    with open(
        os.path.join(cache_dir, SOURCE_PROVENANCE_FILE), "w", encoding="utf-8"
    ) as handle:
        json.dump(provenance, handle, ensure_ascii=False, indent=2)
    written.append(SOURCE_PROVENANCE_FILE)

    image_count = sum(1 for name in normalized if name.startswith("images/"))
    # Report the canonical size actually written, not the raw MinerU markdown
    # length: image embeds are stripped, so the two differ.
    written_md_chars = len(normalized.get(FULL_MD, b"").decode("utf-8", "replace"))
    return CacheWriteResult(
        cache_dir=cache_dir,
        cache_id=int(attachment_id),
        attachment_key=attachment_key.upper() if attachment_key else None,
        parent_item_key=parent_item_key.upper() if parent_item_key else None,
        markdown_chars=written_md_chars,
        section_count=len(manifest.get("sections") or []),
        figure_count=len(manifest.get("allFigures") or []),
        image_count=image_count,
        written_files=written,
    )


def _iso_now() -> str:
    import datetime

    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )

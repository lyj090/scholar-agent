#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_import.py — Import papers into Zotero.

Supports multiple identifier types:
  - arXiv ID: 2505.12345
  - arXiv URL: https://arxiv.org/abs/2505.12345
  - DOI: 10.1109/tpami.2020.3008413
  - Title search: --title "Event-Based Vision"

Fetches metadata itself (arXiv Atom API / Crossref) and writes through the
Zotero 10+ native write API, so importing no longer needs the llm-for-zotero
plugin or an in-process translator.

Design notes:

  - Metadata is fetched *before* anything is written, so a bad identifier fails
    without leaving a half-created item behind.
  - Items land in the target collection via ``local_write``, which keeps this
    path identical to every other write in the repo (one code path, one set of
    safety guarantees).

NOT responsible for: discovery/ranking (cmd_find), note generation.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

from ..client import ArxivClient
from zotero.api import ZoteroAPIError, normalize_key
from zotero.config import load_config
from zotero.local_write import (
    ZoteroAuthRequired,
    ZoteroWriteError,
    build_write_client,
)
from zotero.metadata_fetch import MetadataError, fetch as fetch_metadata
from arxiv.cache import note, set_quiet

# ─── Constants ─────────────────────────────────────────────────────────────────────────────────────────────────────────

kImportDelay = 2.0  # seconds between imports


def _JsLiteral(value: str) -> str:
    """Encode a value as a JSON string literal (kept for callers that embed it)."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


# ─── Public ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'import' subcommand."""
    # Note: We can't easily load config here to set the default, 
    # so we'll handle the default resolution inside _Run.
    p = subparsers.add_parser(
        "import",
        help="Import papers into Zotero (by arXiv ID, URL, DOI, or title)",
    )
    p.add_argument(
        "targets", nargs="*",
        help="arXiv IDs, DOIs, URLs, or comma-separated (e.g. 2505.12345,10.1109/xxx)",
    )
    p.add_argument("--title", "-t", help="Search arXiv by title and import first match")
    p.add_argument("--doi", "-d", nargs="*", help="DOIs to import (e.g. 10.1109/tpami.2020.3008413)")
    p.add_argument(
        "--collection", "-c",
        help="Target collection key (defaults to zotero.inbox_collection_key in paths.local.yaml)",
    )
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p.add_argument("--json", action="store_true", help="Output JSON results")
    p.add_argument(
        "--no-pdf",
        action="store_true",
        help="Import metadata only; do not download and attach the PDF",
    )
    p.add_argument(
        "--pdf-dir",
        help="Directory for downloaded PDFs (default: <repo>/data/pdf-cache)",
    )
    p.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress lines. Errors still print.",
    )
    p.set_defaults(func=_Run)


def _Run(args, config) -> int:
    """Execute the import command."""
    set_quiet(getattr(args, "quiet", False))
    # Resolve all targets to importable items
    items = _ResolveTargets(args)
    if not items:
        print("[ERROR] No valid targets resolved.", file=sys.stderr)
        note("  Usage: import <arXiv-ID|DOI|URL> or --title '...' or --doi '...'")
        return 1

    # Resolve collection from the explicit argument or machine-local config.
    raw_collection_key = args.collection or getattr(config, "inbox_collection_key", "")
    if not raw_collection_key:
        print(
            "[ERROR] No target collection configured. Use --collection or set "
            "zotero.inbox_collection_key in shared/paths.local.yaml.",
            file=sys.stderr,
        )
        return 2
    try:
        collection_key = normalize_key(raw_collection_key, "target collection key")
    except ZoteroAPIError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 2

    # Show what will be imported
    note(f"[import] Will import {len(items)} paper(s) to collection {collection_key}:")
    for i, item in enumerate(items, 1):
        label = item.get("title") or item["identifier"]
        note(f"  {i}. [{item['type']}] {label}")

    # Confirm unless --yes
    if not args.yes:
        try:
            answer = input("\n确认导入? [Y/n] ").strip().lower()
            if answer and answer not in ("y", "yes", "是"):
                note("[import] Cancelled.")
                return 0
        except (EOFError, KeyboardInterrupt):
            note("\n[import] Cancelled.")
            return 0

    # Import each paper
    results = []
    imported_ids = []  # Track successful imports for recommended.json cleanup
    
    for i, item in enumerate(items, 1):
        label = item.get("title") or item["identifier"]
        note(f"\n[{i}/{len(items)}] Importing ({item['type']}) {item['identifier']}...")
        
        success, msg = _ImportOneItem(
            item,
            collection_key,
            fetch_pdf=not getattr(args, "no_pdf", False),
            pdf_dir=getattr(args, "pdf_dir", None),
        )
        results.append({
            "identifier": item["identifier"],
            "type": item["type"],
            "title": item.get("title", ""),
            "success": success,
            "msg": msg,
        })
        
        if success:
            note(f"[{i}/{len(items)}] ✓ {label}")
            # Track arXiv IDs for recommended.json cleanup
            if item["type"] == "arxiv":
                # Clean arXiv ID (remove version suffix)
                arxiv_id = item["identifier"]
                if "v" in arxiv_id:
                    arxiv_id = arxiv_id.split("v")[0]
                imported_ids.append(arxiv_id)
        else:
            note(f"[{i}/{len(items)}] ✗ {label}: {msg}")

        # Delay between imports
        if i < len(items):
            time.sleep(kImportDelay)

    # Clean up recommended.json if papers were imported successfully
    if imported_ids:
        from ..store import JsonStore
        store = JsonStore("data/recommended.json")
        if store.Load():  # Load existing data
            removed = store.RemovePapers(imported_ids)
            if removed:
                note(f"[import] Removed {len(imported_ids)} papers from recommended.json")

    # Summary
    n_ok = sum(1 for r in results if r["success"])
    n_fail = len(results) - n_ok
    note(f"\n[import] Done: {n_ok} succeeded, {n_fail} failed.")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    return 0 if n_fail == 0 else 1


# ─── Target Resolution ─────────────────────────────────────────────────────────────────────────────────────────────────


def _ResolveTargets(args) -> list[dict]:
    """Resolve all inputs to a list of importable items.

    Each item: {"type": "arxiv"|"doi", "identifier": "...", "title": "..."}
    """
    items: list[dict] = []

    # Mode 1: --title (arXiv search)
    if args.title:
        resolved = _ResolveTitleSearch(args.title, args.yes)
        if resolved:
            items.append(resolved)
        return items

    # Mode 2: --doi explicit
    if args.doi:
        for doi in args.doi:
            for d in doi.split(","):
                d = d.strip()
                if d:
                    items.append({"type": "doi", "identifier": d, "title": ""})
        return items

    # Mode 3: positional targets (auto-detect type)
    raw_targets: list[str] = []
    for t in (args.targets or []):
        raw_targets.extend(part.strip() for part in t.split(",") if part.strip())

    if not raw_targets:
        return []

    for target in raw_targets:
        item = _ClassifyTarget(target)
        if item:
            items.append(item)
        else:
            print(f"[WARN] Cannot parse target: {target}", file=sys.stderr)

    # Fetch titles for display (best-effort)
    arxiv_items = [it for it in items if it["type"] == "arxiv" and not it.get("title")]
    if arxiv_items:
        _EnrichArxivTitles(arxiv_items)

    return items


def _ClassifyTarget(target: str) -> dict | None:
    """Classify a target string into {type, identifier}."""
    # arXiv URL
    url_match = re.match(
        r"https?://arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)",
        target,
    )
    if url_match:
        return {"type": "arxiv", "identifier": _CleanArxivId(url_match.group(1)), "title": ""}

    # Plain arXiv ID: 2505.12345
    arxiv_match = re.match(r"^([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)$", target)
    if arxiv_match:
        return {"type": "arxiv", "identifier": _CleanArxivId(arxiv_match.group(1)), "title": ""}

    # Old-style arXiv: hep-ph/0512345
    old_match = re.match(r"^([a-z-]+/[0-9]{7})$", target)
    if old_match:
        return {"type": "arxiv", "identifier": old_match.group(1), "title": ""}

    # DOI: starts with 10.xxxx/
    doi_match = re.match(r"^(10\.\d{4,}/\S+)$", target)
    if doi_match:
        return {"type": "doi", "identifier": doi_match.group(1), "title": ""}

    # DOI URL: https://doi.org/10.xxxx/...
    doi_url = re.match(r"https?://doi\.org/(10\.\d{4,}/\S+)", target)
    if doi_url:
        return {"type": "doi", "identifier": doi_url.group(1), "title": ""}

    return None


def _ResolveTitleSearch(title: str, auto_select: bool) -> dict | None:
    """Search arXiv by title, let user pick, return item."""
    client = ArxivClient()
    note(f"[import] Searching arXiv for: {title}")
    try:
        papers = client.Search(title, max_results=5)
    except RuntimeError as e:
        print(f"[ERROR] arXiv search failed: {e}", file=sys.stderr)
        note("  Hint: Try --doi or direct arXiv ID instead.")
        return None

    if not papers:
        print("[ERROR] No papers found.", file=sys.stderr)
        return None

    note(f"[import] Found {len(papers)} match(es):")
    for i, p in enumerate(papers[:5], 1):
        note(f"  {i}. [{p.arxiv_id}] {p.title}")

    if not auto_select:
        try:
            choice = input("\n选择 (编号, 默认=1): ").strip()
            idx = int(choice) - 1 if choice else 0
        except (ValueError, EOFError, KeyboardInterrupt):
            idx = 0
    else:
        idx = 0

    if 0 <= idx < len(papers):
        p = papers[idx]
        return {"type": "arxiv", "identifier": _CleanArxivId(p.arxiv_id), "title": p.title}
    return None


def _EnrichArxivTitles(items: list[dict]) -> None:
    """Fetch titles from arXiv API for display (best-effort)."""
    try:
        client = ArxivClient()
        ids = [it["identifier"] for it in items]
        papers = client.GetById(ids)
        id_to_title = {_CleanArxivId(p.arxiv_id): p.title for p in papers}
        for it in items:
            it["title"] = id_to_title.get(it["identifier"], "")
    except Exception as e:
        print(f"[WARN] Cannot fetch titles: {e}", file=sys.stderr)


def _CleanArxivId(arxiv_id: str) -> str:
    """Remove version suffix."""
    return re.sub(r"v\d+$", "", arxiv_id)


# ─── Import Logic ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _ExistingEntry(config, item: dict):
    """Return the library entry already matching this identifier, if any.

    Import is the one place duplicates enter the library, and the arXiv ID /
    DOI is the strongest key available — so it is checked before creating
    anything. A lookup failure returns None (import proceeds): refusing to
    import because the index hiccuped would be worse than a possible duplicate.

    ``build_library_index`` reuses its own on-disk cache, so the first call
    scans and later ones are cheap.
    """
    try:
        from zotero.library_index import build_library_index

        index = build_library_index(config)
        if item.get("type") == "arxiv":
            return index.by_arxiv(item["identifier"])
        return index.by_doi(item["identifier"])
    except Exception:
        return None


def _ImportOneItem(
    item: dict,
    collection_key: str,
    *,
    fetch_pdf: bool = True,
    pdf_dir: str | None = None,
) -> tuple[bool, str]:
    """Fetch metadata, create the item in Zotero, and attach its PDF.

    Metadata and the PDF are treated as separate concerns on purpose. An import
    that produces a correct item is a success even when the PDF cannot be
    fetched (arXiv rate limits, a paper with no PDF, an offline machine); the
    item is then ``IN_LIBRARY_NO_PDF`` and ``mineru-parse`` can fill the gap. The
    reverse — refusing the whole import because the PDF failed — would throw away
    metadata that was already fetched and validated.

    That separation is the fix for a real defect: this path created the item and
    stopped, so every imported paper landed with ``numChildren: 0`` and had to be
    completed by hand. A PDF that never arrives is now reported, not silently
    missing.
    """
    try:
        collection_key = normalize_key(collection_key, "target collection key")
    except ZoteroAPIError as error:
        return False, str(error)

    kind = item.get("type")
    if kind not in ("arxiv", "doi"):
        return False, f"unknown type: {kind}"

    # Fetch first: a bad identifier must fail before anything is written.
    try:
        payload = fetch_metadata(item["identifier"], kind)
    except MetadataError as error:
        return False, f"metadata fetch failed: {error}"

    try:
        config = load_config()
        client = build_write_client(config)
    except ZoteroWriteError as error:
        return False, str(error)

    existing = _ExistingEntry(config, item)
    if existing is not None:
        # Still file it: the user asked for this paper to be in this collection,
        # and it may currently live somewhere else (or nowhere). An existing item
        # with no PDF is worth completing here too — that is the common state
        # left behind by imports made before this path attached PDFs at all.
        try:
            client.add_to_collection(existing.item_key, collection_key)
        except ZoteroWriteError:
            pass
        note = f"ALREADY PRESENT ({existing.item_key})"
        if fetch_pdf and not getattr(existing, "has_pdf", False):
            pdf_note = _AttachPdf(
                client,
                existing.item_key,
                item,
                config,
                pdf_dir,
                payload.get("creators"),
                payload.get("date", ""),
                payload.get("title", ""),
            )
            return True, f"{note} - {pdf_note}"
        return True, f"{note} - filed into collection"

    try:
        keys = client.create_items([payload])
        if not keys:
            return False, "Zotero did not return a new item key"
        key = keys[0]
        try:
            client.add_to_collection(key, collection_key)
        except ZoteroWriteError as error:
            # The item exists but is not filed: report that precisely instead of
            # pretending the import failed, which would invite a duplicate retry.
            return False, f"created {key} but could not file it: {error}"

        summary = f"OK: {payload.get('title', '')} ({key})"
        if not fetch_pdf:
            return True, f"{summary} [no PDF requested]"

        pdf_note = _AttachPdf(
            client,
            key,
            item,
            config,
            pdf_dir,
            payload.get("creators"),
            payload.get("date", ""),
            payload.get("title", ""),
        )
        # The identifier index described the library before this attachment
        # existed, so a later paper-status would still answer from the old view.
        try:
            from zotero.library_index import invalidate_library_index

            invalidate_library_index()
        except Exception:
            pass
        return True, f"{summary} - {pdf_note}"
    except ZoteroAuthRequired as error:
        return False, str(error)
    except ZoteroWriteError as error:
        return False, str(error)


#: arXiv's PDF endpoint, keyed by the bare identifier. A versioned ID resolves
#: to that exact version; the bare ID resolves to the latest.
_ARXIV_PDF_URL = "https://arxiv.org/pdf/{identifier}"


def _DefaultPdfDir() -> str:
    """Where downloaded PDFs are staged: ``<repo>/data/pdf-cache``.

    Counted anchors, not guessed: this file is
    ``<repo>/scripts/lib/arxiv/commands/cmd_import.py``, so four levels up from
    the module is ``<repo>/scripts`` and five is the repo root. Four was used
    first and silently created ``scripts/data/`` — a stray tree inside the source
    directory, which is why the path is computed in one named place and asserted
    by a test rather than inlined.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ))))
    return os.path.join(repo_root, "data", "pdf-cache")


def _DownloadPdf(identifier: str, kind: str, output_dir: str) -> str:
    """Download a paper's PDF and return its path.

    Raises :class:`ZoteroWriteError` with a readable reason on any failure, so
    the caller can report "imported, PDF unavailable: <why>" in one line.
    """
    if kind != "arxiv":
        # DOI resolution to an open-access PDF is publisher-specific and often
        # paywalled; guessing a URL here would produce a 200-with-HTML page that
        # looks like a PDF. Left to the user, who knows whether they have access.
        raise ZoteroWriteError("PDF auto-download is only supported for arXiv")

    url = _ARXIV_PDF_URL.format(identifier=identifier)
    os.makedirs(output_dir, exist_ok=True)
    safe = identifier.replace("/", "_").replace(":", "_")
    target = os.path.join(output_dir, f"{safe}.pdf")

    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target

    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url, headers={"User-Agent": "scholar-agent/1.0 (local literature tool)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            content_type = str(response.headers.get("Content-Type") or "")
    except urllib.error.HTTPError as error:
        raise ZoteroWriteError(f"HTTP {error.code} fetching {url}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ZoteroWriteError(f"cannot reach {url}: {error}") from error

    # arXiv serves an HTML error page with status 200 for a bad ID. Trusting the
    # extension alone would upload that page as the paper.
    if not body.startswith(b"%PDF"):
        raise ZoteroWriteError(
            f"{url} did not return a PDF (Content-Type: {content_type or 'unknown'})"
        )

    with open(target, "wb") as handle:
        handle.write(body)
    return target


def _AttachPdf(
    client,
    item_key: str,
    item: dict,
    config,
    pdf_dir: str | None,
    creators: list[dict] | None = None,
    date: str = "",
    title: str = "",
) -> str:
    """Download and attach a PDF; return a short status string. Never raises.

    A failure here must not fail the import — see :func:`_ImportOneItem`.

    ``title`` is the *fetched* title rather than ``item['title']``: a positional
    target is only enriched with a title on a best-effort basis (arXiv may be
    rate-limited), while the freshly fetched metadata always has the real one.
    """
    identifier = item.get("identifier") or ""
    kind = item.get("type") or ""
    resolved_title = title or item.get("title") or ""

    directory = pdf_dir or _DefaultPdfDir()
    try:
        path = _DownloadPdf(identifier, kind, directory)
    except ZoteroWriteError as error:
        return f"PDF NOT ATTACHED ({error})"

    filename = _PdfFilename(resolved_title, creators or [], date, identifier)
    try:
        attachment_key = client.create_attachment(
            item_key, title=f"{identifier} PDF", filename=filename
        )
        client.upload_file(attachment_key, path)
    except ZoteroWriteError as error:
        return f"PDF NOT ATTACHED ({error})"
    return f"PDF attached ({attachment_key})"


def _PdfFilename(
    title: str,
    creators: list[dict] | None = None,
    date: str = "",
    identifier: str = "",
) -> str:
    """Build a human-readable ``Author - Year - Title.pdf`` name.

    Zotero's own attachment naming convention is ``Author - Year - Title``, and
    the uploaded file keeps whatever name it is given, so this is what the reader
    sees in the file pane. A bare arXiv ID would make the library unreadable.
    Characters that break filesystems or link round-trips are stripped, and the
    result is capped so a very long title cannot exceed filesystem limits.
    """
    first_author = ""
    if creators:
        first = creators[0]
        if isinstance(first, dict):
            first_author = str(first.get("lastName") or first.get("name") or "").strip()
        else:
            first_author = str(first).strip()
        first_author = first_author.split()[-1] if first_author else ""

    year = ""
    match = re.match(r"(\d{4})", str(date or ""))
    if match:
        year = match.group(1)

    clean_title = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", str(title or "")).strip()
    clean_title = re.sub(r"\s+", " ", clean_title)
    if len(clean_title) > 120:
        clean_title = clean_title[:120].rsplit(" ", 1)[0]

    parts = [part for part in (first_author, year, clean_title) if part]
    stem = " - ".join(parts) if parts else str(identifier)
    return f"{stem}.pdf"


# ─── Helpers ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def _FindScript(name: str) -> str | None:
    """Find a script under scripts/literature/ relative to this module."""
    scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    path = os.path.join(scripts_dir, "literature", name)
    return path if os.path.isfile(path) else None

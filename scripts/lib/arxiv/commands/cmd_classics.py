#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_classics.py — Search classic high-citation papers via OpenAlex API.

Finds influential papers in a research field, sorted by citation count.
Uses OpenAlex (free, no API key required) for citation data.

NOT responsible for: daily discovery (cmd_daily), Zotero import (cmd_import).
"""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from zotero.api import ZoteroAPIError, normalize_key

# ─── Constants ─────────────────────────────────────────────────────────────────────────────────────────────────────────

kOpenAlexBase = "https://api.openalex.org/works"
kDefaultMinCite = 50
kDefaultTopN = 10
kRequestTimeout = 20
kPoliteEmail = "scholar-agent@localhost"  # OpenAlex polite pool

# OpenAlex concept IDs for field filtering (API-level, not post-fetch)
kFieldConcepts = {
    "computer science": "C154945302",
    "cs": "C154945302",
    "robotics": "C34413123",
    "robot": "C90509273",
    "computer vision": "C31972630",
    "cv": "C31972630",
    "reinforcement learning": "C97541855",
    "rl": "C97541855",
    "machine learning": "C119857082",
    "ml": "C119857082",
    "artificial intelligence": "C154945302",
    "ai": "C154945302",
    "control": "C65401140",
    "manipulation": "C2985527887",
    "locomotion": "C6101204",
    "humanoid": "C60692881",
    "tactile": "C34413123",
    "soft robotics": "C2776058767",
    "planning": "C34413123",
    "slam": "C31972630",
    "engineering": "C127413603",
}


# ─── Public ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'classics' subcommand."""
    p = subparsers.add_parser(
        "classics",
        help="Search classic high-citation papers (via OpenAlex)",
    )
    p.add_argument("query", help="Search keywords (e.g. 'dexterous manipulation')")
    p.add_argument("--top", "-n", type=int, default=kDefaultTopN, help=f"Top N results (default: {kDefaultTopN})")
    p.add_argument(
        "--min-cite", type=int, default=kDefaultMinCite,
        help=f"Min citation count (default: {kDefaultMinCite})",
    )
    p.add_argument("--year", help="Year range: '2018-2024' or '2020' for single year")
    p.add_argument("--field", default="Computer Science", help="Field of study (default: Computer Science)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--import", dest="do_import", action="store_true", help="Import results to Zotero after display")
    p.set_defaults(func=_Run)


def _Run(args, config) -> int:
    """Execute the classics command."""
    print(f"[classics] Searching: \"{args.query}\" (min {args.min_cite} citations)...", file=sys.stderr)

    # Build query
    papers = _SearchOpenAlex(
        query=args.query,
        min_cite=args.min_cite,
        year_range=args.year,
        field=args.field,
        limit=args.top,
    )

    if not papers:
        print("[classics] No papers found matching criteria.", file=sys.stderr)
        print("  Hint: Try lowering --min-cite or broadening your query.", file=sys.stderr)
        return 0

    print(f"[classics] Found {len(papers)} classic papers.", file=sys.stderr)

    # Output
    if args.json:
        _PrintJson(papers)
    else:
        _PrintTable(papers)

    # Optional import
    if args.do_import and papers:
        return _ImportPapers(papers, config)

    return 0


# ─── OpenAlex Query ────────────────────────────────────────────────────────────────────────────────────────────────────


def _SearchOpenAlex(
    query: str,
    min_cite: int,
    year_range: str | None,
    field: str,
    limit: int,
) -> list[dict]:
    """Query OpenAlex for high-citation papers."""
    # Build filter
    filters = [f"cited_by_count:>{min_cite}"]
    if year_range:
        filters.append(f"publication_year:{year_range}")

    # Resolve field to OpenAlex concept ID (API-level filtering)
    concept_id = _ResolveFieldConcept(field)
    if concept_id:
        filters.append(f"concepts.id:https://openalex.org/{concept_id}")
        print(f"[classics] Field filter: {field} → {concept_id}", file=sys.stderr)

    params = {
        "search": query,
        "filter": ",".join(filters),
        "sort": "cited_by_count:desc",
        "per_page": str(min(limit, 50)),
        "select": "title,cited_by_count,publication_year,authorships,doi,abstract_inverted_index,id",
        "mailto": kPoliteEmail,
    }

    url = f"{kOpenAlexBase}?{urlencode(params)}"

    try:
        req = Request(url, headers={"User-Agent": "arxiv-fetch/1.0 (scholar-agent)"})
        with urlopen(req, timeout=kRequestTimeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError) as e:
        print(f"[ERROR] OpenAlex API failed: {e}", file=sys.stderr)
        print("  Hint: Check network connectivity. OpenAlex is free and has no key.", file=sys.stderr)
        return []

    results = data.get("results", [])
    papers = []

    for item in results:
        title = item.get("title", "").strip()
        if not title:
            continue

        # Reconstruct abstract from inverted index
        abstract = _ReconstructAbstract(item.get("abstract_inverted_index"))

        # Extract authors
        authors = []
        for authorship in item.get("authorships", [])[:6]:
            name = authorship.get("author", {}).get("display_name", "")
            if name:
                authors.append(name)

        # Extract DOI
        doi = item.get("doi", "") or ""
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]

        papers.append({
            "title": title,
            "authors": authors,
            "year": item.get("publication_year"),
            "cited_by_count": item.get("cited_by_count", 0),
            "doi": doi,
            "abstract": abstract,
            "openalex_id": item.get("id", ""),
        })

    return papers


# ─── Output ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _PrintTable(papers: list[dict]) -> None:
    """Print papers as formatted table."""
    print()
    print(f"{'#':>3} {'Cites':>6} {'Year':>4}  {'Title':<60} {'Authors'}")
    print("-" * 120)

    for i, p in enumerate(papers, 1):
        title = p["title"][:58] + "…" if len(p["title"]) > 58 else p["title"]
        authors = ", ".join(p["authors"][:3])
        if len(p["authors"]) > 3:
            authors += f" +{len(p['authors']) - 3}"
        authors = authors[:35] + "…" if len(authors) > 35 else authors
        print(f"{i:>3} {p['cited_by_count']:>6} {p['year'] or '':>4}  {title:<60} {authors}")

    print("-" * 120)
    print(f"Total: {len(papers)} papers | Sorted by citation count (desc)")
    if papers and papers[0].get("doi"):
        print("  Tip: Use --import to import these to Zotero via DOI")
    print()


def _PrintJson(papers: list[dict]) -> None:
    """Print papers as JSON."""
    print(json.dumps(papers, ensure_ascii=False, separators=(",", ":")))


# ─── Import ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def _ImportPapers(papers: list[dict], config) -> int:
    """Import papers to Zotero using DOI (delegates to cmd_import logic)."""
    from .cmd_import import _ImportOneItem

    raw_collection_key = getattr(config, "inbox_collection_key", "")
    if not raw_collection_key:
        print(
            "[import] No target collection configured. Set "
            "zotero.inbox_collection_key in shared/paths.local.yaml.",
            file=sys.stderr,
        )
        return 2
    try:
        collection_key = normalize_key(raw_collection_key, "target collection key")
    except ZoteroAPIError as error:
        print(f"[import] {error}", file=sys.stderr)
        return 2

    dois = [p["doi"] for p in papers if p.get("doi")]
    if not dois:
        print("[import] No papers have DOI for import.", file=sys.stderr)
        return 1

    print(f"\n[import] Importing {len(dois)} papers to Zotero...", file=sys.stderr)
    n_ok = 0
    for i, p in enumerate(papers, 1):
        doi = p.get("doi", "")
        if not doi:
            print(f"  [{i}/{len(papers)}] ✗ {p['title'][:50]}... (no DOI)", file=sys.stderr)
            continue

        item = {"type": "doi", "identifier": doi, "title": p["title"]}
        success, msg = _ImportOneItem(item, collection_key)
        if success:
            print(f"  [{i}/{len(papers)}] ✓ {p['title'][:50]}", file=sys.stderr)
            n_ok += 1
        else:
            print(f"  [{i}/{len(papers)}] ✗ {p['title'][:50]}... ({msg})", file=sys.stderr)

        if i < len(papers):
            time.sleep(2)

    print(f"\n[import] Done: {n_ok}/{len(papers)} imported.", file=sys.stderr)
    return 0 if n_ok > 0 else 1


# ─── Helpers ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def _ReconstructAbstract(inverted_index: dict | None) -> str:
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    try:
        max_pos = max(max(positions) for positions in inverted_index.values())
        words = [""] * (max_pos + 1)
        for word, positions in inverted_index.items():
            for pos in positions:
                words[pos] = word
        return " ".join(words)
    except (ValueError, TypeError):
        return ""


def _ResolveFieldConcept(field: str) -> str | None:
    """Resolve user-facing field name to OpenAlex concept ID.

    Returns concept ID (e.g. 'C154945302') or None if not recognized.
    Unrecognized fields are silently skipped (search still works via keywords).
    """
    if not field:
        return None
    key = field.lower().strip()
    return kFieldConcepts.get(key)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_daily.py — 'daily' subcommand: fetch today's new arXiv papers.

Orchestrates: cache (corpus) → client (fetch) → dedup (filter) → ranker (score) → formatter.
Optionally downloads PDFs for top-N papers.
"""

from __future__ import annotations

import sys

from ..cache import CorpusCache
from ..client import ArxivClient
from ..dedup import Deduplicator
from ..formatter import Formatter
from ..ranker import Ranker


def Register(subparsers) -> None:
    """Register the 'daily' subcommand."""
    p = subparsers.add_parser("daily", help="Fetch today's new arXiv papers (via RSS)")
    p.add_argument("--cat", required=True, help="arXiv categories, comma-separated (e.g. cs.RO,cs.CV)")
    p.add_argument("--top", type=int, default=0, help="Show only top N papers (0 = all)")
    p.add_argument("--cross", action="store_true", help="Include cross-listed papers")
    p.add_argument("--no-rank", action="store_true", help="Skip relevance ranking (no Zotero needed)")
    p.add_argument("--no-dedup", action="store_true", help="Skip deduplication check")
    p.add_argument("--json", action="store_true", help="Output JSON instead of table")
    p.add_argument("--download", action="store_true", help="Download PDFs for displayed papers")
    p.add_argument("-d", "--dir", default="./papers", help="PDF download directory (default: ./papers)")
    p.add_argument("--refresh", action="store_true", help="Force full cache rebuild from Zotero")
    p.add_argument("--sync-only", action="store_true", help="Only update cache, don't fetch arXiv")
    p.set_defaults(func=_Run)


def _Run(args, config) -> int:
    """Execute the daily command."""
    categories = [c.strip() for c in args.cat.split(",")]
    client = ArxivClient()
    formatter = Formatter()

    # ─── Corpus cache management ──────────────────────────────────────────────────────────────────────────────────────
    cache = _LoadOrBuildCache(config, force_rebuild=args.refresh)

    if args.sync_only:
        return 0

    # ─── Step 1: Fetch from RSS + API ─────────────────────────────────────────────────────────────────────────────────
    print(f"[arXiv] Fetching daily papers for: {', '.join(categories)}...", file=sys.stderr)
    papers = client.FetchDaily(categories, include_cross=args.cross)
    if not papers:
        print("[arXiv] No new papers today (weekend/holiday?).", file=sys.stderr)
        return 0
    print(f"[arXiv] Found {len(papers)} papers from RSS.", file=sys.stderr)

    # ─── Step 2: Dedup ────────────────────────────────────────────────────────────────────────────────────────────────
    if not args.no_dedup and cache and cache.IsLoaded():
        titles = cache.GetTitles()
        if titles:
            deduplicator = Deduplicator(titles)
            before = len(papers)
            papers = deduplicator.Filter(papers)
            removed = before - len(papers)
            if removed:
                print(f"[arXiv] Dedup: removed {removed} already in library.", file=sys.stderr)
    elif not args.no_dedup:
        # Fallback to live API if cache unavailable
        titles = _FetchLibraryTitlesLive(config)
        if titles:
            deduplicator = Deduplicator(titles)
            before = len(papers)
            papers = deduplicator.Filter(papers)
            removed = before - len(papers)
            if removed:
                print(f"[arXiv] Dedup (live): removed {removed} already in library.", file=sys.stderr)

    # ─── Step 3: Rank ─────────────────────────────────────────────────────────────────────────────────────────────────
    if not args.no_rank:
        # Check if papers have abstracts (RSS-only fallback won't have them)
        has_abstracts = any(p.abstract for p in papers)
        if not has_abstracts:
            print(
                "[arXiv] Papers have no abstracts (RSS-only mode), skipping TF-IDF rank.",
                file=sys.stderr,
            )
            print("  Hint: arXiv API was rate-limited. Titles available for dedup.", file=sys.stderr)
        else:
            abstracts = []
            if cache and cache.IsLoaded():
                abstracts = cache.GetAbstracts()
            if not abstracts:
                # Fallback to live API
                abstracts = _FetchCorpusAbstractsLive(config)

            if abstracts:
                print(f"[arXiv] Ranking against {len(abstracts)} library papers...", file=sys.stderr)
                ranker = Ranker()
                papers = ranker.Rank(papers, abstracts)
            else:
                print("[arXiv] No corpus available, skipping rank.", file=sys.stderr)

    # ─── Step 4: Trim ─────────────────────────────────────────────────────────────────────────────────────────────────
    if args.top > 0:
        papers = papers[: args.top]

    # ─── Step 5: Output ───────────────────────────────────────────────────────────────────────────────────────────────
    show_score = not args.no_rank
    if args.json:
        formatter.PrintJson(papers)
    else:
        formatter.PrintTable(papers, show_score=show_score)

    # ─── Step 6: Download ─────────────────────────────────────────────────────────────────────────────────────────────
    if args.download and papers:
        print(f"\n[arXiv] Downloading {len(papers)} PDFs to {args.dir}...", file=sys.stderr)
        for i, paper in enumerate(papers, 1):
            try:
                path = client.DownloadPdf(paper, args.dir)
                print(f"  [{i}/{len(papers)}] {paper.arxiv_id} → {path}", file=sys.stderr)
            except Exception as e:
                print(f"  [{i}/{len(papers)}] FAILED {paper.arxiv_id}: {e}", file=sys.stderr)

    return 0


# ─── Cache Helpers ────────────────────────────────────────────────────────────────────────────────────────────────────


def _LoadOrBuildCache(config, force_rebuild: bool = False) -> CorpusCache | None:
    """Load corpus cache, update incrementally if needed."""
    if config is None:
        return None

    cache = CorpusCache(config.mineru_cache)

    if force_rebuild:
        print("[cache] Rebuilding corpus cache...", file=sys.stderr)
        n = cache.Rebuild(config.api_url, config.user_id)
        if n >= 0:
            print(f"[cache] Rebuilt: {cache.Size()} entries.", file=sys.stderr)
        else:
            print("[cache] Rebuild failed (Zotero unreachable).", file=sys.stderr)
        return cache

    # Try loading from disk
    if cache.Load():
        print(f"[cache] Loaded {cache.Size()} entries (updated {cache.Age()}).", file=sys.stderr)
        # Incremental update
        n = cache.Update(config.api_url, config.user_id)
        if n > 0:
            print(f"[cache] Updated: +{n} new entries (total {cache.Size()}).", file=sys.stderr)
        elif n == 0:
            pass  # Up to date
        else:
            print("[cache] Zotero offline, using stale cache.", file=sys.stderr)
    else:
        # First time — build from scratch
        print("[cache] No cache found, building...", file=sys.stderr)
        n = cache.Update(config.api_url, config.user_id)
        if n >= 0:
            print(f"[cache] Built: {cache.Size()} entries.", file=sys.stderr)
        else:
            print("[cache] Zotero unreachable, no cache available.", file=sys.stderr)
            return None

    return cache


# ─── Fallback Live Fetchers (when cache unavailable) ──────────────────────────────────────────────────────────────────


def _FetchLibraryTitlesLive(config) -> list[str]:
    """Get titles from Zotero local API directly (fallback)."""
    if config is None:
        return []
    try:
        from zotero.api import ZoteroAPI

        api = ZoteroAPI(config.api_url, config.user_id)
        if not api.check_connection():
            return []
        items = api.get_items(limit=500, sort="dateAdded", direction="desc")
        return [item["data"]["title"] for item in items if item["data"].get("title")]
    except Exception:
        return []


def _FetchCorpusAbstractsLive(config) -> list[str]:
    """Get abstracts from Zotero local API directly (fallback)."""
    if config is None:
        return []
    try:
        from zotero.api import ZoteroAPI

        api = ZoteroAPI(config.api_url, config.user_id)
        if not api.check_connection():
            return []
        items = api.get_items(limit=200, sort="dateAdded", direction="desc")
        return [
            item["data"]["abstractNote"].strip()
            for item in items
            if item["data"].get("abstractNote", "").strip() and len(item["data"]["abstractNote"]) > 50
        ]
    except Exception:
        return []

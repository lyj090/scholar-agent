#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_find.py — Unified paper discovery command.

Decouples two orthogonal dimensions:
  - Time: today (RSS) | recent (arXiv API) | classic (OpenAlex, citation-sorted)
  - Scope: --profile (library prior) | --field X | --keywords "..." | --cat cs.RO

All combinations are valid. Script handles routing + ranking internally.
LLM only needs to read the output.

NOT responsible for: Zotero import (cmd_import), note generation.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from ..cache import CorpusCache, note, set_quiet
from ..dedup import Deduplicator
from ..formatter import Formatter
from ..protocol import Paper
from ..ranker import Ranker
from ..llm_refiner import CreateLLMRefiner

# ─── Constants ─────────────────────────────────────────────────────────────────────────────────────────────────────────

kDefaultTopN = 15


# ─── Public ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'find' subcommand."""
    p = subparsers.add_parser(
        "find",
        help="Discover papers (time × scope, fully configurable)",
    )

    # Time dimension
    p.add_argument(
        "--period", "-p", choices=["today", "recent", "classic"], default="today",
        help="Time scope: today (RSS), recent (arXiv search), classic (OpenAlex high-cite)",
    )

    # Scope dimension (at least one recommended, but defaults to --profile)
    scope = p.add_argument_group("scope (领域约束, choose one or combine)")
    scope.add_argument(
        "--profile", action="store_true",
        help="Use library-derived research profile (default if no scope given)",
    )
    scope.add_argument("--field", "-f", help="Field filter: robotics, cv, rl, manipulation, ...")
    scope.add_argument("--keywords", "-k", help="Search keywords (free text)")
    scope.add_argument("--cat", help="arXiv category (cs.RO, cs.CV, ...) — today/recent only")

    
    # Multi-source support (P0)
    source = p.add_argument_group("data sources (P0)")
    source.add_argument(
        "--source", "-s",
        default="arxiv",
        help="Comma-separated data sources: arxiv,biorxiv,openreview (default: arxiv)"
    )

# Common options
    p.add_argument("--top", "-n", type=int, default=kDefaultTopN, help=f"Max results (default: {kDefaultTopN})")
    p.add_argument("--min-cite", type=int, default=50, help="Min citations (classic mode, default: 50)")
    p.add_argument("--year", help="Year range for classic mode: '2018-2024'")
    p.add_argument("--no-dedup", action="store_true", help="Skip deduplication against library")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--report", action="store_true", help="Generate a Markdown report")
    p.add_argument("--output", type=str, help="Path to save the report/JSON")
    p.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress lines (cache/status chatter). Errors still print.",
    )

    # Recommended list options
    recommended = p.add_argument_group("recommended list options")
    recommended.add_argument(
        "--save-recommended",
        action="store_true",
        help="Save recommended papers to data/recommended.json",
    )
    recommended.add_argument(
        "--dedup-recommended",
        action="store_true",
        help="Strict dedup: filter out already recommended papers",
    )
    recommended.add_argument(
        "--show-status",
        action="store_true",
        help="Show ⭐ mark for papers already in recommended.json",
    )

    # LLM refinement options (P1)
    llm_group = p.add_argument_group("LLM refinement options (P1)")
    llm_group.add_argument(
        "--llm-refine",
        action="store_true",
        help="Use LLM to refine candidate papers (filter + score + summarize)",
    )
    llm_group.add_argument(
        "--llm-mode",
        choices=["heuristic", "api", "agent"],
        default="heuristic",
        help="LLM mode: heuristic (local, free), api (external, requires key), or agent (local Agent via JSON handoff)",
    )
    llm_group.add_argument(
        "--llm-model",
        default="",
        help="LLM model for api mode (default: from config)",
    )
    llm_group.add_argument(
        "--agent-read-result",
        action="store_true",
        help="Read Agent processing result from data/llm_refine_input_output.json",
    )

    p.set_defaults(func=_Run)


def _Run(args, config) -> int:
    """Execute the find command — route based on period × scope."""
    # Handle --agent-read-result (read Agent processing result)
    if hasattr(args, 'agent_read_result') and args.agent_read_result:
        return _ReadAgentResult(args)

    # Must precede any call site: it gates the progress lines below.
    set_quiet(getattr(args, "quiet", False))

    # If no scope specified, default to --profile
    if not args.profile and not args.field and not args.keywords and not args.cat:
        args.profile = True

    # Load cache for dedup + profile
    cache = _LoadCache(config)

    # Resolve scope
    scope = _ResolveScope(args, cache)

    # Route by period
    if args.period == "today":
        return _FindToday(args, config, cache, scope)
    elif args.period == "recent":
        return _FindRecent(args, config, cache, scope)
    elif args.period == "classic":
        return _FindClassic(args, config, cache, scope)

    return 1


# ─── Period Handlers ───────────────────────────────────────────────────────────────────────────────────────────────────


def _FindToday(args, config, cache: CorpusCache | None, scope: dict) -> int:
    """Today's new papers from arXiv RSS or other sources."""
    # Multi-source support (P0)
    sources = [s.strip() for s in args.source.split(",")]
    all_papers = []
    formatter = Formatter()

    categories = scope.get("categories", ["cs.RO"])
    note(f"[find] Period: today | Categories: {','.join(categories)} | Sources: {','.join(sources)}")

    for source in sources:
        if source == "arxiv":
            from ..client import ArxivClient
            client = ArxivClient()
            papers = client.FetchDaily(categories, include_cross=False)
            if papers:
                all_papers.extend(papers)
                note(f"[find] arXiv RSS: {len(papers)} papers.")
        elif source == "biorxiv":
            from ..clients.biorxiv_client import BioRxivClient
            client = BioRxivClient()
            papers = client.FetchRecent(days=1, max_results=args.top * 2)
            if papers:
                all_papers.extend(papers)
                note(f"[find] BioRxiv: {len(papers)} papers.")
        else:
            note(f"[find] Source \'{source}\' not supported for \'today\' period, skipping.")
    
    papers = all_papers
    if not papers:
        note("[find] No new papers today (weekend/holiday?).")
        return 0
    
    note(f"[find] Total: {len(papers)} papers.")

    # Dedup
    papers = _ApplyDedup(papers, cache, args)
    if papers is None:
        papers = []
    if papers is None:
        papers = []

    # Rank (use library TF-IDF if abstracts available)
    papers = _ApplyRank(papers, cache, scope)
    if papers is None:
        papers = []
    if papers is None:
        papers = []

    # Keyword post-filter (for --keywords in today mode)
    if scope.get("keywords"):
        papers = _KeywordFilter(papers, scope["keywords"])
        if papers is None:
            papers = []
        note(f"[find] Keyword filter: {len(papers)} match.")

    # Trim
    papers = papers[:args.top]

    # Apply LLM refinement if requested (P1)
    if args.llm_refine:
        # Build query from scope if not provided
        query = scope.get("query", "")
        if not query:
            # Use categories as query
            query = " ".join(scope.get("categories", []))
        if not query:
            # Use keywords as query
            query = scope.get("keywords", "")
        
        papers = _ApplyLLMRefinement(papers, query, args)

    _OutputResults(papers, formatter, args, show_score=True)

    # Save to recommended list if requested
    if args.save_recommended:
        _SaveToRecommended(papers)

    return 0


def _FindRecent(args, config, cache: CorpusCache | None, scope: dict) -> int:
    """Recent papers from arXiv API search."""
    # Multi-source support (P0)
    sources = [s.strip() for s in args.source.split(",")]
    all_papers = []
    formatter = Formatter()

    query = scope.get("query", "")
    if not query:
        print("[ERROR] --keywords required for recent mode.", file=sys.stderr)
        print("  Or use --profile to auto-generate from library.", file=sys.stderr)
        return 1

    note(f"[find] Period: recent | Query: \"{query}\"")

    # Multi-source support (P0)
    for source in sources:
        if source == "arxiv":
            from ..client import ArxivClient
            client = ArxivClient()
            try:
                papers = client.Search(query, max_results=args.top * 2)
                all_papers.extend(papers)
                note(f"[find] arXiv API: {len(papers)} results.")
            except RuntimeError as e:
                print(f"[ERROR] arXiv search failed: {e}", file=sys.stderr)
        elif source == "openreview":
            from ..clients.openreview_client import OpenReviewClient
            client = OpenReviewClient()
            papers = client.FetchRecent(days=30, max_results=args.top * 2)
            all_papers.extend(papers)
            note(f"[find] OpenReview: {len(papers)} papers.")
        else:
            note(f"[find] Source '{source}' not supported for 'recent' period, skipping.")
    
    papers = all_papers
    if not papers:
        note("[find] No results.")

        # Save to recommended list if requested
        if args.save_recommended:
            _SaveToRecommended(papers)
        return 0
    note(f"[find] Found {len(papers)} results.")

    # Dedup
    papers = _ApplyDedup(papers, cache, args)
    if papers is None:
        papers = []
    if papers is None:
        papers = []

    # Rank against library
    papers = _ApplyRank(papers, cache, scope)
    if papers is None:
        papers = []

    papers = (papers or [])[:args.top]

    # Apply LLM refinement if requested (P1)
    if args.llm_refine:
        papers = _ApplyLLMRefinement(papers, scope.get("query", ""), args)
        if not papers:  # Agent mode returns empty, user needs to run again
            return 0

    _OutputResults(papers, formatter, args, show_score=True)
    
    # Save to recommended list if requested
    if args.save_recommended:
        _SaveToRecommended(papers)
    
    return 0


def _FindClassic(args, config, cache: CorpusCache | None, scope: dict) -> int:
    """Classic high-citation papers from OpenAlex / OpenReview."""
    from .cmd_classics import _SearchOpenAlex

    query = scope.get("query", "")
    field = scope.get("field", "")

    if not query and not field:
        print("[ERROR] --keywords or --field required for classic mode.", file=sys.stderr)
        print("  Or use --profile to auto-generate from library.", file=sys.stderr)
        return 1

    # Multi-source support (P0)
    sources = [s.strip() for s in args.source.split(",")]
    all_papers = []

    for source in sources:
        if source in ("openalex", "arxiv"):
            note(f"[find] Period: classic | Query: \"{query}\" | Field: {field or 'any'} | Source: {source}")
            papers = _SearchOpenAlex(
                query=query,
                min_cite=args.min_cite,
                year_range=args.year,
                field=field,
                limit=args.top,
            )
            all_papers.extend(papers)
            note(f"[find] OpenAlex: {len(papers)} classic papers.")
        elif source == "openreview":
            from ..clients.openreview_client import OpenReviewClient
            client = OpenReviewClient()
            # Fetch from major conferences (last 5 years)
            import datetime
            current_year = datetime.datetime.now().year
            for conf in ["NeurIPS", "ICML", "ICLR"]:
                if len(all_papers) >= args.top * 2:
                    break
                conf_papers = client.FetchByConference(conf, current_year, max_results=args.top)
                all_papers.extend(conf_papers)
            note(f"[find] OpenReview (classic): {len(all_papers)} papers.")
        else:
            note(f"[find] Source '{source}' not supported for 'classic' period, skipping.")
    
    papers = all_papers
    if not papers:
        note("[find] No classic papers found.")
        print("  Hint: Try lowering --min-cite or broadening keywords.", file=sys.stderr)
        return 0

    note(f"[find] Found {len(papers)} classic papers.")

    # Dedup against library (by title)
    if not args.no_dedup and cache and cache.IsLoaded():
        titles = cache.GetTitles()
        if titles:
            titles_lower = {t.lower().strip() for t in titles}
            before = len(papers)
            papers = [p for p in papers if p["title"].lower().strip() not in titles_lower]
            removed = before - len(papers)
            if removed:
                note(f"[find] Dedup: -{removed} already in library.")

    # Re-rank against library
    if cache and cache.IsLoaded():
        papers = _RerankClassics(papers, cache)
    
    # Apply LLM refinement if requested (P1)
    if args.llm_refine:
        # Convert dict to Paper objects for refinement
        temp_papers = _ConvertDictsToPapers(papers)
        temp_papers = _ApplyLLMRefinement(temp_papers, scope.get("query", ""), args)
        if not temp_papers:  # Agent mode returns empty
            return 0
        # Convert back to dicts
        papers = [p.ToDict() for p in temp_papers]

    # Save to recommended list if requested
    if args.save_recommended:
        _SaveToRecommended(papers)

    # Output results (handles --show-status, --json, --report)
    # Convert to Paper objects for output
    output_papers = _ConvertDictsToPapers(papers) if isinstance(papers[0], dict) else papers
    _OutputResults(output_papers, Formatter(), args, show_score=True)

    return 0


# ─── Scope Resolution ──────────────────────────────────────────────────────────────────────────────────────────────────


def _ResolveScope(args, cache: CorpusCache | None) -> dict:
    """Resolve user's scope parameters into a unified dict.

    Returns:
        {
            "categories": [...],  # for today mode
            "query": "...",       # for recent/classic mode
            "field": "...",       # for classic mode (OpenAlex concept filter)
            "keywords": "...",    # for post-filtering
        }
    """
    scope: dict = {}

    # Explicit --cat
    if args.cat:
        scope["categories"] = [c.strip() for c in args.cat.split(",")]

    # Explicit --field
    if args.field:
        scope["field"] = args.field
        # Map field to category for today mode
        field_to_cat = {
            "robotics": "cs.RO", "cv": "cs.CV", "computer vision": "cs.CV",
            "rl": "cs.LG", "reinforcement learning": "cs.LG",
            "ml": "cs.LG", "machine learning": "cs.LG",
            "ai": "cs.AI", "nlp": "cs.CL",
        }
        if not scope.get("categories"):
            mapped = field_to_cat.get(args.field.lower())
            if mapped:
                scope["categories"] = [mapped]

    # Explicit --keywords
    if args.keywords:
        scope["keywords"] = args.keywords
        scope["query"] = args.keywords

    # --profile: derive from library
    if args.profile and cache and cache.IsLoaded():
        profile = cache.GetProfile()
        topics = profile.get("topics", [])
        kws = profile.get("keywords", [])

        if not scope.get("categories"):
            scope["categories"] = profile.get("categories", ["cs.RO"])

        if not scope.get("query"):
            # For classic mode: use top 2 topics as focused query
            # For today/recent: use broader keyword set
            if args.period == "classic":
                scope["query"] = " ".join(topics[:2]) if topics else ""
            else:
                bigrams = [k for k in kws if " " in k or "-" in k][:3]
                unigrams = [k for k in kws if " " not in k and "-" not in k][:2]
                scope["query"] = " ".join(bigrams + unigrams) if (bigrams or unigrams) else ""

        if not scope.get("field"):
            # Map top topic to OpenAlex field
            topic_to_field = {
                "manipulation": "robotics", "reinforcement learning": "robotics",
                "event vision": "computer vision", "aerial robotics": "robotics",
                "control": "robotics", "robotics": "robotics",
                "locomotion": "robotics", "legged robotics": "robotics",
                "humanoid robotics": "robotics", "sim to real": "robotics",
                "imitation learning": "robotics", "hand tactile": "robotics",
                "slam": "computer vision", "tracking": "computer vision",
            }
            for t in topics:
                if t in topic_to_field:
                    scope["field"] = topic_to_field[t]
                    break
            if not scope.get("field"):
                # Fallback: map from category
                cat_to_field = {
                    "cs.RO": "robotics", "cs.CV": "computer vision",
                    "cs.LG": "machine learning",
                }
                for c in scope.get("categories", []):
                    if c in cat_to_field:
                        scope["field"] = cat_to_field[c]
                        break

        print(
            f"[find] Profile: topics={topics[:5]}, "
            f"categories={scope.get('categories')}", file=sys.stderr,
        )

    # Defaults
    if not scope.get("categories"):
        scope["categories"] = ["cs.RO"]

    return scope


# ─── Shared Logic ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _LoadCache(config) -> CorpusCache | None:
    """Load corpus cache (best-effort)."""
    if config is None:
        return None
    cache = CorpusCache(config.mineru_cache)
    if cache.Load():
        note(f"[cache] {cache.Size()} entries (updated {cache.Age()}).")
        # Incremental update
        n = cache.Update(config.api_url, config.user_id)
        if n > 0:
            note(f"[cache] +{n} new entries.")
        elif n < 0:
            note("[cache] Zotero offline, using stale cache.")
    else:
        note("[cache] Building cache...")
        n = cache.Update(config.api_url, config.user_id)
        if n < 0:
            note("[cache] Zotero unreachable.")
            return None
    return cache


def _ApplyDedup(papers: list[Paper], cache: CorpusCache | None, args) -> list[Paper]:
    """Remove papers already in library."""
    if args.no_dedup or not cache or not cache.IsLoaded():
        return papers
    titles = cache.GetTitles()
    if not titles:
        return papers
    dedup = Deduplicator(titles)
    before = len(papers)
    papers = dedup.Filter(papers)
    removed = before - len(papers)
    if removed:
        note(f"[find] Dedup: -{removed} already in library.")
    return papers


def _ApplyRank(papers: list[Paper], cache: CorpusCache | None, scope: dict) -> list[Paper]:
    """Rank papers against library using TF-IDF (if abstracts available)."""
    if not cache or not cache.IsLoaded():
        return papers
    # Check if papers have abstracts
    if not any(p.abstract for p in papers):
        # Fallback: keyword-based title scoring using profile
        return _TitleScoreByProfile(papers, cache)

    abstracts = cache.GetAbstracts()
    if not abstracts:
        return papers

    ranker = Ranker()
    papers = ranker.Rank(papers, abstracts)
    note(f"[find] Ranked by library relevance.")
    return papers


def _TitleScoreByProfile(papers: list[Paper], cache: CorpusCache) -> list[Paper]:
    """Score papers by title keyword match against profile (fallback when no abstracts)."""
    profile = cache.GetProfile()
    keywords = profile.get("keywords", [])
    if not keywords:
        return papers

    for paper in papers:
        title_lower = paper.title.lower()
        hits = sum(1 for kw in keywords if kw in title_lower)
        paper.score = float(hits)

    papers.sort(key=lambda p: p.score, reverse=True)
    if any(p.score > 0 for p in papers):
        note(f"[find] Ranked by profile keyword match (title-only mode).")
    return papers


def _KeywordFilter(papers: list[Paper], keywords: str) -> list[Paper]:
    """Post-filter papers by keywords in title/abstract."""
    terms = keywords.lower().split()
    result = []
    for p in papers:
        text = f"{p.title} {p.abstract}".lower()
        if any(t in text for t in terms):
            result.append(p)
    return result if result else papers  # Return all if no match (don't lose everything)


def _RerankClassics(papers: list[dict], cache: CorpusCache) -> list[dict]:
    """Re-rank OpenAlex results by library relevance (blend citation + relevance)."""
    abstracts = cache.GetAbstracts()
    if not abstracts or not any(p.get("abstract") for p in papers):
        return papers

    # Convert to Paper objects for ranking
    temp_papers = [
        Paper(
            arxiv_id=p.get("doi", ""),
            title=p["title"],
            authors=p.get("authors", []),
            abstract=p.get("abstract", ""),
            categories=[], primary_category="",
            published=str(p.get("year", "")),
            pdf_url="", abs_url="",
        )
        for p in papers
    ]

    ranker = Ranker()
    ranker.Rank(temp_papers, abstracts)

    # Blend: 60% citation rank + 40% relevance rank
    n = len(papers)
    cite_ranks = {p["title"]: i for i, p in enumerate(papers)}
    rel_ranks = {tp.title: i for i, tp in enumerate(temp_papers)}

    for p in papers:
        cite_r = cite_ranks.get(p["title"], n) / max(n, 1)
        rel_r = rel_ranks.get(p["title"], n) / max(n, 1)
        p["_blend_score"] = 0.6 * (1 - cite_r) + 0.4 * (1 - rel_r)

    papers.sort(key=lambda p: p.get("_blend_score", 0), reverse=True)
    # Clean up
    for p in papers:
        p.pop("_blend_score", None)

    note(f"[find] Re-ranked classics by citation×relevance blend.")
    return papers


def _OutputResults(papers: list[Paper], formatter: Formatter, args, show_score: bool) -> None:
    """Output results in requested format."""
    # Load recommended_ids if --show-status or --dedup-recommended is set
    recommended_ids = set()
    need_recommended = (hasattr(args, 'show_status') and args.show_status) or \
                      (hasattr(args, 'dedup_recommended') and args.dedup_recommended)
    
    if need_recommended:
        from ..store import JsonStore
        store = JsonStore("data/recommended.json")
        store.Load()  # MUST load from file first!
        recommended_ids = store.GetIDs()
        
        if hasattr(args, 'show_status') and args.show_status and recommended_ids:
            note(f"[find] Loaded {len(recommended_ids)} papers from recommended list.")
        
        # Apply strict dedup if requested
        if hasattr(args, 'dedup_recommended') and args.dedup_recommended and recommended_ids:
            before = len(papers)
            papers = [p for p in papers if p.arxiv_id not in recommended_ids]
            removed = before - len(papers)
            if removed:
                note(f"[find] Dedup recommended: -{removed} already recommended.")
    
    if args.json:
        data = json.dumps([p.ToDict() for p in papers], ensure_ascii=False, separators=(",", ":"))
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(data)
            note(f"[find] Saved JSON to {args.output}")
        else:
            print(data)
    elif args.report:
        report = _GenerateMarkdownReport(papers, args)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report)
            note(f"[find] Saved Markdown report to {args.output}")
        else:
            print(report)
    else:
        if args.output:
            # If output is set but no format specified, default to table in file
            from io import StringIO
            buf = StringIO()
            original_stdout = sys.stdout
            sys.stdout = buf
            formatter.PrintTable(papers, show_score=show_score, recommended_ids=recommended_ids)
            sys.stdout = original_stdout
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(buf.getvalue())
            note(f"[find] Saved table to {args.output}")
        else:
            formatter.PrintTable(papers, show_score=show_score, recommended_ids=recommended_ids)

def _GenerateMarkdownReport(papers: list[Paper], args) -> str:
    """Create a structured Markdown summary of results."""
    lines = [f"# arXiv Discovery Report - {args.period}", ""]
    lines.append(f"- **Scope**: {args.field or args.keywords or 'Profile-based'}")
    lines.append(f"- **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("| # | Score | ID | Title | Authors |")
    lines.append("|---|---|---|---|---|")

    for i, p in enumerate(papers, 1):
        authors = ", ".join(p.authors[:2]) + ("..." if len(p.authors) > 2 else "")
        title_link = f"[{p.title}]({p.abs_url})"
        lines.append(f"| {i} | {p.score:.2f} | `{p.arxiv_id}` | {title_link} | {authors} |")

    lines.append("")
    lines.append("## Details")
    lines.append("")
    for i, p in enumerate(papers, 1):
        lines.append(f"### {i}. {p.title}")
        lines.append(f"- **ID**: `{p.arxiv_id}`")
        lines.append(f"- **Authors**: {', '.join(p.authors)}")
        lines.append(f"- **URL**: {p.abs_url}")
        lines.append("")
        lines.append("> " + (p.abstract.replace("\n", " ") if p.abstract else "No abstract available."))
        lines.append("")

    return "\n".join(lines)


# ─── Recommended List Helpers ─────────────────────────────────────────────────────────────────────────

def _SaveToRecommended(papers: list[Paper]) -> None:
    """Save papers to data/recommended.json."""
    from ..store import JsonStore
    store = JsonStore("data/recommended.json")
    
    # Load existing
    existing = store.Load()
    existing_ids = {p.get("arxiv_id") for p in existing}
    
    # Add new papers (avoid duplicates)
    papers_dict = [p.ToDict() for p in papers if p.arxiv_id not in existing_ids]
    
    if papers_dict:
        store.Save(papers_dict, append=True)
        note(f"[find] Saved {len(papers_dict)} papers to recommended.json")


def _LoadRecommendedIDs() -> set[str]:
    """Load recommended paper IDs from data/recommended.json."""
    from ..store import JsonStore
    store = JsonStore("data/recommended.json")
    return store.GetIDs()


# ─── LLM Refinement ──────────────────────────────────────────────────────────────────────────────

def _ApplyLLMRefinement(papers: list[Paper], query: str, args) -> list[Paper]:
    """Apply LLM refinement to candidate papers (P1).
    
    Returns:
        Refined paper list (may be empty if LLM filtering removes all)
    """
    if not papers:
        return papers
    
    note(f"[find] Applying LLM refinement (mode: {args.llm_mode})...")
    
    try:
        refiner = CreateLLMRefiner(
            mode=args.llm_mode,
            model=args.llm_model,
        )
        
        # For agent mode, we need special handling
        if args.llm_mode == "agent":
            # Agent mode: output JSON and print instructions
            refiner.Refine(papers, query)
            # Return empty list (user needs to run again with --agent-read-result)
            return []
        
        # For heuristic/api mode: directly refine
        results = refiner.Refine(papers, query)
        
        if not results:
            note(f"[find] LLM refinement returned empty results.")
            return papers  # Fallback to original papers
        
        # Convert results back to Paper objects with scores
        refined_papers = []
        for r in results:
            paper = r["paper"]
            paper.score = r.get("score", 0.0)
            # Store summary as an attribute (not in Paper dataclass)
            setattr(paper, "_llm_summary", r.get("summary", ""))
            refined_papers.append(paper)
        
        note(f"[find] LLM refinement: {len(refined_papers)} papers after filtering.")
        return refined_papers
    
    except Exception as e:
        print(f"[ERROR] LLM refinement failed: {e}", file=sys.stderr)
        return papers  # Fallback to original papers


def _ReadAgentResult(args) -> int:
    """Read Agent processing result and output (for --agent-read-result)."""
    from ..llm_refiner import AgentLLMRefiner
    
    refiner = AgentLLMRefiner()
    results = refiner.ReadResult()
    
    if not results:
        print("[ERROR] Failed to read Agent result. Make sure data/llm_refine_input_output.json exists.", file=sys.stderr)
        return 1
    
    # Convert to Paper list and set scores
    papers = []
    for r in results:
        paper = r["paper"]
        paper.score = r.get("score", 0.0)
        # Store summary as an attribute
        setattr(paper, "_llm_summary", r.get("summary", ""))
        papers.append(paper)
    
    # Output
    formatter = Formatter()
    _OutputResults(papers, formatter, args, show_score=True)
    
    return 0


def _ConvertDictsToPapers(papers_dicts: list[dict]) -> list[Paper]:
    """Convert list of dicts (from OpenAlex) to list of Paper objects."""
    from ..protocol import Paper
    
    papers = []
    for p in papers_dicts:
        papers.append(Paper(
            arxiv_id=p.get("doi", p.get("id", "")),
            title=p.get("title", ""),
            authors=p.get("authors", []),
            abstract=p.get("abstract", ""),
            categories=[],
            primary_category="",
            published=str(p.get("year", "")),
            pdf_url="",
            abs_url=p.get("url", ""),
        ))
    
    return papers

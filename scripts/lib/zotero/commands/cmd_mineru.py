#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent-friendly MinerU cache discovery and bounded reading commands."""

from __future__ import annotations

import json
import sys

from ..api import ZoteroAPI
from ..config import ZoteroConfig
from ..mineru_catalog import (
    MineruAmbiguousError,
    MineruCatalog,
    MineruCatalogError,
    MineruNoteIndex,
    MineruNotFoundError,
)


def Register(subparsers) -> None:
    parser = subparsers.add_parser(
        "mineru",
        help="Discover, resolve, outline, and read exact MinerU caches",
    )
    actions = parser.add_subparsers(dest="mineru_action", required=True)

    grep_parser = actions.add_parser(
        "grep",
        help="Full-text search across every local MinerU cache (offline)",
    )
    grep_parser.add_argument(
        "pattern", help="Text (or regex with --regex) to find in cached papers"
    )
    grep_parser.add_argument(
        "--regex", action="store_true", help="Treat pattern as a Python regex"
    )
    grep_parser.add_argument(
        "--case-sensitive", action="store_true", help="Match case sensitively"
    )
    grep_parser.add_argument(
        "--context",
        "-C",
        type=int,
        default=0,
        help="Characters of context around each hit (default: 0)",
    )
    grep_parser.add_argument(
        "--max-hits",
        type=int,
        default=5,
        help="Max matching papers to report (default: 5)",
    )
    grep_parser.add_argument(
        "--hits-per-paper",
        type=int,
        default=1,
        help="Max excerpts shown per paper (default: 1)",
    )
    grep_parser.add_argument(
        "--max-chars",
        type=int,
        default=2000,
        help="Hard bound on total excerpt output (default: 2000)",
    )
    grep_parser.add_argument(
        "--titles-only",
        action="store_true",
        help="Only list matching papers, without excerpts",
    )
    grep_parser.add_argument("--json", action="store_true")
    grep_parser.set_defaults(func=_RunGrep)

    list_parser = actions.add_parser(
        "list", help="List MinerU-parsed Zotero attachments"
    )
    list_parser.add_argument("--query", "-q", help="Filter title, filename, key, or cache ID")
    list_parser.add_argument(
        "--unnoted",
        action="store_true",
        help="Only caches without a matching Zotero key/link or note filename in the vault",
    )
    list_parser.add_argument(
        "--all-caches",
        action="store_true",
        help="Show duplicate caches separately instead of grouping identical PDFs",
    )
    list_parser.add_argument("--limit", "-n", type=int, default=20)
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=_RunList)

    resolve_parser = actions.add_parser(
        "resolve", help="Resolve a cache ID, attachment key, parent key, or title"
    )
    _AddReferenceArgs(resolve_parser)
    resolve_parser.add_argument("--json", action="store_true")
    resolve_parser.set_defaults(func=_RunResolve)

    outline_parser = actions.add_parser(
        "outline", help="Return Markdown section spans without returning paper text"
    )
    _AddReferenceArgs(outline_parser)
    outline_parser.add_argument("--json", action="store_true")
    outline_parser.set_defaults(func=_RunOutline)

    read_parser = actions.add_parser(
        "read", help="Read one bounded section from the exact MinerU cache"
    )
    _AddReferenceArgs(read_parser)
    read_parser.add_argument(
        "--section",
        "-s",
        help="Section index or unique heading/prefix; omit only for short sources",
    )
    read_parser.add_argument(
        "--scope",
        choices=("self", "subtree"),
        default="self",
        help="Read only this heading body (default) or include numbered subsections",
    )
    read_parser.add_argument(
        "--max-chars",
        type=int,
        default=8000,
        help=(
            "Hard output bound (default: 8000; use 0 for unlimited). "
            "Lowered from 12000: the note body is Chinese, where 12000 characters "
            "is roughly 6K tokens for a single section, well above the 2-4K the "
            "workflow budgets per section. 8000 keeps a full section while making "
            "the truncation notice fire before context is flooded."
        ),
    )
    read_parser.add_argument("--json", action="store_true")
    read_parser.set_defaults(func=_RunRead)


def _AddReferenceArgs(parser) -> None:
    parser.add_argument(
        "reference", help="Cache ID, attachment key, parent item key, or title"
    )
    parser.add_argument(
        "--match",
        help="Narrow an ambiguous parent item, e.g. --match 'Chapter 6'",
    )


def _Catalog(config: ZoteroConfig) -> MineruCatalog:
    api = ZoteroAPI(config.api_url, config.user_id)
    return MineruCatalog(config.mineru_cache, api, config)


def _RunList(args, config: ZoteroConfig) -> int:
    catalog = _Catalog(config)
    note_index = MineruNoteIndex(config.vault) if args.unnoted else None
    records = catalog.find(args.query)
    groups = (
        [[record] for record in records]
        if args.all_caches
        else catalog.logical_groups(records)
    )
    rows = []
    for group in groups:
        record = catalog.representative(group)
        has_note = (
            any(note_index.has_note(candidate) for candidate in group)
            if note_index
            else None
        )
        if args.unnoted and has_note:
            continue
        row = record.to_dict(has_note=has_note)
        if len(group) > 1:
            row["duplicate_cache_ids"] = [candidate.cache_id for candidate in group]
            row["duplicate_attachment_keys"] = [
                candidate.attachment_key for candidate in group
            ]
        rows.append(row)
        if len(rows) >= max(0, args.limit):
            break

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    elif not rows:
        print("No matching MinerU caches.")
    else:
        for row in rows:
            note_flag = " unnoted" if row.get("has_note") is False else ""
            key = row.get("attachment_key") or "NO_KEY"
            print(f"{row['cache_id']}\t{key}\t{row['title']}{note_flag}")
    return 0


def _RunResolve(args, config: ZoteroConfig) -> int:
    try:
        record = _Catalog(config).resolve(args.reference, match=args.match)
    except MineruCatalogError as exc:
        return _PrintError(exc)

    data = record.to_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        for key, value in data.items():
            if value is not None:
                print(f"{key}\t{value}")
    return 0


def _RunOutline(args, config: ZoteroConfig) -> int:
    catalog = _Catalog(config)
    try:
        record = catalog.resolve(args.reference, match=args.match)
        sections = catalog.outline(record)
    except MineruCatalogError as exc:
        return _PrintError(exc)

    if args.json:
        payload = {
            "source": record.to_dict(),
            "section_count": len(sections),
            "sections": [section.to_dict() for section in sections],
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        for section in sections:
            print(
                f"{section.index}\tH{section.level}\t{section.chars}\t{section.heading}"
            )
    return 0


def _RunGrep(args, config: ZoteroConfig) -> int:
    catalog = _Catalog(config)
    try:
        matches = catalog.search_content(
            args.pattern,
            regex=args.regex,
            case_sensitive=args.case_sensitive,
            context=args.context,
            max_papers=args.max_hits,
            hits_per_paper=args.hits_per_paper,
        )
    except MineruCatalogError as exc:
        return _PrintError(exc)

    if args.json:
        payload = {
            "pattern": args.pattern,
            "paper_count": len(matches),
            "matches": [match.to_dict() for match in matches],
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0

    if not matches:
        print("NO_LOCAL_MATCH")
        print("MSG=not in the local corpus; fetching externally is justified")
        return 0

    budget = args.max_chars
    for match in matches:
        header = (
            f"{match.record.cache_id}\t{match.record.attachment_key or '-'}\t"
            f"{match.total_hits} hit(s)\t{match.record.title}"
        )
        print(header)
        if args.titles_only:
            continue
        for hit in match.hits:
            if budget <= 0:
                print("[TRUNCATED] output budget reached")
                return 0
            excerpt = hit.excerpt
            if len(excerpt) > budget:
                excerpt = excerpt[:budget].rstrip() + "…"
            budget -= len(excerpt)
            print(f"  @{hit.char_offset}: {excerpt}")
    return 0


def _RunRead(args, config: ZoteroConfig) -> int:
    catalog = _Catalog(config)
    max_chars = args.max_chars if args.max_chars and args.max_chars > 0 else None
    try:
        record = catalog.resolve(args.reference, match=args.match)
        result = catalog.read(
            record,
            section=args.section,
            scope=args.scope,
            max_chars=max_chars,
        )
    except MineruCatalogError as exc:
        return _PrintError(exc)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")))
    else:
        print(result.content)
        if result.truncated:
            print(
                f"[INFO] Returned {len(result.content)} of {result.original_chars} chars; "
                "select a narrower section or raise --max-chars.",
                file=sys.stderr,
            )
    return 0


def _PrintError(exc: MineruCatalogError) -> int:
    print(f"[ERROR] {exc}", file=sys.stderr)
    if isinstance(exc, MineruAmbiguousError):
        payload = [
            {
                "cache_id": record.cache_id,
                "title": record.title,
                "attachment_key": record.attachment_key,
                "source_filename": record.source_filename,
            }
            for record in exc.candidates
        ]
        print(json.dumps({"candidates": payload}, ensure_ascii=False), file=sys.stderr)
        return 3
    if isinstance(exc, MineruNotFoundError):
        return 2
    return 1

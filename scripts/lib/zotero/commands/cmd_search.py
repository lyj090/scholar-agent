#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_search.py — Full-text search across Zotero library.

Queries Zotero API with a search string, returns matching items.
NOT responsible for: API implementation, MinerU cache.
"""

from __future__ import annotations

from ..api import ZoteroAPI, ZoteroAPIError
from ..config import ZoteroConfig
from ..formatters import format_items_table, to_json


def Register(subparsers) -> None:
    """Register the 'search' subcommand."""
    p = subparsers.add_parser("search", help="Search library by keyword")
    p.add_argument("query", help="Search query string")
    p.add_argument("--limit", "-n", type=int, default=10, help="Max results (default: 10)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute the search command. Returns exit code."""
    api = ZoteroAPI(config.api_url, config.user_id)

    try:
        items = api.search(query=args.query, limit=args.limit)
    except ZoteroAPIError as e:
        print(f"[ERROR] {e}", file=__import__("sys").stderr)
        return 1

    if args.json:
        print(to_json(items))
    else:
        print(format_items_table(items))

    return 0

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_list.py — List library items.

Queries Zotero Local API for top-level items with optional filters.
NOT responsible for: API implementation, config loading.
"""

from __future__ import annotations

from ..api import ZoteroAPI, ZoteroAPIError
from ..config import ZoteroConfig
from ..formatters import format_items_table, to_json


def Register(subparsers) -> None:
    """Register the 'list' subcommand."""
    p = subparsers.add_parser("list", help="List library items")
    p.add_argument("--limit", "-n", type=int, default=10, help="Max items (default: 10)")
    p.add_argument("--collection", "-c", help="Filter by collection key")
    p.add_argument("--type", "-t", dest="item_type", help="Filter by itemType (e.g. preprint, journalArticle)")
    p.add_argument("--sort", "-s", default="dateAdded", choices=["dateAdded", "dateModified", "title"],
                   help="Sort field (default: dateAdded)")
    p.add_argument("--asc", action="store_true", help="Sort ascending (default: descending)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute the list command. Returns exit code."""
    api = ZoteroAPI(config.api_url, config.user_id)

    try:
        items = api.get_items(
            limit=args.limit,
            item_type=args.item_type,
            collection=args.collection,
            sort=args.sort,
            direction="asc" if args.asc else "desc",
        )
    except ZoteroAPIError as e:
        print(f"[ERROR] {e}", file=__import__("sys").stderr)
        return 1

    if args.json:
        print(to_json(items))
    else:
        print(format_items_table(items))

    return 0

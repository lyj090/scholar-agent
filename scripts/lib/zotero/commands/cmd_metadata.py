#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_metadata.py — Get full metadata for a single Zotero item.

Fetches item data + children (attachments/notes) from the API.
NOT responsible for: MinerU cache reading, note generation.
"""

from __future__ import annotations

import sys

from ..api import ZoteroAPI, ZoteroAPIError
from ..config import ZoteroConfig
from ..formatters import format_metadata, to_json


def Register(subparsers) -> None:
    """Register the 'metadata' subcommand."""
    p = subparsers.add_parser("metadata", help="Get item metadata by key")
    p.add_argument("key", help="Zotero item key (e.g. CMBCBW9Q)")
    p.add_argument("--children", action="store_true", help="Include children (attachments, notes)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute the metadata command. Returns exit code."""
    api = ZoteroAPI(config.api_url, config.user_id)

    try:
        item = api.get_item(args.key)
    except ZoteroAPIError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if args.children:
        try:
            children = api.get_children(args.key)
        except ZoteroAPIError:
            children = []
        item["_children"] = children

    if args.json:
        print(to_json(item))
    else:
        print(format_metadata(item))

        # Show children summary if requested
        if args.children and item.get("_children"):
            print("\nAttachments:")
            for child in item["_children"]:
                cd = child.get("data", {})
                ctype = cd.get("contentType", "")
                title = cd.get("title", "(untitled)")
                print(f"  [{cd.get('key', '?')}] {ctype or cd.get('itemType', '?')}: {title}")

    return 0

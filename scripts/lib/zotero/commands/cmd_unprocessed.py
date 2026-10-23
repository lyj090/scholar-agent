#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_unprocessed.py — List papers with MinerU cache but no Obsidian note.

Cross-references: Zotero API × MinerU cache × Obsidian notes directory.
NOT responsible for: note generation, tag assignment.
"""

from __future__ import annotations

from ..api import ZoteroAPI
from ..config import ZoteroConfig
from ..formatters import to_json
from ..mineru_catalog import MineruCatalog, MineruNoteIndex


def Register(subparsers) -> None:
    """Register the 'unprocessed' subcommand."""
    p = subparsers.add_parser("unprocessed", help="List papers with cache but no Obsidian note")
    p.add_argument("--limit", "-n", type=int, default=10, help="Max results (default: 10)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute the unprocessed command. Returns exit code."""
    api = ZoteroAPI(config.api_url, config.user_id)
    catalog = MineruCatalog(config.mineru_cache, api)
    note_index = MineruNoteIndex(config.vault)
    unprocessed = []

    for group in catalog.logical_groups():
        if len(unprocessed) >= args.limit:
            break
        if any(note_index.has_note(record) for record in group):
            continue
        record = catalog.representative(group)
        row = record.to_dict(has_note=False)
        if len(group) > 1:
            row["duplicate_cache_ids"] = [candidate.cache_id for candidate in group]
            row["duplicate_attachment_keys"] = [
                candidate.attachment_key for candidate in group
            ]
        unprocessed.append(row)

    # Output
    if args.json:
        print(to_json(unprocessed))
    else:
        if not unprocessed:
            print("All cached papers have Obsidian notes. Nothing to process.")
            return 0

        print(f"Papers with MinerU cache but no Obsidian note ({len(unprocessed)}):\n")
        for i, item in enumerate(unprocessed, 1):
            key = item.get("attachment_key") or "NO_KEY"
            print(f"  {i}. [{item['cache_id']}] [{key}] {item['title']}")
        print("\nUse 'zotero_cli.py mineru outline <attachment-key>'.")

    return 0

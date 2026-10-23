#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_sync_links.py — Backfill Obsidian ↔ Zotero links for existing literature notes.

One-time / on-demand batch sync. Not part of the agent skill workflow.

Usage:
    python3 zotero_cli.py sync-links
    python3 zotero_cli.py sync-links --dry-run
    python3 zotero_cli.py sync-links --json
"""

from __future__ import annotations

import json
import os
import sys

from ..api import ZoteroAPI
from ..config import ZoteroConfig
from ..link_sync import (
    extract_title_from_content,
    extract_zotero_key_from_content,
    finalize_note_links,
    is_valid_item_key,
    match_item_key_by_title,
)


def Register(subparsers) -> None:
    """Register the 'sync-links' subcommand."""
    p = subparsers.add_parser(
        "sync-links",
        help="Backfill bidirectional Obsidian ↔ Zotero links for existing notes",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    p.add_argument("--json", action="store_true", help="Output JSON report")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    api = ZoteroAPI(config.api_url, config.user_id)
    notes_dir = config.literature_notes

    if not os.path.isdir(notes_dir):
        print(f"[ERROR] Literature notes dir not found: {notes_dir}", file=sys.stderr)
        return 1

    results: list[dict] = []
    updated = 0
    skipped = 0
    failed = 0

    for filename in sorted(os.listdir(notes_dir)):
        if not filename.endswith(".md"):
            continue

        note_path = os.path.join(notes_dir, filename)
        with open(note_path, "r", encoding="utf-8") as f:
            content = f.read()

        item_key = extract_zotero_key_from_content(content)
        match_method = "frontmatter" if item_key else None

        if not item_key:
            title = extract_title_from_content(content) or os.path.splitext(filename)[0]
            item_key = match_item_key_by_title(title, api)
            if item_key:
                match_method = "title_search"

        if not item_key or not is_valid_item_key(item_key):
            failed += 1
            results.append({
                "file": filename,
                "status": "unmatched",
                "msg": "No valid zotero_key and title search failed",
            })
            continue

        if args.dry_run:
            results.append({
                "file": filename,
                "status": "would_update",
                "zotero_key": item_key,
                "match": match_method,
            })
            updated += 1
            continue

        try:
            new_content = finalize_note_links(
                content,
                item_key,
                api,
                note_path,
                config.vault,
                sync_backlink=True,
            )
            if new_content != content:
                with open(note_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                results.append({
                    "file": filename,
                    "status": "updated",
                    "zotero_key": item_key,
                    "match": match_method,
                })
                updated += 1
            else:
                results.append({
                    "file": filename,
                    "status": "unchanged",
                    "zotero_key": item_key,
                    "match": match_method,
                })
                skipped += 1
        except Exception as exc:
            failed += 1
            results.append({
                "file": filename,
                "status": "error",
                "msg": str(exc),
            })

    summary = {
        "updated": updated,
        "unchanged": skipped,
        "failed": failed,
        "total": len(results),
        "dry_run": args.dry_run,
        "results": results,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        mode = " (dry-run)" if args.dry_run else ""
        print(f"[OK] sync-links{mode}: {updated} updated, {skipped} unchanged, {failed} failed")
        for row in results:
            if row["status"] in ("unmatched", "error"):
                print(f"  ❌ {row['file']}: {row.get('msg', row['status'])}", file=sys.stderr)

    return 1 if failed else 0

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_backfill_status_tags.py — Add status/* tags to literature notes that lack them.

Scans obsidian.literature_notes and injects status/to-read (or preserves existing
status/* from the file) using frontmatter_tags.ensure_literature_status_tag.
"""

from __future__ import annotations

import json
import os
import sys

from ..config import ZoteroConfig
from ..frontmatter_tags import ensure_literature_status_tag, find_status_tag, extract_frontmatter


def Register(subparsers) -> None:
    """Register the 'backfill-status-tags' subcommand."""
    p = subparsers.add_parser(
        "backfill-status-tags",
        help="Inject status/* tags into literature notes missing them",
    )
    p.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    notes_dir = config.literature_notes
    if not os.path.isdir(notes_dir):
        print(f"[ERROR] Directory not found: {notes_dir}", file=sys.stderr)
        return 1

    md_files = sorted(f for f in os.listdir(notes_dir) if f.endswith(".md"))
    updated: list[dict] = []
    skipped: list[str] = []
    errors: list[dict] = []

    for filename in md_files:
        filepath = os.path.join(notes_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            errors.append({"file": filename, "error": str(e)})
            continue

        frontmatter = extract_frontmatter(content)
        if frontmatter is None:
            errors.append({"file": filename, "error": "No YAML frontmatter"})
            continue

        tags = frontmatter.get("tags")
        if isinstance(tags, list) and find_status_tag(tags):
            skipped.append(filename)
            continue

        new_content = ensure_literature_status_tag(content, filepath)
        if new_content == content:
            skipped.append(filename)
            continue

        if not args.dry_run:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)

        updated.append({"file": filename, "path": filepath})

    result = {
        "total": len(md_files),
        "updated": len(updated),
        "skipped": len(skipped),
        "errors": len(errors),
        "files_updated": [u["file"] for u in updated],
        "files_errored": errors,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[OK] backfill-status-tags: {len(updated)} updated, {len(skipped)} already OK, {len(errors)} errors")
        for item in updated:
            prefix = "WOULD UPDATE" if args.dry_run else "UPDATED"
            print(f"  {prefix}: {item['file']}")
        for item in errors:
            print(f"  ERROR: {item['file']}: {item['error']}", file=sys.stderr)

    return 1 if errors else 0

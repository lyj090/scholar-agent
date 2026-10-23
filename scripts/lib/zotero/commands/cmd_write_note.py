#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_write_note.py — Write a literature note to Obsidian vault.

Accepts note content via stdin and writes to the correct path.
The path is derived from the item title (same logic as prepare-note).

This allows agents to write notes using only Bash permission (heredoc),
without needing the Write tool permission.

Usage:
    python3 zotero_cli.py write-note ITEM_KEY <<'EOF'
    ---
    title: "Paper Title"
    ...
    ---
    # Paper Title
    ...
    EOF

NOT responsible for: note content generation (that's the LLM's job).
"""

from __future__ import annotations

import os
import re
import sys

from ..note_structure import validate_note
from ..figure_identity import validate_figures
from ..source_links import ensure_source_links

from ..api import ZoteroAPI, ZoteroAPIError
from ..config import ZoteroConfig
from ..frontmatter_tags import (
    ensure_literature_status_tag,
    find_note_by_zotero_key,
    note_filename_for_title,
)
from ..link_sync import finalize_note_links


def Register(subparsers) -> None:
    """Register the 'write-note' subcommand."""
    p = subparsers.add_parser(
        "write-note",
        help="Write note content (from stdin or file) to Obsidian vault",
    )
    p.add_argument("key", help="Zotero item key (used to derive output path)")
    p.add_argument("--force", action="store_true", help="Overwrite existing note")
    p.add_argument("--dry-run", action="store_true", help="Print path only, don't write")
    p.add_argument("--content-file", help="Read note content from file instead of stdin")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute write-note. Reads stdin, writes to correct path."""
    api = ZoteroAPI(config.api_url, config.user_id)

    # Get item title for path derivation
    try:
        item = api.get_item(args.key)
    except ZoteroAPIError as e:
        print(f"[ERROR] Cannot fetch item: {e}", file=sys.stderr)
        return 1

    title = item.get("data", {}).get("title", "").strip()
    if not title:
        print("[ERROR] Item has no title", file=sys.stderr)
        return 1

    # Derive note path (shared sanitiser: the three writers each carried a copy
    # and assemble-note's had drifted).
    note_filename = note_filename_for_title(title)
    # If a note for this key already exists under another name (renamed in
    # Obsidian, or written before the sanitiser changed), update that one rather
    # than creating a second note for the same paper.
    note_path = (
        find_note_by_zotero_key(config.literature_notes, args.key)
        or os.path.join(config.literature_notes, note_filename)
    )
    note_filename = os.path.basename(note_path)

    # Check existing. `--dry-run` reports before this gate: its whole purpose is
    # to tell you the target path, and refusing with "already exists" hides the
    # one fact the caller asked for.
    exists = os.path.isfile(note_path)
    if args.dry_run:
        state = "exists (would need --force)" if exists else "new"
        print(note_path)
        print(f"  [{state}]", file=sys.stderr)
        return 0

    if exists and not args.force:
        print(f"[ERROR] Note already exists: {note_filename}", file=sys.stderr)
        print("  Use --force to overwrite.", file=sys.stderr)
        return 1

    # Read content from --content-file or stdin
    if args.content_file:
        if not os.path.isfile(args.content_file):
            print(f"[ERROR] Content file not found: {args.content_file}", file=sys.stderr)
            return 1
        with open(args.content_file, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        if sys.stdin.isatty():
            print("[ERROR] No content on stdin. Pipe, use heredoc, or pass --content-file.", file=sys.stderr)
            print("  Usage with stdin:   python3 zotero_cli.py write-note KEY <<'EOF'", file=sys.stderr)
            print("  Usage with file:    python3 zotero_cli.py write-note KEY --content-file /tmp/note.md", file=sys.stderr)
            return 1
        content = sys.stdin.read()

    if not content.strip():
        print("[ERROR] Empty content received", file=sys.stderr)
        return 1

    content = ensure_source_links(content, item.get("data", {}))
    existing_path = note_path if os.path.isfile(note_path) else None
    content = ensure_literature_status_tag(content, existing_path)
    # Keep local Zotero/PDF quick links in every note. Backlink sync is a
    # separate Zotero write and may require an interactive confirmation UI.
    content = finalize_note_links(
        content,
        args.key,
        api,
        note_path,
        config.vault,
        sync_backlink=False,
    )

    issues = validate_note(content) + validate_figures(content, note_path)
    if issues:
        print("[ERROR] " + ", ".join(issues), file=sys.stderr)
        return 1

    # Write
    os.makedirs(os.path.dirname(note_path), exist_ok=True)
    with open(note_path + ".tmp", "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(note_path + ".tmp", note_path)

    print(f"[OK] Written: {note_path} ({len(content)} bytes)", file=sys.stderr)
    return 0

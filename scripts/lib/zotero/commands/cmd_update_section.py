#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_update_section.py — Replace a specific section in an existing Obsidian note.

Finds a heading by exact match, replaces all content from that heading to the
next heading of same or higher level, and writes atomically.

Usage:
    python3 zotero_cli.py update-section NOTE_PATH \
      --heading "## 3. World Model as Policy" \
      --content-file /tmp/new-section.md

This allows AI agents to modify one chapter of a long note at a time,
avoiding full-note rewrites.
"""

from __future__ import annotations

import os
import re
import sys

from ..note_structure import validate_note
from ..figure_identity import validate_figures
from ..source_links import ensure_source_links

from ..api import ZoteroAPI
from ..config import ZoteroConfig
from ..link_sync import extract_zotero_key_from_content, finalize_note_links


def Register(subparsers) -> None:
    """Register the 'update-section' subcommand."""
    p = subparsers.add_parser(
        "update-section",
        help="Replace a section in an existing note by heading",
    )
    p.add_argument("note_path", help="Path to the note file (absolute or relative to vault)")
    p.add_argument("--heading", required=True, help="Exact heading text to match (e.g. '## 3. World Model as Policy')")
    p.add_argument("--content-file", required=True, help="File containing replacement section content")
    p.add_argument("--dry-run", action="store_true", help="Preview replacement, don't write")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute update-section. Finds heading, replaces content, writes atomically."""
    note_path = os.path.expanduser(args.note_path)
    if not os.path.isabs(note_path):
        note_path = os.path.join(config.vault, note_path)

    if not os.path.isfile(note_path):
        print(f"[ERROR] Note file not found: {note_path}", file=sys.stderr)
        return 1

    if not os.path.isfile(args.content_file):
        print(f"[ERROR] Content file not found: {args.content_file}", file=sys.stderr)
        return 1

    with open(args.content_file, "r", encoding="utf-8") as f:
        new_section = f.read().rstrip("\n")

    with open(note_path, "r", encoding="utf-8") as f:
        original = f.read()

    # Keep the note's Zotero keys and ## 快捷链接 current. update-section only
    # rewrites one body section, so the block is injected before the split and
    # the target heading is located afterwards — headings for the section are
    # unaffected, but a note that predates link injection gains its links here.
    key = extract_zotero_key_from_content(original)
    if key:
        api = ZoteroAPI(config.api_url, config.user_id)
        updated = finalize_note_links(
            original, key, api, note_path, config.vault, sync_backlink=False
        )
    else:
        updated = original

    lines = updated.split("\n")

    # Parse heading level from --heading
    target_level = _heading_level(args.heading)
    if target_level is None:
        print(f"[ERROR] --heading must start with one or more '#' characters: {args.heading}", file=sys.stderr)
        return 1

    # Find the target heading line
    target_idx = None
    for i, line in enumerate(lines):
        if line.strip() == args.heading.strip():
            target_idx = i
            break

    if target_idx is None:
        print(f"[ERROR] Heading not found: {args.heading}", file=sys.stderr)
        return 1

    # Find the end: next heading of same or higher level, or EOF
    end_idx = len(lines)
    for j in range(target_idx + 1, len(lines)):
        if _is_heading_of_level(lines[j], target_level):
            end_idx = j
            break

    if args.dry_run:
        old_section = "\n".join(lines[target_idx:end_idx])
        print(f"--- OLD SECTION ({target_idx}:{end_idx}, {len(old_section)} chars) ---")
        print(old_section[:500])
        if len(old_section) > 500:
            print(f"... ({len(old_section) - 500} more chars)")
        print("--- NEW SECTION ---")
        print(new_section[:500])
        if len(new_section) > 500:
            print(f"... ({len(new_section) - 500} more chars)")
        return 0

    # Replace
    body = _strip_repeated_heading(new_section, args.heading)
    new_lines = lines[:target_idx] + [args.heading.strip(), "", body] + lines[end_idx:]
    new_content = "\n".join(new_lines)

    issues = validate_note(new_content) + validate_figures(new_content, note_path)
    if issues:
        print("[ERROR] " + ", ".join(issues), file=sys.stderr)
        return 1

    # Atomic write
    tmp_path = note_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    os.replace(tmp_path, note_path)

    print(f"[OK] Updated section '{args.heading}' in {os.path.basename(note_path)}", file=sys.stderr)
    print(f"     Lines {target_idx}:{end_idx} → {len(new_section)} chars", file=sys.stderr)
    return 0


def _strip_repeated_heading(section: str, heading: str) -> str:
    """Drop the leading target heading from a replacement section, if present.

    The command always writes ``--heading`` itself, so a content file that also
    starts with it produced two identical headings. Because the next
    ``update-section`` run replaces from the *first* match, that duplicate then
    compounded: every edit added another copy of the heading. Both spellings
    (with and without the heading) must converge on the same result.
    """
    lines = section.split("\n")
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].strip() == heading.strip():
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        return "\n".join(lines[index:])
    return section


def _heading_level(line: str) -> int | None:
    """Return heading level (number of #) or None if not a heading."""
    m = re.match(r'^(#{1,6})\s', line.strip())
    return len(m.group(1)) if m else None


def _is_heading_of_level(line: str, max_level: int) -> bool:
    """Check if line is a heading at level <= max_level."""
    level = _heading_level(line)
    return level is not None and level <= max_level

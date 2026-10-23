#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_assemble_note.py — Assemble a literature note from multiple section files.

Reads YAML frontmatter from --yaml file and body sections from --sections
files, concatenates them into a complete note, and writes to the correct
Obsidian path. This avoids passing large note content through Bash heredocs.

Usage:
    python3 zotero_cli.py assemble-note KEY \
      --yaml /tmp/yaml.md \
      --sections /tmp/sec1.md /tmp/sec2.md /tmp/sec3.md \
      --force

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
from ..frontmatter_tags import ensure_literature_status_tag, note_filename_for_title
from ..link_sync import finalize_note_links


def Register(subparsers) -> None:
    """Register the 'assemble-note' subcommand."""
    p = subparsers.add_parser(
        "assemble-note",
        help="Assemble a note from YAML frontmatter + section files",
    )
    p.add_argument("key", help="Zotero item key (used to derive output path)")
    p.add_argument("--yaml", required=True, help="File containing YAML frontmatter block (--- ... ---)")
    p.add_argument("--sections", nargs="+", required=True, help="Section files to concatenate in order")
    p.add_argument("--force", action="store_true", help="Overwrite existing note")
    p.add_argument("--dry-run", action="store_true", help="Print path only, don't write")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute assemble-note. Reads --yaml + --sections, writes concatenated note."""
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

    # Derive note path (sanitiser shared with write-note/verify-note; this
    # command's private copy had drifted and omitted $ and ^).
    note_filename = note_filename_for_title(title)
    note_path = os.path.join(config.literature_notes, note_filename)

    # Check existing
    if os.path.isfile(note_path) and not args.force:
        print(f"[ERROR] Note already exists: {note_filename}", file=sys.stderr)
        print("  Use --force to overwrite.", file=sys.stderr)
        return 1

    # Validate YAML file
    if not os.path.isfile(args.yaml):
        print(f"[ERROR] YAML file not found: {args.yaml}", file=sys.stderr)
        return 1

    with open(args.yaml, "r", encoding="utf-8") as f:
        yaml_content = f.read().strip()
    if not yaml_content.startswith("---"):
        print("[ERROR] --yaml file must start with '---'", file=sys.stderr)
        return 1

    # Read section files
    section_contents = []
    for i, sec_file in enumerate(args.sections):
        if not os.path.isfile(sec_file):
            print(f"[ERROR] Section file not found: {sec_file}", file=sys.stderr)
            return 1
        with open(sec_file, "r", encoding="utf-8") as f:
            sec_text = f.read().strip()
        if not sec_text:
            print(f"[WARN] Section file is empty: {sec_file}", file=sys.stderr)
        section_contents.append(sec_text)

    if args.dry_run:
        print(note_path)
        print(f"  Sections: {len(section_contents)}")
        return 0

    # Assemble: YAML + blank line + sections joined by double newlines
    parts = [yaml_content, ""]
    parts.extend(section_contents)
    content = "\n\n".join(parts) + "\n"

    content = ensure_source_links(content, item.get("data", {}))
    existing_path = note_path if os.path.isfile(note_path) else None
    content = ensure_literature_status_tag(content, existing_path)
    # Inject zotero_key + ## 快捷链接, exactly as write-note does. The spec
    # routes long notes through assemble-note and tells the agent NOT to write
    # the quick links by hand, so skipping this left every long note failing
    # verify-note's zotero_key_present / quick_links_present checks.
    # Backlink sync stays off: it is a separate Zotero write.
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

    # Write atomically
    os.makedirs(os.path.dirname(note_path), exist_ok=True)
    tmp_path = note_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, note_path)

    print(f"[OK] Assembled: {note_path} ({len(content)} bytes, {len(section_contents)} sections)", file=sys.stderr)
    return 0

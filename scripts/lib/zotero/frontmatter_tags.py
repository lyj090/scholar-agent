#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frontmatter tag helpers for literature notes."""

from __future__ import annotations

import json
import os
import re

DEFAULT_STATUS_TAG = "status/to-read"

#: Characters Obsidian/Zotero note filenames must not contain. ``$`` and ``^``
#: are included because they are regex-special in Obsidian's search and broke
#: wikilinks before they were added.
_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*$^]')


def note_filename_for_title(title: str) -> str:
    """Derive the canonical note filename for a Zotero title.

    Single source for every reader and writer. They previously each carried a
    copy of the pattern and ``assemble-note`` had drifted (it omitted ``$`` and
    ``^``), so the same paper could map to two different filenames depending on
    which command wrote it.
    """
    return f"{_FILENAME_UNSAFE.sub('', title or '').strip()}.md"


def find_note_by_zotero_key(notes_dir: str, item_key: str) -> str | None:
    """Return the path of the note whose frontmatter declares ``item_key``.

    Filenames are derived from the *current* Zotero title, but notes on disk may
    have been written under an older title, an older sanitiser, or renamed in
    Obsidian. Keying off the frontmatter is the only durable link, and without
    it ``verify-note`` reported a missing file for real, healthy notes (15 of
    them in this vault, all of which keep ``:`` in the filename).
    """
    if not item_key or not os.path.isdir(notes_dir):
        return None
    for name in sorted(os.listdir(notes_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(notes_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            continue
        match = re.search(r"(?mi)^zotero_key:\s*(\S+)\s*$", content)
        if match and match.group(1) == item_key:
            return path
    return None


def resolve_note_path(notes_dir: str, title: str, item_key: str | None = None) -> str | None:
    """Resolve a note path, preferring the title-derived name but falling back
    to the frontmatter ``zotero_key``."""
    candidate = os.path.join(notes_dir, note_filename_for_title(title))
    if os.path.isfile(candidate):
        return candidate
    if item_key:
        return find_note_by_zotero_key(notes_dir, item_key)
    return None


def frontmatter_bounds(content: str) -> tuple[int, int] | None:
    """Locate the YAML frontmatter block, line-anchored.

    Returns ``(inner_start, close_start)``. ``inner_start`` is always 3 (just
    past the opening ``---``) so it matches the historical ``content[3:end]``
    convention, which keeps the newline that follows the opening fence.
    ``close_start`` is the offset of the line holding the closing ``---``.

    This exists because ``content.find("---", 3)`` (the previous approach) is
    not a delimiter search — it matches any ``---`` run, including one inside a
    quoted title such as ``title: "A --- B"``. In that case the block was cut
    mid-value and the rewritten frontmatter was silently corrupted. Anchoring
    the close delimiter to its own line is the only correct rule.
    """
    if not content.startswith("---"):
        return None

    first_nl = content.find("\n")
    if first_nl == -1 or content[:first_nl].strip() != "---":
        return None

    pos = first_nl + 1
    while pos <= len(content):
        nl = content.find("\n", pos)
        line_end = len(content) if nl == -1 else nl
        if content[pos:line_end].strip() == "---":
            return 3, pos
        if nl == -1:
            return None
        pos = nl + 1
    return None


def split_frontmatter(content: str) -> tuple[str, str] | None:
    """Split into ``(inner_yaml, tail)``.

    ``inner_yaml`` keeps the newline after the opening fence (``content[3:close]``)
    and ``tail`` starts at the closing ``---`` line, so the pair round-trips
    losslessly when rebuilt as ``"---" + inner_yaml + tail``.
    """
    bounds = frontmatter_bounds(content)
    if bounds is None:
        return None
    inner_start, close_start = bounds
    return content[inner_start:close_start], content[close_start:]


def find_status_tag(tags: list[str]) -> str | None:
    """Return the first status/* tag in the list, if any."""
    for tag in tags:
        if tag.startswith("status/"):
            return tag
    return None


def extract_frontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter as dict. Returns None if not found."""
    parsed = split_frontmatter(content)
    if parsed is None:
        return None
    yaml_str = parsed[0].strip()
    result: dict = {}
    current_key = ""
    current_list: list | None = None

    for line in yaml_str.split("\n"):
        list_match = re.match(r"^\s*-\s+(.+)$", line)
        if list_match and current_key:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(list_match.group(1).strip().strip("'\""))
            continue

        kv_match = re.match(r"^(\w+):\s*(.*)$", line)
        if kv_match:
            current_key = kv_match.group(1)
            value = kv_match.group(2).strip().strip("'\"")
            current_list = None
            if value:
                if value.startswith("["):
                    try:
                        result[current_key] = json.loads(value)
                    except json.JSONDecodeError:
                        result[current_key] = value
                else:
                    result[current_key] = value
            continue

    return result if result else None


def status_from_existing_file(path: str | None) -> str | None:
    """Read status/* from an existing note file, if present."""
    if not path or not os.path.isfile(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        frontmatter = extract_frontmatter(f.read())
    if not frontmatter:
        return None

    tags = frontmatter.get("tags")
    if not isinstance(tags, list):
        return None
    return find_status_tag(tags)


def ensure_tags_have_status(tags: list[str], existing_path: str | None = None) -> list[str]:
    """Ensure tags list contains one status/* tag when missing."""
    if find_status_tag(tags):
        return tags

    status = status_from_existing_file(existing_path) or DEFAULT_STATUS_TAG
    return tags + [status]


def _insert_tag_in_yaml_block(yaml_block: str, tag: str) -> str:
    """Insert a tag line into the tags: block of a YAML frontmatter string."""
    lines = yaml_block.split("\n")
    out: list[str] = []
    in_tags = False
    inserted = False
    tag_line = f"  - {tag}"

    for line in lines:
        stripped = line.strip()
        if re.match(r"^tags:\s*$", stripped):
            in_tags = True
            out.append(line)
            continue

        if re.match(r"^tags:\s*\[\s*\]\s*$", stripped):
            out.append("tags:")
            out.append(tag_line)
            inserted = True
            in_tags = False
            continue

        if in_tags:
            if re.match(r"^\s*-\s+", line):
                out.append(line)
                continue
            out.append(tag_line)
            inserted = True
            in_tags = False
            out.append(line)
            continue

        out.append(line)

    if in_tags and not inserted:
        out.append(tag_line)
        inserted = True

    if not inserted:
        out = ["tags:", tag_line, *out]

    return "\n".join(out)


def ensure_literature_status_tag(content: str, existing_path: str | None = None) -> str:
    """Ensure literature note frontmatter has status/* when missing."""
    parsed = split_frontmatter(content)
    if parsed is None:
        return content
    yaml_block, tail = parsed

    frontmatter = extract_frontmatter(content)
    if frontmatter is None:
        return content

    tags = frontmatter.get("tags")
    if not isinstance(tags, list):
        tags = []

    if find_status_tag(tags):
        return content

    status = status_from_existing_file(existing_path) or DEFAULT_STATUS_TAG
    new_yaml = _insert_tag_in_yaml_block(yaml_block, status)
    return "---" + new_yaml + tail

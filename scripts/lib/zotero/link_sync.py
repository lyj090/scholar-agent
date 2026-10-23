#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
link_sync.py — Obsidian ↔ Zotero bidirectional link helpers.

Inject quick-link blocks into literature notes and write Obsidian URIs
back to Zotero item Extra fields. Used by write-note, assemble-note, sync-links.

NOT responsible for: note body generation, tag validation.
"""

from __future__ import annotations

import os
import re
import sys
from typing import TYPE_CHECKING
from urllib.parse import quote

from .api import ZoteroAPI
from .frontmatter_tags import split_frontmatter
from .local_write import ZoteroWriteError, build_write_client

if TYPE_CHECKING:  # pragma: no cover
    from .config import ZoteroConfig

# ─── Constants ────────────────────────────────────────────────────────────────

kItemKeyPattern = re.compile(r"^[A-Z0-9]{8}$")
kQuickLinksHeader = "## 快捷链接"
#: Matches an existing block so it can be replaced. ``blank*`` tolerates a blank
#: line between the header and its bullets (seen in hand-edited notes) and
#: ``(?:\n|$)`` tolerates a block that ends the file with no trailing newline;
#: without either, the matcher left the stale block in place and appended a
#: second copy of the links.
kQuickLinksBlockPattern = re.compile(
    r"^## 快捷链接[ \t]*\n(?:[ \t]*\n)*(?:- .*(?:\n|$))*",
    re.MULTILINE,
)
kObsidianNotePrefix = "obsidian-note: "


# ─── Public API ───────────────────────────────────────────────────────────────


def is_valid_item_key(key: str | None) -> bool:
    """Return True if key looks like a Zotero 8-char item key."""
    return bool(key and kItemKeyPattern.match(key))


def resolve_pdf_key(item_key: str, api: ZoteroAPI) -> str | None:
    """Return the first PDF attachment key for a Zotero item."""
    try:
        children = api.get_children(item_key)
    except Exception:
        return None

    for child in children:
        data = child.get("data", {})
        if data.get("contentType") == "application/pdf":
            return child.get("key") or data.get("key")

    return None


def extract_pdf_key_from_content(content: str) -> str | None:
    """Read ``zotero_pdf_key`` from note frontmatter, if present and valid."""
    parsed = split_frontmatter(content)
    if parsed is None:
        return None
    for line in parsed[0].split("\n"):
        match = re.match(r"^zotero_pdf_key:\s*(\S+)\s*$", line.strip())
        if match and is_valid_item_key(match.group(1)):
            return match.group(1)
    return None


def build_quick_links(item_key: str, pdf_key: str | None) -> str:
    """Build the ## 快捷链接 markdown block."""
    lines = [
        kQuickLinksHeader,
        f"- [在 Zotero 中打开](zotero://select/library/items/{item_key})",
    ]
    if pdf_key:
        lines.append(f"- [打开 PDF](zotero://open-pdf/library/items/{pdf_key})")
    return "\n".join(lines)


def build_obsidian_uri(note_path: str, vault_root: str) -> str:
    """Build obsidian://open URI for a note relative to vault root."""
    vault_name = os.path.basename(os.path.abspath(vault_root))
    rel_path = os.path.relpath(os.path.abspath(note_path), os.path.abspath(vault_root))
    rel_posix = rel_path.replace(os.sep, "/")
    return (
        f"obsidian://open?vault={quote(vault_name)}"
        f"&file={quote(rel_posix, safe='')}"
    )


def ensure_frontmatter_zotero_keys(
    content: str,
    item_key: str,
    pdf_key: str | None,
) -> str:
    """Update YAML frontmatter with zotero_key / zotero_pdf_key; drop zotero_item_id.

    Idempotent: applying it to its own output is a byte-for-byte no-op.

    Two failure modes made the previous version non-idempotent and destructive:

    1. It split the block with ``content.find("---", 3)``, which matches a
       ``---`` inside a quoted value. The split is now line-anchored via
       :func:`split_frontmatter`.
    2. It preserved the blank line that follows the opening fence as a *list
       element*, then appended the new keys after it — so every call pushed one
       more blank line past the fence. Leading blank lines are now normalised
       away instead of being carried in the element list.

    ``pdf_key=None`` previously **deleted** an existing ``zotero_pdf_key``. A
    transient failure to read the attachment list (parent item trashed, API
    hiccup) would therefore silently strip a valid key. The existing value is
    now preserved when the caller has nothing better to offer.
    """
    parsed = split_frontmatter(content)
    if parsed is None:
        return content

    inner, tail = parsed

    # Locate the existing values before dropping, so a PDF key survives a
    # caller that could not resolve one.
    existing_pdf_key: str | None = None
    for line in inner.split("\n"):
        match = re.match(r"^zotero_pdf_key:\s*(\S+)\s*$", line.strip())
        if match and is_valid_item_key(match.group(1)):
            existing_pdf_key = match.group(1)
            break
    if pdf_key is None:
        pdf_key = existing_pdf_key

    drop_prefixes = ("zotero_item_id:", "zotero_key:", "zotero_pdf_key:")
    kept: list[str] = []
    for line in inner.split("\n"):
        stripped = line.strip()
        if any(stripped.startswith(p) for p in drop_prefixes):
            continue
        kept.append(line)

    # Normalise blank lines adjacent to the fences. The opening fence is
    # rebuilt as ``"---" + inner``, so ``inner`` begins with the newline that
    # terminates it; any *further* leading blanks are stray padding that used to
    # accumulate by one on every call.
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()

    kept.append(f"zotero_key: {item_key}")
    if pdf_key:
        kept.append(f"zotero_pdf_key: {pdf_key}")

    new_inner = "\n" + "\n".join(kept) + "\n"
    # ``tail`` begins at the closing fence, so rebuild the opening fence too.
    return f"---{new_inner}{tail}"


def inject_quick_links_block(content: str, block: str) -> str:
    """Insert or replace ## 快捷链接 immediately after frontmatter.

    Idempotent, and safe when the existing block is the last thing in the file
    (the previous ``(?:- .+\\n)*`` pattern required a trailing newline on every
    bullet, so a block at EOF left its final line behind and the rewrite
    duplicated links instead of replacing them).
    """
    parsed = split_frontmatter(content)
    if parsed is None:
        stripped = content.lstrip("\n")
        return f"{block}\n\n{stripped}" if stripped else f"{block}\n"

    inner, tail = parsed
    prefix = "---" + inner
    body = tail

    # Drop the closing fence, the old block, then re-emit both in order.
    body_lines = body.split("\n")
    body_rest = "\n".join(body_lines[1:]) if body_lines else ""
    body_rest = kQuickLinksBlockPattern.sub("", body_rest)
    body_rest = body_rest.lstrip("\n")

    closing = body_lines[0] if body_lines else "---"
    if re.search(r"^note_contract:\s*2\s*$", inner, re.MULTILINE) and body_rest.startswith("## 原文与代码"):
        following = re.search(r"\n##\s+", body_rest)
        end = following.start() if following else len(body_rest)
        sources, rest = body_rest[:end].rstrip(), body_rest[end:].lstrip()
        return f"{prefix}{closing}\n\n{sources}\n\n{block}\n\n{rest}".rstrip() + "\n"
    if body_rest:
        return f"{prefix}{closing}\n\n{block}\n\n{body_rest}"
    return f"{prefix}{closing}\n\n{block}\n"


def inject_quick_links(content: str, item_key: str, api: ZoteroAPI) -> str:
    """Ensure frontmatter keys + quick-link block are present and current.

    The PDF key is resolved once and reused for both the frontmatter and the
    link block, so the two can never disagree. When resolution fails (a trashed
    parent, a transient API error) the note's existing key is reused instead of
    silently dropping the PDF link — the note stays self-consistent.
    """
    pdf_key = resolve_pdf_key(item_key, api)
    if pdf_key is None:
        pdf_key = extract_pdf_key_from_content(content)
    content = ensure_frontmatter_zotero_keys(content, item_key, pdf_key)
    block = build_quick_links(item_key, pdf_key)
    return inject_quick_links_block(content, block)


def sync_zotero_backlink(
    item_key: str, obsidian_uri: str, *, config: "ZoteroConfig | None" = None
) -> bool:
    """Write an obsidian-note URI into the Zotero item's Extra field.

    Idempotent: an existing ``obsidian-note:`` line is replaced rather than
    appended, so re-syncing a moved note does not accumulate stale links.
    Returns True on success or if already current; False when Zotero is
    unreachable. Never raises — a broken backlink must not fail note writing.
    """
    from .config import load_config

    try:
        cfg = config or load_config()
        client = build_write_client(cfg)
        envelope = client.get_item(item_key)
        extra = envelope["data"].get("extra") or ""

        if obsidian_uri in extra:
            return True

        # Drop any previous obsidian-note line, then append the current one.
        kept = [
            line for line in extra.split("\n")
            if not line.strip().startswith(kObsidianNotePrefix.strip())
        ]
        body = "\n".join(kept).strip()
        updated = f"{body}\n{kObsidianNotePrefix}{obsidian_uri}" if body else (
            f"{kObsidianNotePrefix}{obsidian_uri}"
        )

        client.patch(item_key, {"extra": updated}, version=envelope["version"])
        return True
    except (ZoteroWriteError, OSError, ImportError) as exc:
        print(f"[WARN] Zotero backlink sync skipped: {exc}", file=sys.stderr)
        return False


def finalize_note_links(
    content: str,
    item_key: str,
    api: ZoteroAPI,
    note_path: str,
    vault_root: str,
    *,
    sync_backlink: bool = True,
) -> str:
    """Inject Obsidian quick links and optionally sync Zotero Extra backlink."""
    content = inject_quick_links(content, item_key, api)
    if sync_backlink:
        uri = build_obsidian_uri(note_path, vault_root)
        sync_zotero_backlink(item_key, uri)
    return content


def extract_zotero_key_from_content(content: str) -> str | None:
    """Read zotero_key from note frontmatter if present and valid."""
    parsed = split_frontmatter(content)
    if parsed is None:
        return None
    inner = parsed[0]
    for line in inner.split("\n"):
        match = re.match(r"^zotero_key:\s*(\S+)\s*$", line.strip())
        if match and is_valid_item_key(match.group(1)):
            return match.group(1)
    return None


def extract_title_from_content(content: str) -> str | None:
    """Read title from note frontmatter."""
    parsed = split_frontmatter(content)
    if parsed is None:
        return None
    for line in parsed[0].split("\n"):
        match = re.match(r'^title:\s*["\']?(.+?)["\']?\s*$', line.strip())
        if match:
            return match.group(1).strip()
    return None


def match_item_key_by_title(title: str, api: ZoteroAPI) -> str | None:
    """Search Zotero library by title; return item key on exact title match."""
    if not title:
        return None
    try:
        results = api.search(title, limit=5)
    except Exception:
        return None

    norm = title.strip().lower()
    for item in results:
        data = item.get("data", {})
        item_title = (data.get("title") or "").strip()
        key = item.get("key") or data.get("key")
        if item_title.lower() == norm and is_valid_item_key(key):
            return key
    return None

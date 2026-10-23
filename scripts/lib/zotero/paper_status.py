#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper preflight — decide whether a paper can be read locally, before the web.

The single most expensive habit this guards against: an agent holding an arXiv
ID reaches for ``web_fetch arxiv.org/abs/<id>`` when the paper is *already* in
the local library with a parsed MinerU cache. Nothing errors in that case, which
is why the habit survives; the same paper gets fetched repeatedly and each repeat
is a fresh chance to misread an extraction that was already correct.

This module classifies a reference into exactly one of four states so the
caller has an unambiguous next action:

    LOCAL_READY         parsed cache exists      → read locally, do NOT fetch
    LOCAL_UNPARSED      PDF exists, no cache     → parse locally, then read
    IN_LIBRARY_NO_PDF   metadata only            → find/attach the PDF
    ABSENT              not in library           → fetching the web is correct

NOT responsible for: HTTP transport, cache reading, parse execution.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .identifiers import PaperIdentifiers, extract_identifiers

if TYPE_CHECKING:
    from .config import ZoteroConfig
    from .mineru_catalog import MineruRecord

__all__ = ["PaperStatus", "PreflightResult", "preflight"]


LOCAL_READY = "LOCAL_READY"
LOCAL_UNPARSED = "LOCAL_UNPARSED"
IN_LIBRARY_NO_PDF = "IN_LIBRARY_NO_PDF"
ABSENT = "ABSENT"

# What the agent should do next, per state. Kept here so every surface
# (CLI, gate, docs) tells the same story.
_ACTION = {
    LOCAL_READY: "read local cache; do not fetch from the web",
    LOCAL_UNPARSED: "parse the local PDF, then read; web fetch still unnecessary",
    IN_LIBRARY_NO_PDF: "locate or attach the PDF, then parse",
    ABSENT: "fetch from the web, then import to the library",
}

# States where going to the web is the wrong move.
LOCAL_STATES = frozenset({LOCAL_READY, LOCAL_UNPARSED})


@dataclass
class PaperStatus:
    """Outcome of one preflight probe."""

    state: str
    reference: str
    identifiers: PaperIdentifiers
    item_key: str | None = None
    title: str | None = None
    cache_id: int | None = None
    attachment_key: str | None = None
    cache_path: str | None = None
    section_count: int | None = None
    pdf_attachment_keys: list[str] = field(default_factory=list)
    candidates: list[int] = field(default_factory=list)

    @property
    def is_local(self) -> bool:
        """True when the paper is readable without touching the network."""
        return self.state in LOCAL_STATES

    @property
    def action(self) -> str:
        return _ACTION.get(self.state, "")

    def to_dict(self) -> dict:
        data = {
            "state": self.state,
            "action": self.action,
            "reference": self.reference,
            "identifiers": self.identifiers.to_dict(),
            "item_key": self.item_key,
            "title": self.title,
            "cache_id": self.cache_id,
            "attachment_key": self.attachment_key,
            "cache_path": self.cache_path,
            "section_count": self.section_count,
            "pdf_attachment_keys": list(self.pdf_attachment_keys),
        }
        if self.candidates:
            data["candidate_cache_ids"] = list(self.candidates)
        return data


@dataclass
class PreflightResult:
    """Preflight output for one or more references."""

    statuses: list[PaperStatus]

    def to_dict(self) -> dict:
        return {
            "count": len(self.statuses),
            "local_count": sum(1 for s in self.statuses if s.is_local),
            "statuses": [s.to_dict() for s in self.statuses],
        }


def _records_for_entry(catalog, entry) -> list["MineruRecord"]:
    """All caches belonging to one library entry (by parent, then attachment)."""
    parent = (entry.item_key or "").upper()
    records = [
        record
        for record in catalog.records()
        if (record.parent_item_key or "").upper() == parent
    ]
    if records:
        return records
    wanted = {k.upper() for k in entry.pdf_attachment_keys}
    return [
        record
        for record in catalog.records()
        if (record.attachment_key or "").upper() in wanted
    ]


def _records_for_key(catalog, key: str) -> list["MineruRecord"]:
    """Caches matching a key directly (attachment key, parent key, or cache id).

    Used when the library index cannot resolve the reference — an attachment key
    is not a top-level item, so the index legitimately has nothing for it while
    the cache is right there on disk.
    """
    try:
        return catalog.candidates_for_key(key)
    except Exception:
        return []


def _status_for_key(
    reference: str, identifiers: PaperIdentifiers, records: list["MineruRecord"]
) -> PaperStatus:
    """Build a LOCAL_READY status for a reference resolved straight to caches."""
    record = max(records, key=lambda r: (r.cache_id,))
    return PaperStatus(
        state=LOCAL_READY,
        reference=reference,
        identifiers=identifiers,
        # A directly-resolved cache may belong to an attachment; report its
        # parent when known so callers can still address the paper as a whole.
        item_key=record.parent_item_key or record.attachment_key,
        title=record.title,
        cache_id=record.cache_id,
        attachment_key=record.attachment_key,
        cache_path=record.cache_dir,
        candidates=[r.cache_id for r in records] if len(records) > 1 else [],
    )


def _status_from_records(
    reference: str,
    identifiers: PaperIdentifiers,
    entry,
    records: list["MineruRecord"],
) -> PaperStatus:
    """Build a LOCAL_READY status, preferring the best record among duplicates."""
    if len(records) > 1:
        record = max(records, key=lambda r: (r.cache_id,))
    else:
        record = records[0]
    return PaperStatus(
        state=LOCAL_READY,
        reference=reference,
        identifiers=identifiers,
        item_key=entry.item_key,
        title=record.title or entry.title,
        cache_id=record.cache_id,
        attachment_key=record.attachment_key,
        cache_path=record.cache_dir,
        candidates=[r.cache_id for r in records] if len(records) > 1 else [],
    )


def preflight(
    config: "ZoteroConfig",
    references: list[str],
    *,
    catalog=None,
    index=None,
) -> PreflightResult:
    """Classify each reference into one of the four local-availability states.

    ``catalog`` and ``index`` may be injected to reuse a warm scan across many
    references; both are built lazily otherwise.
    """
    from .library_index import LibraryIndex, build_library_index
    from .mineru_catalog import MineruCatalog

    if catalog is None:
        from .api import ZoteroAPI

        api = ZoteroAPI(config.api_url, config.user_id)
        catalog = MineruCatalog(config.mineru_cache, api, config)
    if index is None:
        # The index is an *enhancement*: it maps arXiv/DOI/title references to
        # items. The MinerU caches themselves live on disk and resolve without
        # it. On a machine with no Zotero database and no API — a fresh clone
        # reading an exported cache, or CI — raising here would turn "I cannot
        # see a library" into "I cannot answer", and push the caller to the web
        # for a paper that is sitting on disk. Degrade to an empty index and let
        # the catalog answer; references that truly need the library still come
        # back ABSENT, which is the honest answer for them.
        try:
            index = build_library_index(config)
        except Exception as exc:  # noqa: BLE001 - any failure means "no library"
            print(
                f"[WARN] Library index unavailable ({exc}); "
                "answering from local caches only",
                file=sys.stderr,
            )
            index = LibraryIndex([])

    statuses: list[PaperStatus] = []
    for reference in references:
        identifiers = extract_identifiers(reference)

        entry = index.resolve(
            arxiv_id=identifiers.arxiv_id,
            doi=identifiers.doi,
            item_key=identifiers.zotero_key,
        )

        # A raw Zotero key or bare cache id can resolve without the index.
        if entry is None and identifiers.zotero_key:
            entry = index.by_key(identifiers.zotero_key)

        # The library index only holds *top-level* items, so an attachment key
        # (the PDF key a note records in its frontmatter, or a bare cache id)
        # never resolves there even when its cache exists on disk. Ask the
        # catalog directly before declaring the paper absent — otherwise the
        # same key that `mineru resolve` accepts is reported as ABSENT here,
        # which is exactly the signal that sends an agent to the web.
        lookup_key = identifiers.zotero_key
        if lookup_key is None and reference.strip().isdigit():
            # A bare MinerU cache id is a valid reference (it is what the cache
            # directory is named, and what `mineru list` prints).
            lookup_key = reference.strip()
        if entry is None and lookup_key:
            direct = _records_for_key(catalog, lookup_key)
            if direct:
                statuses.append(
                    _status_for_key(reference, identifiers, direct)
                )
                continue

        if entry is None:
            statuses.append(
                PaperStatus(
                    state=ABSENT,
                    reference=reference,
                    identifiers=identifiers,
                )
            )
            continue

        records = _records_for_entry(catalog, entry)
        if records:
            statuses.append(
                _status_from_records(reference, identifiers, entry, records)
            )
            continue

        state = (
            LOCAL_UNPARSED if entry.pdf_attachment_keys else IN_LIBRARY_NO_PDF
        )
        statuses.append(
            PaperStatus(
                state=state,
                reference=reference,
                identifiers=identifiers,
                item_key=entry.item_key,
                title=entry.title,
                pdf_attachment_keys=list(entry.pdf_attachment_keys),
            )
        )

    return PreflightResult(statuses)

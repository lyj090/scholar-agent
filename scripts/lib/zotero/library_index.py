#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Library index — resolve academic identifiers to Zotero items and caches.

Why this exists
---------------
The MinerU cache and the Zotero native API are keyed on Zotero *item keys*
(8-character strings). Agents hold *academic identifiers* (arXiv ID, DOI) or
URLs. Zotero's Local API cannot search those fields: ``q=2506.09384`` returns
zero hits even though the item's ``archiveID`` is ``arXiv:2506.09384``. That
mismatch is why agents fall back to fetching arXiv over the web.

This module builds the missing bridge: it scans the library once, extracts
identifiers from the metadata Zotero *does* expose (``archiveID``, ``DOI``,
``url``, ``extra``), and maps them to parent item keys and PDF attachments.

Cost
----
A full scan is cheap (tens of milliseconds for a few hundred items) and is cached
on disk keyed by Zotero's ``Last-Modified-Version`` header, so repeat calls are a
single HTTP request.

NOT responsible for: MinerU cache scanning, HTTP transport, CLI formatting.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .api import ZoteroAPI, ZoteroAPIError
from .identifiers import normalize_arxiv_id, normalize_doi

if TYPE_CHECKING:
    from .config import ZoteroConfig

__all__ = [
    "LibraryEntry",
    "LibraryIndex",
    "build_library_index",
    "current_library_version",
    "invalidate_library_index",
]

# Bump when the on-disk schema changes so stale caches are rebuilt. v2 began
# populating ``library_version`` (v1 always stored "") — old caches must be
# rebuilt once so the version check has a baseline to compare against.
INDEX_SCHEMA_VERSION = 2

_PDF_CONTENT_TYPE = "application/pdf"

# Metadata fields scanned for identifiers, in priority order.
_IDENTIFIER_FIELDS = ("archiveID", "DOI", "url", "extra", "archive")


def _default_index_path() -> str:
    # scripts/lib/zotero/library_index.py -> repo root is 4 levels up.
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    return os.path.join(root, "data", "zotero-library-index.json")


@dataclass
class LibraryEntry:
    """One top-level Zotero item, with its extractable identifiers."""

    item_key: str
    title: str
    item_type: str
    arxiv_id: str | None = None
    doi: str | None = None
    pdf_attachment_keys: list[str] = field(default_factory=list)
    version: int = 0

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdf_attachment_keys)

    def to_dict(self) -> dict:
        return {
            "item_key": self.item_key,
            "title": self.title,
            "item_type": self.item_type,
            "arxiv_id": self.arxiv_id,
            "doi": self.doi,
            "pdf_attachment_keys": list(self.pdf_attachment_keys),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LibraryEntry":
        return cls(
            item_key=str(data.get("item_key") or ""),
            title=str(data.get("title") or ""),
            item_type=str(data.get("item_type") or ""),
            arxiv_id=data.get("arxiv_id") or None,
            doi=data.get("doi") or None,
            pdf_attachment_keys=[
                str(k) for k in (data.get("pdf_attachment_keys") or [])
            ],
            version=int(data.get("version") or 0),
        )


def _extract_item_identifiers(data: dict) -> tuple[str | None, str | None]:
    """Return ``(arxiv_id, doi)`` found anywhere in an item's metadata."""
    arxiv_id: str | None = None
    doi: str | None = None

    for field_name in _IDENTIFIER_FIELDS:
        value = data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        if arxiv_id is None:
            arxiv_id = normalize_arxiv_id(value)
        if doi is None:
            doi = normalize_doi(value)

    # arXiv's own DOI is the most reliable signal for the arXiv ID.
    if arxiv_id is None and doi and doi.startswith("10.48550/arxiv."):
        arxiv_id = normalize_arxiv_id(doi)

    return arxiv_id, doi


class LibraryIndex:
    """Identifier → Zotero item lookup built from one library scan."""

    def __init__(self, entries: list[LibraryEntry], library_version: str = ""):
        self.entries = entries
        self.library_version = library_version
        self._by_arxiv: dict[str, LibraryEntry] = {}
        self._by_doi: dict[str, LibraryEntry] = {}
        self._by_key: dict[str, LibraryEntry] = {}
        self._by_attachment: dict[str, LibraryEntry] = {}
        for entry in entries:
            self._index_entry(entry)

    def _index_entry(self, entry: LibraryEntry) -> None:
        self._by_key[entry.item_key.upper()] = entry
        if entry.arxiv_id:
            self._by_arxiv.setdefault(entry.arxiv_id, entry)
        if entry.doi:
            self._by_doi.setdefault(entry.doi, entry)
        for attachment_key in entry.pdf_attachment_keys:
            self._by_attachment[attachment_key.upper()] = entry

    # -- lookups ---------------------------------------------------------

    def by_arxiv(self, arxiv_id: str | None) -> LibraryEntry | None:
        if not arxiv_id:
            return None
        return self._by_arxiv.get(arxiv_id)

    def by_doi(self, doi: str | None) -> LibraryEntry | None:
        if not doi:
            return None
        return self._by_doi.get(doi.lower())

    def by_key(self, key: str | None) -> LibraryEntry | None:
        if not key:
            return None
        return self._by_key.get(key.upper())

    def by_attachment(self, key: str | None) -> LibraryEntry | None:
        if not key:
            return None
        return self._by_attachment.get(key.upper())

    def resolve(
        self,
        *,
        arxiv_id: str | None = None,
        doi: str | None = None,
        item_key: str | None = None,
    ) -> LibraryEntry | None:
        """Best-effort identifier → entry resolution."""
        return (
            self.by_key(item_key)
            or self.by_arxiv(arxiv_id)
            or self.by_doi(doi)
        )

    def __len__(self) -> int:
        return len(self.entries)

    # -- persistence -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "library_version": self.library_version,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "entry_count": len(self.entries),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LibraryIndex":
        entries = [LibraryEntry.from_dict(row) for row in data.get("entries") or []]
        return cls(entries, library_version=str(data.get("library_version") or ""))

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "LibraryIndex | None":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("schema_version") != INDEX_SCHEMA_VERSION:
            return None
        try:
            return cls.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None


def _fetch_library(
    api: ZoteroAPI,
) -> tuple[list[LibraryEntry], dict[str, list[str]], str]:
    """Scan top-level items plus PDF children in two paged passes."""
    version = api.library_version()
    top_items = api.get_all("/items/top", params={"format": "json"})
    all_items = api.get_all("/items", params={"format": "json"})

    pdf_by_parent: dict[str, list[str]] = {}
    for item in all_items:
        data = item.get("data") or {}
        if data.get("itemType") != "attachment":
            continue
        if data.get("contentType") != _PDF_CONTENT_TYPE:
            continue
        parent = (data.get("parentItem") or "").upper()
        if not parent:
            continue
        child_key = (item.get("key") or "").upper()
        if child_key:
            pdf_by_parent.setdefault(parent, []).append(child_key)

    entries: list[LibraryEntry] = []
    for item in top_items:
        data = item.get("data") or {}
        item_key = (item.get("key") or "").upper()
        if not item_key:
            continue
        arxiv_id, doi = _extract_item_identifiers(data)
        entries.append(
            LibraryEntry(
                item_key=item_key,
                title=str(data.get("title") or ""),
                item_type=str(data.get("itemType") or ""),
                arxiv_id=arxiv_id,
                doi=doi,
                pdf_attachment_keys=pdf_by_parent.get(item_key, []),
                version=int(item.get("version") or 0),
            )
        )
    return entries, pdf_by_parent, version


def _fetch_library_offline(data_dir: str) -> tuple[list[LibraryEntry], dict, str]:
    """Build entries straight from ``zotero.sqlite`` — no Zotero process.

    This is the preferred source: it works with Zotero closed, is ~100x faster
    than HTTP, and is the only source that also exposes numeric item IDs. It also
    yields the library's version counter from the same consistent snapshot.
    """
    from .zotero_db import ZoteroDatabase

    with ZoteroDatabase(data_dir) as database:
        items, pdf_by_parent = database.fetch_library()
        version = database.library_version()

    entries: list[LibraryEntry] = []
    for item in items:
        fields = item.get("fields") or {}
        arxiv_id, doi = _extract_item_identifiers(fields)
        item_key = item["item_key"]
        entries.append(
            LibraryEntry(
                item_key=item_key,
                title=item.get("title") or "",
                item_type=item.get("item_type") or "",
                arxiv_id=arxiv_id,
                doi=doi,
                pdf_attachment_keys=pdf_by_parent.get(item_key, []),
                version=int(item.get("version") or 0),
            )
        )
    return entries, pdf_by_parent, version


def current_library_version(config: "ZoteroConfig") -> str:
    """The library's current change counter, or ``""`` when it cannot be read.

    Read from SQLite first (works with Zotero closed, ~10 ms) and only then from
    the HTTP API, mirroring :func:`build_library_index`'s source order so the
    version compared against a cached index comes from the same kind of place.
    """
    try:
        from .zotero_db import ZoteroDatabase

        with ZoteroDatabase(config.data_dir) as database:
            version = database.library_version()
        if version:
            return version
    except Exception:
        pass

    try:
        return ZoteroAPI(config.api_url, config.user_id).library_version()
    except Exception:
        return ""


def invalidate_library_index(cache_path: str | None = None) -> bool:
    """Drop the on-disk index so the next build re-scans.

    Called after a write that changes library structure (attaching a PDF,
    re-parenting children, rename/relink). Relying on the version check alone is
    not enough: a write made through a *different* Zotero profile or before the
    version was readable would leave the counter unchanged, and the caller knows
    better than the cache does.

    Returns True when a cache file was actually removed.
    """
    path = cache_path or _default_index_path()
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def build_library_index(
    config: "ZoteroConfig",
    *,
    cache_path: str | None = None,
    force: bool = False,
    max_age_seconds: float = 900.0,
    prefer_offline: bool = True,
) -> LibraryIndex:
    """Build (or reuse) the identifier index for the local Zotero library.

    Source order:
      1. A fresh on-disk index (TTL ``max_age_seconds`` **and** an unchanged
         library version).
      2. The SQLite database directly (no Zotero process needed).
      3. The Local HTTP API (requires Zotero running) — last resort.

    The HTTP fallback is kept only for a library whose database is unavailable
    (for example, a remote/headless profile).

    Cache freshness is deliberately two-dimensional. A TTL alone is wrong for
    this data: attaching a PDF does not change any *item the index already
    described* in a way a file-time check can see, so between the import and the
    TTL expiring the index kept reporting ``IN_LIBRARY_NO_PDF`` — the exact
    signal that sends an agent to the web for a paper it already owns. Comparing
    the stored library version against the current one makes a structural change
    visible immediately. When the version is unreadable on either side the TTL
    still governs, so the cache can never become permanently stale.
    """
    path = cache_path or _default_index_path()

    if not force:
        cached = LibraryIndex.load(path)
        if cached is not None:
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                age = float("inf")

            # The cache is reusable only when both conditions hold, and they are
            # checked separately because they fail for different reasons.
            #
            # Freshness: a TTL of 0 means "never reuse". `age` can be exactly 0.0
            # for a file written moments ago, and timestamp granularity differs
            # by platform (Windows is coarser than Linux), so `age <= 0` would
            # otherwise make 0 a no-op.
            fresh = max_age_seconds > 0 and age <= max_age_seconds

            # Version: an unchanged library version permits reuse regardless of
            # age, because the TTL exists to catch changes the version cannot
            # express. If the version moved, the cache is stale even when fresh.
            stored = cached.library_version
            current = current_library_version(config)
            version_ok = not stored or not current or stored == current

            if fresh and version_ok:
                return cached

    entries: list[LibraryEntry] | None = None
    version = ""
    if prefer_offline:
        try:
            entries, _pdf_by_parent, version = _fetch_library_offline(config.data_dir)
        except Exception:
            entries = None

    if entries is None:
        api = ZoteroAPI(config.api_url, config.user_id)
        try:
            entries, _pdf_by_parent, version = _fetch_library(api)
        except ZoteroAPIError:
            # Both sources failed: a stale index still beats no index.
            cached = LibraryIndex.load(path)
            if cached is not None:
                return cached
            raise

    index = LibraryIndex(entries, library_version=version)
    try:
        index.save(path)
    except OSError:
        pass
    return index

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mineru_lookup.py — thin lookup helpers over :mod:`zotero.mineru_catalog`.

Why this exists
---------------
``cache.py`` (``MineruCache``, 262 lines) predated the catalog and solved the
same problem twice: it kept its own ``.zotero_cli_index.json`` and talked to the
Local HTTP API for numeric IDs. ``MineruCatalog`` already does all of it, and
does it offline (filesystem + SQLite) and by more address forms (arXiv ID, DOI,
URL, title, item key).

What remained were two questions asked by callers that already had a *Zotero
key* and wanted a path, not a record:

    find_cache_dir(key)   -> the directory holding ``full.md``
    read_manifest(key)    -> the parsed ``manifest.json``

Both are four lines over the catalog. Keeping them here — rather than keeping a
second cache implementation alive — is what lets ``cache.py`` be deleted.

NOT responsible for: cache parsing, section spans, content search (all catalog).
"""

from __future__ import annotations

import json
import os

from .api import ZoteroAPI
from .config import ZoteroConfig
from .mineru_catalog import (
    MineruAmbiguousError,
    MineruCatalog,
    MineruNotFoundError,
)


class MineruLookup:
    """Resolve a Zotero key to its MinerU cache path on disk."""

    def __init__(self, config: ZoteroConfig, api: ZoteroAPI | None = None):
        self._catalog = MineruCatalog(
            config.mineru_cache,
            api or ZoteroAPI(config.api_url, config.user_id),
            config,
        )

    def find_cache_dir(self, item_key: str) -> str | None:
        """Return the cache directory for a Zotero item/attachment key."""
        if not item_key:
            return None
        try:
            return self._catalog.resolve(str(item_key).strip()).cache_dir
        except (MineruNotFoundError, MineruAmbiguousError, OSError):
            return None

    def read_manifest(self, item_key: str) -> dict | None:
        """Return the parsed ``manifest.json`` for a key, if present."""
        cache_dir = self.find_cache_dir(item_key)
        if not cache_dir:
            return None
        path = os.path.join(cache_dir, "manifest.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def read_full_md(self, item_key: str) -> str | None:
        """Return the full Markdown for a key, if present."""
        cache_dir = self.find_cache_dir(item_key)
        if not cache_dir:
            return None
        path = os.path.join(cache_dir, "full.md")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    def list_cached_ids(self) -> list[int]:
        """Every cache directory id present on disk."""
        return sorted(record.cache_id for record in self._catalog.records())

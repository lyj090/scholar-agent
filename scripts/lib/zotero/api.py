#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api.py — Zotero Local API client.

Handles HTTP requests to Zotero's local REST API, pagination, error mapping.
NOT responsible for: config loading, MinerU cache, output formatting.
"""

from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


_ZOTERO_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")
_DEFAULT_PAGE_SIZE = 100
_ERROR_BODY_LIMIT = 240


def normalize_key(value: str, label: str = "Zotero key") -> str:
    """Normalize and validate an 8-character Zotero item/collection key."""
    if not isinstance(value, str):
        raise ZoteroAPIError(f"{label} must be an 8-character Zotero key")
    key = value.strip().upper()
    if not _ZOTERO_KEY_RE.fullmatch(key):
        raise ZoteroAPIError(f"Invalid {label}: {value!r}")
    return key


def _validate_limit(value: int, label: str = "limit") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ZoteroAPIError(f"{label} must be a positive integer")
    return value


def _error_body(error: HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    if len(body) > _ERROR_BODY_LIMIT:
        body = body[:_ERROR_BODY_LIMIT].rstrip() + "..."
    return body


class ZoteroAPIError(Exception):
    """Raised when Zotero API returns an error or is unreachable."""

    pass


class ZoteroAPI:
    """Lightweight client for Zotero Local API (http://localhost:23119)."""

    def __init__(self, base_url: str, user_id: str, *, timeout: float = 10):
        base = str(base_url or "").strip().rstrip("/")
        if not re.match(r"^https?://", base, re.IGNORECASE):
            raise ZoteroAPIError(f"Invalid Zotero API URL: {base_url!r}")
        user = str(user_id or "").strip()
        if not user:
            raise ZoteroAPIError("Zotero user ID is required")
        if timeout <= 0:
            raise ZoteroAPIError("Zotero API timeout must be positive")
        self._base = f"{base}/api/users/{quote(user, safe='')}"
        self._timeout = timeout

    # ------------------------------------------------------------------
    #  Connection check
    # ------------------------------------------------------------------

    def check_connection(self) -> bool:
        """Return True if Zotero is reachable."""
        try:
            self._get("/items", params={"limit": "1"})
            return True
        except ZoteroAPIError:
            return False

    def library_version(self) -> str:
        """The library's ``Last-Modified-Version``, or ``""`` when unknown.

        This is the change signal for cached views built over HTTP: Zotero bumps
        it on every committed write, so a cache can compare it against the value
        it was built from instead of guessing from a TTL alone.
        """
        url = f"{self._base}/items?{urlencode({'limit': '1', 'format': 'json'})}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return str(response.headers.get("Last-Modified-Version") or "")
        except (HTTPError, URLError, TimeoutError, OSError):
            return ""

    # ------------------------------------------------------------------
    #  Item queries
    # ------------------------------------------------------------------

    def get_items(
        self,
        limit: int = 25,
        item_type: str | None = None,
        collection: str | None = None,
        sort: str = "dateAdded",
        direction: str = "desc",
    ) -> list[dict]:
        """
        Fetch top-level items (excludes child attachments by default).

        Args:
            limit: Max items to return.
            item_type: Filter by itemType (e.g. "journalArticle", "preprint").
            collection: Collection key to scope the query.
            sort: Sort field (dateAdded, dateModified, title).
            direction: "asc" or "desc".
        """
        limit = _validate_limit(limit)
        if collection:
            collection = normalize_key(collection, "collection key")

        params = {
            "sort": sort,
            "direction": direction,
            "format": "json",
        }
        if item_type:
            params["itemType"] = item_type

        if collection:
            path = f"/collections/{collection}/items/top"
        else:
            path = "/items/top"

        if limit <= _DEFAULT_PAGE_SIZE:
            params["limit"] = str(limit)
            return self._get_list(path, params=params)
        return self.get_all(path, params=params, max_items=limit)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search across library."""
        query = str(query or "").strip()
        if not query:
            raise ZoteroAPIError("Search query must not be empty")
        limit = _validate_limit(limit)
        params = {
            "q": query,
            "sort": "dateAdded",
            "direction": "desc",
            "format": "json",
            "itemType": "-attachment",
        }
        if limit <= _DEFAULT_PAGE_SIZE:
            params["limit"] = str(limit)
            return self._get_list("/items", params=params)
        return self.get_all("/items", params=params, max_items=limit)

    def get_item(self, key: str) -> dict:
        """Get a single item by key."""
        key = normalize_key(key, "item key")
        result = self._get(f"/items/{key}")
        if not isinstance(result, dict):
            raise ZoteroAPIError(f"Unexpected response for item {key}")
        return result

    def get_children(self, key: str) -> list[dict]:
        """Get all child items (attachments, notes) of an item."""
        key = normalize_key(key, "item key")
        result = self._get(f"/items/{key}/children", params={"format": "json"})
        if not isinstance(result, list):
            raise ZoteroAPIError(f"Unexpected children response for item {key}")
        return result

    def get_collections(self) -> list[dict]:
        """Fetch all collections through the public paginated API."""
        return self.get_all("/collections")

    def _get_list(self, path: str, params: dict | None = None) -> list[dict]:
        result = self._get(path, params=params)
        if not isinstance(result, list):
            raise ZoteroAPIError(f"Expected a list response from {path}")
        return result

    def get_all(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
        max_items: int | None = None,
    ) -> list[dict]:
        """Fetch a list endpoint page by page, with a bounded optional limit."""
        if not path.startswith("/"):
            raise ZoteroAPIError(f"API path must start with '/': {path!r}")
        page_size = _validate_limit(page_size, "page_size")
        if max_items is not None:
            max_items = _validate_limit(max_items, "max_items")

        rows: list[dict] = []
        start = 0
        while True:
            query = dict(params or {})
            request_limit = page_size
            if max_items is not None:
                request_limit = min(page_size, max_items - len(rows))
                if request_limit <= 0:
                    break
            query.update({"limit": str(request_limit), "start": str(start), "format": "json"})
            page = self._get(path, params=query)
            if not isinstance(page, list):
                raise ZoteroAPIError(f"Unexpected list response from {path}")
            rows.extend(page)
            if not page or len(page) < request_limit:
                break
            if max_items is not None and len(rows) >= max_items:
                break
            start += len(page)

        return rows[:max_items] if max_items is not None else rows

    # ------------------------------------------------------------------
    #  Internal HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        """Make a GET request, return parsed JSON."""
        if not path.startswith("/"):
            raise ZoteroAPIError(f"API path must start with '/': {path!r}")
        url = self._base + path
        if params:
            url += f"?{urlencode(params)}"

        req = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as e:
            detail = _error_body(e)
            suffix = f": {detail}" if detail else ""
            raise ZoteroAPIError(f"HTTP {e.code}: {url}{suffix}") from e
        except URLError as e:
            raise ZoteroAPIError(
                f"Cannot connect to Zotero at {self._base}. "
                f"Is Zotero running? ({e.reason})"
            ) from e
        except (TimeoutError, OSError) as e:
            raise ZoteroAPIError(f"Zotero request failed: {e}") from e

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ZoteroAPIError(f"Zotero returned invalid JSON: {e.msg}") from e
        if not isinstance(result, (dict, list)):
            raise ZoteroAPIError("Zotero returned an unexpected JSON value")
        return result

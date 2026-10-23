#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline Zotero database reader — no Zotero process, no HTTP, no plugin.

Why this exists
---------------
The read path used to require Zotero to be *running*, because library metadata
came from the Local HTTP API (``localhost:23119``). Measured: with the API down,
``paper-status`` fails outright — even though every field it needs already sits
in ``zotero.sqlite`` on disk.

Zotero keeps the DB open with a live WAL, so we copy the file (plus ``-wal`` and
``-shm``) and open the copy ``mode=ro``. The original is never written.

What SQLite gives us that the HTTP API does not
-----------------------------------------------
* **Numeric item IDs.** The HTTP API deliberately omits them, yet the MinerU
  cache directory is named after ``item.id``.
* **Speed.** Full metadata extraction for 750 items is ~3 ms, versus ~250 ms
  over HTTP.
* **Availability.** Works with Zotero closed, and inside scripts/CI.

NOT responsible for: HTTP fallback, cache parsing, MinerU calls, CLI output.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass

__all__ = [
    "ZoteroDatabase",
    "ZoteroDbError",
    "copy_database",
    "NON_ITEM_TYPES",
]


class ZoteroDbError(Exception):
    """Raised when the Zotero database cannot be read."""


# Types that are not standalone bibliographic items.
NON_ITEM_TYPES = ("attachment", "note", "annotation")

# Fields scanned for academic identifiers, in priority order.
_IDENTIFIER_FIELDS = ("title", "DOI", "url", "archiveID", "extra", "archive")

_PDF_CONTENT_TYPE = "application/pdf"


def copy_database(data_dir: str) -> tuple[str, str]:
    """Copy ``zotero.sqlite`` (+WAL sidecars) to a temp dir.

    Returns ``(db_path, temp_dir)``. The caller owns ``temp_dir``. Copying is
    necessary because Zotero holds the live DB open; reading it in place can
    fail on locks or silently miss WAL content.
    """
    source = os.path.join(os.path.abspath(os.path.expanduser(data_dir)), "zotero.sqlite")
    if not os.path.isfile(source):
        raise ZoteroDbError(f"zotero.sqlite not found at {source}")

    temp_dir = tempfile.mkdtemp(prefix="zotero-db-")
    target = os.path.join(temp_dir, "zotero.sqlite")
    for suffix in ("", "-wal", "-shm"):
        sidecar = source + suffix
        if os.path.isfile(sidecar):
            try:
                shutil.copy2(sidecar, target + suffix)
            except OSError as exc:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise ZoteroDbError(f"cannot copy {sidecar}: {exc}") from exc
    return target, temp_dir


@dataclass(frozen=True)
class ItemLocation:
    """One item's identity in both key and numeric spaces."""

    item_id: int
    item_key: str
    parent_item_id: int | None = None
    parent_item_key: str | None = None
    library_id: int | None = None


class ZoteroDatabase:
    """Read-only view of a Zotero library, usable with Zotero closed."""

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(os.path.expanduser(data_dir))
        self._conn: sqlite3.Connection | None = None
        self._temp_dir: str | None = None
        self._type_names: dict[int, str] | None = None

    # -- lifecycle -------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        db_path, temp_dir = copy_database(self.data_dir)
        self._temp_dir = temp_dir
        try:
            self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ZoteroDbError(f"cannot open Zotero DB copy: {exc}") from exc
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    def __enter__(self) -> "ZoteroDatabase":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- helpers ---------------------------------------------------------

    def _table_exists(self, name: str) -> bool:
        row = self.connect().execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _item_types(self) -> dict[int, str]:
        if self._type_names is None:
            self._type_names = {
                int(row[0]): str(row[1])
                for row in self.connect().execute(
                    "SELECT itemTypeID, typeName FROM itemTypes"
                )
            }
        return self._type_names

    def _deleted_clause(self, alias: str = "i") -> str:
        """SQL fragment excluding trashed items, when the table exists."""
        if self._table_exists("deletedItems"):
            return (
                f" AND {alias}.itemID NOT IN (SELECT itemID FROM deletedItems)"
            )
        return ""

    def library_version(self) -> str:
        """Monotonic version counter for the user library, as a string.

        Zotero bumps ``libraries.version`` on every committed write, which makes
        it the cheap "has anything changed?" signal a cached view needs. It is
        read straight from SQLite so it stays correct with Zotero closed.

        Returns ``""`` when the table or row is unavailable — callers must treat
        that as "unknown", never as "unchanged", or a cache would never expire.
        A missing or unreadable database is exactly such a case: this is a probe,
        so it reports "unknown" instead of raising.
        """
        try:
            rows = self.connect().execute(
                "SELECT version FROM libraries WHERE type = 'user' ORDER BY libraryID"
            ).fetchall()
        except (sqlite3.Error, ZoteroDbError, OSError):
            return ""
        for row in rows:
            if row and row[0] is not None:
                return str(row[0])
        return ""

    # -- library scan ----------------------------------------------------

    def fetch_library(self) -> tuple[list[dict], dict[str, list[str]]]:
        """Return ``(items, pdf_by_parent_key)`` for the whole library.

        Each item dict carries ``item_key``, ``item_id``, ``title``,
        ``item_type``, ``version`` and ``fields`` (the raw identifier-bearing
        columns), so callers can extract arXiv IDs/DOIs without more SQL.
        """
        connection = self.connect()
        types = self._item_types()
        placeholders = ",".join("?" for _ in NON_ITEM_TYPES)

        rows = list(
            connection.execute(
                f"""
                SELECT i.itemID, i.key, i.itemTypeID, i.version, i.libraryID
                FROM items i
                WHERE i.itemTypeID NOT IN (
                    SELECT itemTypeID FROM itemTypes WHERE typeName IN ({placeholders})
                )
                {self._deleted_clause('i')}
                """,
                NON_ITEM_TYPES,
            )
        )

        items: dict[str, dict] = {}
        for item_id, key, type_id, version, library_id in rows:
            items[str(key).upper()] = {
                "item_key": str(key).upper(),
                "item_id": int(item_id),
                "item_type": types.get(int(type_id), ""),
                "version": int(version or 0),
                "library_id": int(library_id) if library_id is not None else None,
                "title": "",
                "fields": {},
            }

        if items:
            field_placeholders = ",".join("?" for _ in _IDENTIFIER_FIELDS)
            for key, field_name, value in connection.execute(
                f"""
                SELECT i.key, f.fieldName, v.value
                FROM itemData d
                JOIN items i ON i.itemID = d.itemID
                JOIN fields f ON f.fieldID = d.fieldID
                JOIN itemDataValues v ON v.valueID = d.valueID
                WHERE f.fieldName IN ({field_placeholders})
                """,
                _IDENTIFIER_FIELDS,
            ):
                entry = items.get(str(key).upper())
                if entry is None or value is None:
                    continue
                entry["fields"][str(field_name)] = str(value)
                if field_name == "title":
                    entry["title"] = str(value)

        pdf_by_parent: dict[str, list[str]] = {}
        for item_key, parent_key, content_type in connection.execute(
            f"""
            SELECT i.key, p.key, a.contentType
            FROM itemAttachments a
            JOIN items i ON i.itemID = a.itemID
            LEFT JOIN items p ON p.itemID = a.parentItemID
            WHERE a.contentType = ?
            {self._deleted_clause('i')}
            """,
            (_PDF_CONTENT_TYPE,),
        ):
            if not parent_key:
                continue
            pdf_by_parent.setdefault(str(parent_key).upper(), []).append(
                str(item_key).upper()
            )

        return list(items.values()), pdf_by_parent

    # -- lookups ---------------------------------------------------------

    def key_to_id(self, key: str) -> int | None:
        row = self.connect().execute(
            "SELECT itemID FROM items WHERE key = ?", (key.strip().upper(),)
        ).fetchone()
        return int(row[0]) if row else None

    def id_to_key(self, item_id: int) -> str | None:
        row = self.connect().execute(
            "SELECT key FROM items WHERE itemID = ?", (int(item_id),)
        ).fetchone()
        return str(row[0]) if row else None

    def locate(self, key: str) -> ItemLocation | None:
        row = self.connect().execute(
            """
            SELECT i.itemID, i.key, a.parentItemID, p.key, i.libraryID
            FROM items i
            LEFT JOIN itemAttachments a ON a.itemID = i.itemID
            LEFT JOIN items p ON p.itemID = a.parentItemID
            WHERE i.key = ?
            """,
            (key.strip().upper(),),
        ).fetchone()
        if row is None:
            return None
        return ItemLocation(
            item_id=int(row[0]),
            item_key=str(row[1]),
            parent_item_id=int(row[2]) if row[2] is not None else None,
            parent_item_key=str(row[3]) if row[3] else None,
            library_id=int(row[4]) if row[4] is not None else None,
        )

    def all_pdf_attachments(self) -> list[dict]:
        """Every live PDF attachment, with its parent and on-disk path.

        Used by the auto-parse watcher: it needs the same "which PDFs exist"
        view the plugin got from Zotero's notifier, but without an in-process
        hook. Returns newest-first so a bounded run handles recent imports first.
        """
        rows = self.connect().execute(
            f"""
            SELECT i.itemID, i.key, a.parentItemID, p.key,
                   (SELECT v.value FROM itemData d
                      JOIN itemDataValues v ON v.valueID = d.valueID
                      JOIN fields f ON f.fieldID = d.fieldID
                     WHERE d.itemID = COALESCE(a.parentItemID, i.itemID)
                       AND f.fieldName = 'title'),
                   i.dateAdded,
                   pit.typeName
            FROM items i
            JOIN itemAttachments a ON a.itemID = i.itemID
            LEFT JOIN items p ON p.itemID = a.parentItemID
            LEFT JOIN itemTypes pit ON pit.itemTypeID = p.itemTypeID
            WHERE a.contentType = 'application/pdf'
              {self._deleted_clause('i')}
            ORDER BY i.dateAdded DESC, i.itemID DESC
            """
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            key = str(row[1])
            out.append({
                "item_id": int(row[0]),
                "attachment_key": key,
                "parent_item_id": int(row[2]) if row[2] is not None else None,
                "parent_item_key": str(row[3]) if row[3] else None,
                "title": str(row[4]) if row[4] else "",
                "date_added": str(row[5]) if row[5] else "",
                # The parent's item type is the signal for "is this a paper" —
                # curated metadata, unlike guessing from the filename.
                "item_type": str(row[6]) if row[6] else None,
                "pdf_path": self.storage_path(key),
            })
        return out

    def storage_path(self, key: str) -> str | None:
        """Absolute path of a stored attachment, or None if not present."""
        row = self.connect().execute(
            """
            SELECT a.path
            FROM itemAttachments a
            JOIN items i ON i.itemID = a.itemID
            WHERE i.key = ?
            """,
            (key.strip().upper(),),
        ).fetchone()
        if row is None or not row[0]:
            return None
        raw_path = str(row[0])
        item_key = key.strip().upper()

        if raw_path.startswith("storage:"):
            return os.path.join(
                self.data_dir, "storage", item_key, raw_path[len("storage:") :]
            )
        if raw_path.startswith("attachments:"):
            return os.path.join(
                self.data_dir, "storage", raw_path[len("attachments:") :]
            )
        if os.path.isabs(raw_path):
            return raw_path
        return os.path.join(self.data_dir, "storage", item_key, raw_path)

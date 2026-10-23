#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
local_write.py — Zotero 10+ local write client (no plugin, no in-process carrier).

Why this exists
---------------
Writes used to require the llm-for-zotero plugin's in-process JS bridge, which
meant a token from ``prefs.js``, version drift against Zotero, and a 328k-line
upstream to keep alive. Zotero 10 added native write endpoints, so the write
path can now be plain HTTP like the read path.

This module is a *client*, not a carrier: it injects nothing into Zotero's
process. That is the structural difference from the plugin, not a matter of
degree.

What it adds over calling the API directly
------------------------------------------
The API guarantees protocol correctness, not data safety. Three measured traps
are neutralised here so a caller cannot hit them by accident:

1. **Array fields overwrite, they do not merge.** ``PATCH {"tags":[{"tag":"x"}]}``
   returns ``204`` while silently dropping every tag the item already had.
   Measured on this machine: 160 library items carry >=3 tags, so a naive batch
   tagger is a data-loss tool. :meth:`add_tags` reads, merges, and writes back.
2. **``DELETE`` is permanent.** It erases the row outright and never touches the
   trash (measured: ``items`` 987 -> 986 while ``deletedItems`` stayed at 43).
   This module exposes only :meth:`trash` (``PATCH {"deleted": 1}``); raw delete
   requires an explicit ``permanent=True`` opt-in.
3. **Every update needs a version precondition.** Omitting it returns ``428``;
   supplying a stale one returns ``412``. :meth:`patch` carries the version and
   retries on conflict rather than leaving the caller to reinvent it.

NOT responsible for: MinerU parsing, read indexing, ledger/undo (see
``action_ledger.py``).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import quote

_ZOTERO_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")

# The local API rejects writes without this header (428 Precondition Required).
SERVER_ID_HEADER = "Zotero-Server-ID"
API_KEY_HEADER = "Zotero-API-Key"
VERSION_HEADER = "If-Unmodified-Since-Version"

# Upload authorization keys expire after an hour; stay well inside that.
_UPLOAD_TIMEOUT = 300


class ZoteroWriteError(RuntimeError):
    """Raised when a local write cannot be completed safely."""


class ZoteroAuthRequired(ZoteroWriteError):
    """No remembered local API key available — the user must authorize once.

    Zotero issues these keys through a dialog and stores them in
    ``localAPIKeys.json`` under the Firefox profile. A key granted with
    "Always Allow" (``remember: true``) is reusable indefinitely; a single-use
    key is consumed by the first successful write, after which the user has to
    approve again.
    """


class ZoteroConflictError(ZoteroWriteError):
    """The item changed underneath us and retries were exhausted."""


def normalize_key(value: str, label: str = "Zotero key") -> str:
    """Normalize and validate an 8-character Zotero object key."""
    if not isinstance(value, str):
        raise ZoteroWriteError(f"{label} must be an 8-character Zotero key")
    key = value.strip().upper()
    if not _ZOTERO_KEY_RE.fullmatch(key):
        raise ZoteroWriteError(f"Invalid {label}: {value!r}")
    return key


def find_remembered_key() -> str | None:
    """Return a reusable local API key, or None if none was ever granted.

    Keys live beside the Firefox profile, not in ``prefs.js``:
    ``~/.zotero/zotero/<profile>/localAPIKeys.json``.
    """
    pattern = os.path.expanduser("~/.zotero/zotero/*/localAPIKeys.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        for entry in (data or {}).get("keys", []) or []:
            key = str(entry.get("key") or "").strip()
            if key and entry.get("remember"):
                return key
    return None


def _mtime_ms(path: str) -> int:
    return int(os.path.getmtime(path) * 1000)


def _md5_file(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ZoteroWriteClient:
    """Write client for the Zotero 10+ local API.

    ``api_url``/``user_id`` mirror :class:`zotero.api.ZoteroAPI` so both can be
    built from the same :class:`zotero.config.ZoteroConfig`.
    """

    def __init__(
        self,
        api_url: str,
        user_id: str,
        *,
        api_key: str | None = None,
        timeout: float = 15.0,
        data_dir: str | None = None,
    ):
        base = str(api_url or "").strip().rstrip("/")
        if not re.match(r"^https?://", base, re.IGNORECASE):
            raise ZoteroWriteError(f"Invalid Zotero API URL: {api_url!r}")
        user = str(user_id or "").strip()
        if not user:
            raise ZoteroWriteError("Zotero user ID is required")
        self._base = f"{base}/api/users/{quote(user, safe='')}"
        self._root = base
        self._timeout = timeout
        self._api_key = api_key
        self._server_id: str | None = None
        #: Needed only by operations that must keep the database and the files
        #: on disk in agreement (attachment rename). Optional so a caller that
        #: only does metadata writes needs no knowledge of the data directory.
        self._data_dir = (
            os.path.abspath(os.path.expanduser(data_dir)) if data_dir else None
        )

    # ------------------------------------------------------------------
    #  Discovery and auth
    # ------------------------------------------------------------------

    @property
    def api_key(self) -> str:
        """The local API key, discovered lazily from the profile if not given."""
        if not self._api_key:
            self._api_key = find_remembered_key()
        if not self._api_key:
            raise ZoteroAuthRequired(
                "No remembered Zotero local API key found. Authorize once:\n"
                "  curl -X POST -H 'Content-Type: application/json' \\\n"
                "       -H 'Zotero-Server-ID: <id>' \\\n"
                "       -d '{\"appName\":\"scholar-agent\"}' \\\n"
                "       http://127.0.0.1:23119/api/local/authorize\n"
                "Choose \"Always Allow\" so the key is reusable."
            )
        return self._api_key

    def server_id(self) -> str:
        """Return the running instance's Server-ID, cached for this client.

        Zotero requires it on every write and rejects a mismatched one with 412,
        because object versions are only meaningful within one database. A bare
        ``GET /api/`` is enough to read it.
        """
        if self._server_id:
            return self._server_id
        try:
            with urllib.request.urlopen(
                f"{self._root}/api/", timeout=self._timeout
            ) as resp:
                server_id = resp.headers.get(SERVER_ID_HEADER)
        except (urllib.error.URLError, OSError) as error:
            raise ZoteroWriteError(
                f"Cannot reach Zotero at {self._root}. Is it running? ({error})"
            ) from error
        if not server_id:
            raise ZoteroWriteError(
                "Zotero did not send a Zotero-Server-ID header. "
                "Native writes require Zotero 10 or newer."
            )
        self._server_id = server_id
        return server_id

    def check_writable(self) -> bool:
        """True when both the Server-ID and a reusable key are available."""
        try:
            self.server_id()
            self.api_key
            return True
        except ZoteroWriteError:
            return False

    # ------------------------------------------------------------------
    #  Internal HTTP
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body=None,
        version=None,
        raw: bytes | None = None,
        content_type: str = "application/json",
        absolute_url: str | None = None,
        auth: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, object, dict]:
        """Issue one request; return ``(status, parsed_body, headers)``."""
        url = absolute_url or f"{self._base}{path}"
        data: bytes | None
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
        else:
            data = None

        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", content_type)
        if auth:
            request.add_header(SERVER_ID_HEADER, self.server_id())
            request.add_header(API_KEY_HEADER, self.api_key)
        if version is not None:
            request.add_header(VERSION_HEADER, str(version))
        for name, value in (extra_headers or {}).items():
            request.add_header(name, value)

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                payload = resp.read()
                headers = dict(resp.headers.items())
                status = resp.status
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")[:300].strip()
            except Exception:  # pragma: no cover - body already consumed
                pass
            return error.code, detail, {}
        except (urllib.error.URLError, OSError) as error:
            raise ZoteroWriteError(
                f"Cannot reach Zotero at {self._root}. Is it running? ({error})"
            ) from error

        parsed: object = None
        if payload:
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = payload.decode("utf-8", "replace")
        return status, parsed, headers

    @staticmethod
    def _raise_for_status(status: int, detail: object, action: str) -> None:
        if status in (200, 201, 204):
            return
        if status == 401:
            raise ZoteroAuthRequired(
                f"{action} rejected: API key missing, expired, or already used. "
                "Re-authorize (POST /api/local/authorize); use \"Always Allow\" "
                "for a reusable key."
            )
        if status == 428:
            raise ZoteroWriteError(
                f"{action} rejected (428): Zotero demanded a precondition — "
                f"{detail}"
            )
        if status == 412:
            raise ZoteroConflictError(f"{action} rejected (412): {detail}")
        raise ZoteroWriteError(f"{action} failed (HTTP {status}): {detail}")

    # ------------------------------------------------------------------
    #  Reads
    # ------------------------------------------------------------------

    def get_item(self, key: str) -> dict:
        """Return the item envelope (``data``/``version``/``key``)."""
        key = normalize_key(key)
        status, payload, _ = self._request("GET", f"/items/{key}", auth=True)
        self._raise_for_status(status, payload, f"GET item {key}")
        if not isinstance(payload, dict):
            raise ZoteroWriteError(f"Unexpected item payload for {key}")
        return payload

    def item_version(self, key: str) -> int:
        return int(self.get_item(key)["version"])

    # ------------------------------------------------------------------
    #  Writes
    # ------------------------------------------------------------------

    def create_items(self, items: list[dict]) -> list[str]:
        """Create items (max 50 per request); return the new keys in order."""
        if not items:
            return []
        if len(items) > 50:
            raise ZoteroWriteError("Zotero accepts at most 50 objects per request")
        status, payload, _ = self._request("POST", "/items", body=items)
        self._raise_for_status(status, payload, "create items")
        result = payload if isinstance(payload, dict) else {}
        created = result.get("successful") or {}
        failed = result.get("failed") or {}
        if failed:
            first = next(iter(failed.values()))
            raise ZoteroWriteError(
                f"Zotero rejected item {first.get('key')!r}: "
                f"{first.get('message')}"
            )
        # Indexes are the positions in the uploaded array.
        return [created[str(i)]["key"] for i in range(len(items)) if str(i) in created]

    def patch(
        self,
        key: str,
        fields: dict,
        *,
        version: int | None = None,
        retries: int = 3,
    ) -> None:
        """Partially update an item, carrying the version precondition.

        On 412 the item is re-read and the write replayed, because the caller's
        intended *field values* are still valid even when the version moved.

        Array fields (``tags``, ``collections``, ``relations``) are complete
        replacements — use :meth:`add_tags` / :meth:`add_to_collection` when the
        intent is to add rather than to set.

        Deliberately returns nothing: a successful PATCH yields only ``204``
        with no body, and item versions are *not* simply ``version + 1`` (they
        are allocated per library transaction). Callers that need the new version
        must re-read. Fabricating one here would hand out a version that fails
        the next precondition check.
        """
        key = normalize_key(key)
        if not fields:
            raise ZoteroWriteError("patch() needs at least one field")
        for attempt in range(max(1, retries)):
            if version is None:
                version = self.item_version(key)
            status, payload, _ = self._request(
                "PATCH", f"/items/{key}", body=fields, version=version
            )
            if status in (200, 204):
                return
            if status == 412 and attempt < retries - 1:
                version = None  # re-read and retry
                continue
            self._raise_for_status(status, payload, f"PATCH item {key}")
        raise ZoteroConflictError(
            f"Could not update {key} after {retries} attempts (item kept changing)"
        )

    def add_tags(self, key: str, tags: list[str]) -> list[str]:
        """Add tags without disturbing existing ones. Returns the final tag list.

        This is the safe counterpart to a raw ``PATCH``: the API treats ``tags``
        as a complete list, so writing only the new tags deletes the rest.

        Existing tags are re-sent **verbatim**, including their ``type`` field.
        Rebuilding them from names alone downgraded Zotero's automatic
        (connector-added) tags to manual on every write — the API returns
        ``{"tag": ..., "type": 1}`` for an auto tag and treats a missing ``type``
        as manual. Tagging an item must not silently reclassify its other tags.
        """
        key = normalize_key(key)
        wanted = [t.strip() for t in tags if str(t).strip()]
        if not wanted:
            return self.tag_names(key)
        for attempt in range(3):
            envelope = self.get_item(key)
            current_tags = list(envelope["data"].get("tags", []) or [])
            current = [t.get("tag") for t in current_tags if t.get("tag")]
            additions = [t for t in wanted if t not in current]
            if not additions:
                return current
            # Keep each existing dict intact; new tags default to manual.
            merged = current_tags + [{"tag": t} for t in additions]
            status, payload, _ = self._request(
                "PATCH",
                f"/items/{key}",
                body={"tags": merged},
                version=envelope["version"],
            )
            if status in (200, 204):
                return current + additions
            if status == 412 and attempt < 2:
                continue
            self._raise_for_status(status, payload, f"tag item {key}")
        raise ZoteroConflictError(f"Could not tag {key}: item kept changing")

    def tag_names(self, key: str) -> list[str]:
        return [t["tag"] for t in self.get_item(key)["data"].get("tags", [])]

    def set_tags(self, key: str, tags: list[str]) -> None:
        """Replace the tag list wholesale. Prefer :meth:`add_tags`.

        A replacement is by definition lossy for tag *metadata*: the ``type``
        flag of tags that survive is not carried over, so connector-added
        automatic tags re-sent through here become manual. That is acceptable
        for governance, which rewrites the curated list on purpose, but it is
        why :meth:`add_tags` preserves the original dicts instead of rebuilding
        them. Use this only when the caller owns the whole list.
        """
        self.patch(key, {"tags": [{"tag": t} for t in tags]})

    def add_to_collection(self, key: str, collection: str) -> list[str]:
        """Add an item to a collection, keeping its other memberships."""
        key = normalize_key(key)
        collection = normalize_key(collection, "collection key")
        for attempt in range(3):
            envelope = self.get_item(key)
            current = list(envelope["data"].get("collections", []) or [])
            if collection in current:
                return current
            merged = current + [collection]
            status, payload, _ = self._request(
                "PATCH",
                f"/items/{key}",
                body={"collections": merged},
                version=envelope["version"],
            )
            if status in (200, 204):
                return merged
            if status == 412 and attempt < 2:
                continue
            self._raise_for_status(status, payload, f"move item {key}")
        raise ZoteroConflictError(f"Could not move {key}: item kept changing")

    def remove_from_collection(self, key: str, collection: str) -> list[str]:
        """Remove an item from one collection, keeping its other memberships."""
        key = normalize_key(key)
        collection = normalize_key(collection, "collection key")
        for attempt in range(3):
            envelope = self.get_item(key)
            current = list(envelope["data"].get("collections", []) or [])
            if collection not in current:
                return current
            remaining = [c for c in current if c != collection]
            status, payload, _ = self._request(
                "PATCH",
                f"/items/{key}",
                body={"collections": remaining},
                version=envelope["version"],
            )
            if status in (200, 204):
                return remaining
            if status == 412 and attempt < 2:
                continue
            self._raise_for_status(status, payload, f"unfile item {key}")
        raise ZoteroConflictError(f"Could not unfile {key}: item kept changing")

    def triage_item(
        self,
        key: str,
        *,
        target: str,
        tags: list[str] | None = None,
        inbox: str | None = None,
        drop_tags: tuple[str, ...] = (),
    ) -> dict:
        """Move an item out of the inbox into a target collection and tag it.

        One read and one write, so a batch of triage operations cannot drift
        apart the way a sequence of separate calls would.
        """
        key = normalize_key(key)
        target = normalize_key(target, "target collection key")
        if inbox:
            inbox = normalize_key(inbox, "inbox collection key")

        for attempt in range(3):
            envelope = self.get_item(key)
            data = envelope["data"]
            current_cols = list(data.get("collections", []) or [])
            current_tag_objs = list(data.get("tags", []) or [])
            current_tags = [t["tag"] for t in current_tag_objs]

            cols = [c for c in current_cols if c != inbox] if inbox else list(current_cols)
            if target not in cols:
                cols.append(target)

            drop = set(drop_tags)
            dropped = [t for t in current_tags if t in drop]
            # Preserve each surviving tag dict verbatim (notably ``type``), so
            # triage does not downgrade connector-added automatic tags.
            kept_objs = [t for t in current_tag_objs if t.get("tag") not in drop]
            kept = [t["tag"] for t in kept_objs]
            for tag in tags or []:
                if tag not in kept:
                    kept.append(tag)
                    kept_objs.append({"tag": tag})

            if cols == current_cols and kept == current_tags:
                return {"key": key, "collections": cols, "tags": kept, "changed": False}

            status, payload, _ = self._request(
                "PATCH",
                f"/items/{key}",
                body={
                    "collections": cols,
                    "tags": kept_objs,
                },
                version=envelope["version"],
            )
            if status in (200, 204):
                return {
                    "key": key,
                    "collections": cols,
                    "tags": kept,
                    "droppedTags": dropped,
                    "changed": True,
                }
            if status == 412 and attempt < 2:
                continue
            self._raise_for_status(status, payload, f"triage item {key}")
        raise ZoteroConflictError(f"Could not triage {key}: item kept changing")

    def _stored_pdf_path(self, key: str, filename: str) -> str | None:
        """Absolute path of an ``imported_file`` attachment's stored copy."""
        if not self._data_dir:
            return None
        return os.path.join(self._data_dir, "storage", key, filename)

    def rename_attachment(self, key: str, name: str) -> dict:
        """Rename an attachment's stored filename, on disk *and* in the database.

        The local API's ``filename`` field only relabels the *row*: Zotero does
        not move the file. Verified on this machine against Zotero 10.0.5 — after
        ``PATCH {"filename": ...}`` the item reported the new name while
        ``storage/<key>/`` still held the old one. The two then disagree, and the
        next local read resolves the recorded name to nothing: MinerU failed with
        "PDF file not found (linked attachment?)" until the paths were
        hand-synchronised.

        So the file is moved here, and the PATCH follows. Ordering matters: the
        move is the reversible half (the API field can be set to anything), and
        doing it first means a failure leaves the row describing where the bytes
        really are rather than a name that resolves to nothing.

        Linked files (``linkMode: linked_file``) live outside ``storage/`` and are
        renamed in place at their recorded path.
        """
        key = normalize_key(key, "attachment key")
        clean = str(name or "").strip()
        if not clean:
            raise ZoteroWriteError("Attachment name cannot be empty")
        if os.path.basename(clean) != clean or clean in (".", ".."):
            raise ZoteroWriteError(
                f"Attachment name must be a bare filename, not a path: {name!r}"
            )

        envelope = self.get_item(key)
        data = envelope.get("data") or {}
        if data.get("itemType") != "attachment":
            raise ZoteroWriteError(f"{key} is not an attachment")
        if data.get("linkMode") == "linked_url":
            raise ZoteroWriteError(f"{key} is a URL attachment and has no file to rename")

        old_name = str(data.get("filename") or "")
        moved_from: str | None = None
        moved_to: str | None = None

        if old_name and old_name != clean:
            source = self._resolve_attachment_file(key, data, old_name)
            if source:
                target = os.path.join(os.path.dirname(source), clean)
                if os.path.exists(target):
                    raise ZoteroWriteError(
                        f"Refusing to rename: {target} already exists"
                    )
                try:
                    os.rename(source, target)
                except OSError as error:
                    raise ZoteroWriteError(
                        f"Cannot rename {source} -> {target}: {error}"
                    ) from error
                moved_from, moved_to = source, target

        try:
            self.patch(key, {"filename": clean})
        except ZoteroWriteError:
            # Restore the file so the row and the disk do not diverge on failure.
            if moved_from and moved_to and os.path.exists(moved_to):
                try:
                    os.rename(moved_to, moved_from)
                except OSError:
                    pass
            raise

        return {
            "key": key,
            "filename": clean,
            "previous": old_name or None,
            "fileMoved": bool(moved_from),
            "path": moved_to,
        }

    def _resolve_attachment_file(self, key: str, data: dict, filename: str) -> str | None:
        """Locate an attachment's bytes, tolerating an already-diverged pair.

        Returns None when nothing is found, which downgrades the rename to a
        metadata-only update rather than an error: the row is still worth
        correcting, and refusing would strand the caller.
        """
        candidates: list[str] = []

        stored = self._stored_pdf_path(key, filename)
        if stored:
            candidates.append(stored)

        raw_path = str(data.get("path") or "")
        if raw_path:
            if raw_path.startswith("storage:"):
                rel = raw_path[len("storage:") :]
                if self._data_dir:
                    candidates.append(
                        os.path.join(self._data_dir, "storage", key, rel)
                    )
            elif raw_path.startswith("attachments:"):
                # Relative to the data directory, not to storage/<key>/.
                if self._data_dir:
                    candidates.append(
                        os.path.join(
                            self._data_dir, raw_path[len("attachments:") :]
                        )
                    )
            elif os.path.isabs(raw_path):
                candidates.append(raw_path)

        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate

        # Last resort: the row is stale but the folder holds exactly one file
        # (the state a previous metadata-only rename leaves behind).
        if self._data_dir:
            folder = os.path.join(self._data_dir, "storage", key)
            try:
                entries = [
                    os.path.join(folder, entry)
                    for entry in os.listdir(folder)
                    if not entry.startswith(".")
                ]
            except OSError:
                entries = []
            files = [entry for entry in entries if os.path.isfile(entry)]
            if len(files) == 1:
                return files[0]
        return None

    def relink_attachment(self, key: str, path: str) -> None:
        """Point a linked-file attachment at a new absolute path.

        Zotero stores linked paths in ``path`` with an ``attachments:`` prefix
        for relative-to-data-dir paths; an absolute path is written verbatim so
        the link survives a data-dir move.
        """
        absolute = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(absolute):
            raise ZoteroWriteError(f"File not found: {absolute}")
        self.patch(normalize_key(key, "attachment key"), {"path": absolute})

    def create_collection(self, name: str, parent: str | None = None) -> str:
        """Create a collection (optionally nested); return its key."""
        clean = str(name or "").strip()
        if not clean:
            raise ZoteroWriteError("Collection name cannot be empty")
        body: dict = {"name": clean}
        if parent:
            body["parentCollection"] = normalize_key(parent, "parent collection key")
        status, payload, _ = self._request("POST", "/collections", body=[body])
        self._raise_for_status(status, payload, "create collection")
        result = payload if isinstance(payload, dict) else {}
        created = (result.get("successful") or {}).get("0")
        if not created:
            raise ZoteroWriteError(f"Zotero did not return a collection key: {payload}")
        return created["key"]

    # ------------------------------------------------------------------
    #  Trash vs permanent deletion
    # ------------------------------------------------------------------

    def set_deleted(self, key: str, deleted: bool = True) -> None:
        """Move an item to (or out of) the trash. Reversible."""
        self.patch(key, {"deleted": 1 if deleted else 0})

    def trash(self, key: str) -> None:
        """Move to trash. This is the default deletion path."""
        self.set_deleted(key, True)

    def restore(self, key: str) -> None:
        """Restore from trash."""
        self.set_deleted(key, False)

    def delete_permanently(self, key: str, *, confirm: bool = False) -> None:
        """Erase an item outright. **Not reversible, not sent to the trash.**

        Guarded behind ``confirm=True`` because the local API's ``DELETE``
        bypasses the trash entirely (verified against a live library:
        ``deletedItems`` did not change while the item vanished).
        """
        if not confirm:
            raise ZoteroWriteError(
                "delete_permanently() erases the item without using the trash. "
                "Pass confirm=True if that is really intended, or use trash()."
            )
        key = normalize_key(key)
        version = self.item_version(key)
        status, payload, _ = self._request(
            "DELETE", f"/items/{key}", version=version
        )
        self._raise_for_status(status, payload, f"delete item {key}")

    # ------------------------------------------------------------------
    #  Attachments
    # ------------------------------------------------------------------

    def create_attachment(
        self,
        parent: str,
        *,
        title: str,
        filename: str,
        content_type: str = "application/pdf",
    ) -> str:
        """Create an ``imported_file`` attachment under a regular item."""
        parent = normalize_key(parent, "parent item key")
        body = {
            "itemType": "attachment",
            "linkMode": "imported_file",
            "parentItem": parent,
            "title": title or filename,
            "filename": filename,
            "contentType": content_type,
        }
        keys = self.create_items([body])
        if not keys:
            raise ZoteroWriteError("Attachment was not created")
        return keys[0]

    def upload_file(self, attachment_key: str, path: str) -> dict:
        """Upload a file onto an existing attachment (three-phase flow).

        Phase 1 authorizes and may short-circuit with ``{"exists": 1}`` when the
        stored file already matches the MD5. Phase 2 posts the bytes to the
        returned local URL (no auth needed — the upload key carries it). Phase 3
        registers completion with ``{"upload": <uploadKey>}``.

        The third-phase body key is ``upload``; sending ``uploadKey`` instead is
        accepted silently but is interpreted as a *fresh authorization request*,
        so the file never lands. That trap is why this is a method and not a
        snippet.
        """
        attachment_key = normalize_key(attachment_key, "attachment key")
        if not os.path.isfile(path):
            raise ZoteroWriteError(f"File not found: {path}")
        size = os.path.getsize(path)
        if size > 4 * 1024 * 1024 * 1024:
            raise ZoteroWriteError("Zotero rejects files of 4 GB or larger")
        filename = os.path.basename(path)
        digest = _md5_file(path)
        payload = {
            "md5": digest,
            "filename": filename,
            "filesize": size,
            "mtime": _mtime_ms(path),
        }

        # Phase 1 — authorize (or learn the file is already there).
        status, body, _ = self._request(
            "POST",
            f"/items/{attachment_key}/file",
            body=payload,
            extra_headers={"If-None-Match": "*"},
        )
        self._raise_for_status(status, body, f"authorize upload for {attachment_key}")
        if isinstance(body, dict) and body.get("exists"):
            return {"uploaded": False, "reason": "already present", "md5": digest}
        upload_url = (body or {}).get("url")
        upload_key = (body or {}).get("uploadKey")
        if not upload_url or not upload_key:
            raise ZoteroWriteError(f"Unexpected upload authorization: {body}")

        # Phase 2 — send bytes. Deliberately unauthenticated: the upload key
        # in the URL is the credential, and extra headers can break the PUT.
        with open(path, "rb") as handle:
            raw = handle.read()
        status, detail, _ = self._request(
            "POST",
            "",
            raw=raw,
            content_type="application/octet-stream",
            absolute_url=upload_url,
            auth=False,
        )
        if status not in (200, 201, 204):
            raise ZoteroWriteError(f"Upload failed (HTTP {status}): {detail}")

        # Phase 3 — register completion. Note: key name is "upload".
        status, detail, _ = self._request(
            "POST",
            f"/items/{attachment_key}/file",
            body={"upload": upload_key},
            extra_headers={"If-None-Match": "*"},
        )
        self._raise_for_status(status, detail, f"register upload for {attachment_key}")
        return {"uploaded": True, "md5": digest, "size": size}

    def set_fulltext(self, attachment_key: str, content: str) -> None:
        """Replace an attachment's indexed full text."""
        attachment_key = normalize_key(attachment_key, "attachment key")
        text = content or ""
        status, payload, _ = self._request(
            "PUT",
            f"/items/{attachment_key}/fulltext",
            body={
                "content": text,
                "indexedChars": len(text),
                "totalChars": len(text),
            },
        )
        self._raise_for_status(status, payload, f"set fulltext on {attachment_key}")

    # ------------------------------------------------------------------
    #  Snapshot / undo primitives
    # ------------------------------------------------------------------

    #: Fields captured by :meth:`snapshot`. Only fields this repo mutates —
    #: widening it costs an extra copy of every note body on each write.
    #: ``filename``/``path`` are attachment fields, captured so ``write undo``
    #: can put an attachment back where it was; :meth:`restore_snapshot` routes
    #: them through the rename/relink methods rather than a raw PATCH, because a
    #: raw PATCH would move the database row without moving the file.
    SNAPSHOT_FIELDS = ("tags", "collections", "title", "extra", "filename", "path")

    def snapshot(self, key: str) -> dict:
        """Capture the mutable state of an item for a later :meth:`restore`."""
        data = self.get_item(key)["data"]
        return {
            field: data.get(field)
            for field in self.SNAPSHOT_FIELDS
            if data.get(field) is not None
        }

    def restore_snapshot(self, key: str, snapshot: dict) -> None:
        """Write a snapshot back, reverting the fields it captured.

        Attachment fields are routed through the operations that keep the file
        and the row together. A plain ``PATCH {"filename": ...}`` relabels only
        the row (see :meth:`rename_attachment`), so replaying an undo that way
        would leave the attachment pointing at a file that no longer has that
        name — the failure being fixed elsewhere in this module.
        """
        if not snapshot:
            return
        fields = {k: v for k, v in snapshot.items() if k in self.SNAPSHOT_FIELDS}
        if not fields:
            return

        filename = fields.pop("filename", None)
        path = fields.pop("path", None)
        if fields:
            self.patch(key, fields)

        # Replayed explicitly rather than by PATCH so the stored file follows.
        if filename:
            try:
                current_name = str(self.get_item(key)["data"].get("filename") or "")
            except ZoteroWriteError:
                current_name = ""
            if current_name != filename:
                self.rename_attachment(key, filename)
        if path:
            try:
                self.relink_attachment(key, path)
            except ZoteroWriteError:
                # A relink target that has since disappeared must not make the
                # whole undo fail; the other fields are already restored.
                pass

    # ------------------------------------------------------------------
    #  Merge
    # ------------------------------------------------------------------

    def merge(self, master: str, others: list[str]) -> dict:
        """Merge duplicates into ``master`` without the Zotero UI.

        The API has no merge endpoint (404 on every shape tried), so this
        reproduces the essential effect: fold the donors' tags/collections into
        the master, re-parent their children onto the master, then **trash** the
        donors.

        Trashing rather than erasing is what makes this recoverable — unlike the
        plugin's merge, which was genuinely irreversible. ``childMoves`` records
        each re-parented child's original owner so :meth:`unmerge` can replay it
        backwards.
        """
        master = normalize_key(master, "master key")
        donors = [normalize_key(k, "duplicate key") for k in others]
        if master in donors:
            raise ZoteroWriteError("The master item cannot also be a duplicate")
        if not donors:
            return {"master": master, "merged": 0, "moved": 0,
                    "trashed": [], "childMoves": []}

        master_env = self.get_item(master)
        master_tags = [t["tag"] for t in master_env["data"].get("tags", [])]
        master_cols = list(master_env["data"].get("collections", []) or [])

        moved = 0
        child_moves: list[dict] = []
        for donor in donors:
            donor_env = self.get_item(donor)
            for tag in donor_env["data"].get("tags", []):
                if tag["tag"] not in master_tags:
                    master_tags.append(tag["tag"])
            for col in donor_env["data"].get("collections", []) or []:
                if col not in master_cols:
                    master_cols.append(col)

            # Re-parent child items so notes/attachments survive the merge.
            for child in self.get_children(donor):
                self.patch(child["key"], {"parentItem": master})
                child_moves.append({"key": child["key"], "from": donor})
                moved += 1

        self.patch(
            master,
            {
                "tags": [{"tag": t} for t in master_tags],
                "collections": master_cols,
            },
        )
        for donor in donors:
            self.trash(donor)

        return {
            "master": master,
            "merged": len(donors),
            "moved": moved,
            "trashed": donors,
            "childMoves": child_moves,
            "tags": master_tags,
            "collections": master_cols,
        }

    def unmerge(self, master: str, donors: list[str], child_moves: list[dict]) -> dict:
        """Reverse :meth:`merge`: send children back, then restore the donors.

        Replays in the opposite order to the merge. The master's own
        tags/collections are *not* touched here — the caller restores those from
        the ledger snapshot, which is the only place the prior values exist.
        """
        master = normalize_key(master, "master key")
        returned = 0
        for move in child_moves or []:
            child = move.get("key")
            origin = move.get("from")
            if not child or not origin:
                continue
            try:
                self.patch(child, {"parentItem": normalize_key(origin, "parent key")})
                returned += 1
            except ZoteroWriteError:
                continue
        restored: list[str] = []
        for donor in donors or []:
            try:
                self.restore(donor)
                restored.append(donor)
            except ZoteroWriteError:
                continue
        return {"master": master, "childrenReturned": returned, "restored": restored}

    def get_children(self, key: str) -> list[dict]:
        """Return child items (notes, attachments) of an item."""
        key = normalize_key(key)
        status, payload, _ = self._request("GET", f"/items/{key}/children")
        self._raise_for_status(status, payload, f"GET children of {key}")
        return payload if isinstance(payload, list) else []

    # ------------------------------------------------------------------
    #  Convenience
    # ------------------------------------------------------------------

    def wait_for_index(self, seconds: float = 0.5) -> None:
        """Brief pause so a subsequent read sees the write.

        Zotero applies local API writes synchronously, but a caller that
        immediately re-reads through a *different* channel (a sqlite copy, the
        search index) can still race the commit.
        """
        time.sleep(seconds)


# ----------------------------------------------------------------------
#  Construction with auto-start
# ----------------------------------------------------------------------


def build_write_client(
    config,
    *,
    ensure_running: bool = True,
    max_wait: int = 60,
    api_key: str | None = None,
) -> "ZoteroWriteClient":
    """Build a write client, starting Zotero first when it is not available.

    Every write entry point routes through here so the "Zotero is not running"
    failure is handled once. Previously each command constructed
    :class:`ZoteroWriteClient` directly, and each therefore failed identically
    when the app was closed — the user had to notice, start Zotero by hand, and
    retry. Auto-start is what makes that a non-event.

    ``config`` is a :class:`zotero.config.ZoteroConfig`; it is taken as a loose
    parameter to avoid importing config here and creating a cycle.

    Raises :class:`ZoteroWriteError` with a precise message when Zotero cannot be
    reached and cannot be started, so callers still surface one clear error
    rather than a connection traceback.
    """
    client = ZoteroWriteClient(
        config.api_url,
        config.user_id,
        api_key=api_key,
        # Needed by operations that touch files on disk (attachment rename).
        data_dir=getattr(config, "data_dir", None),
    )
    if not ensure_running:
        return client

    if client.check_writable():
        return client

    # Imported lazily: the launcher imports config, and a module-level import
    # here would make the dependency unconditional for callers that merely read.
    from .launcher import ZoteroLauncher

    result = ZoteroLauncher(config).ensure_zotero_running(max_wait=max_wait)
    if result.get("status") == "error":
        raise ZoteroWriteError(
            f"{result.get('message')}"
            + (f" — {result['diagnostic']}" if result.get("diagnostic") else "")
        )
    return client


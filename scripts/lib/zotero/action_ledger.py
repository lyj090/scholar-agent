#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
action_ledger.py — self-contained undo history for Zotero writes.

Why this exists
---------------
The old ledger was only an *index*: it stored the plugin's ``actionId`` so
``rollback --last N`` could ask the plugin's journal to revert. Reading that
journal needed an in-process carrier, so undo was impossible without the plugin
— that was the single strongest argument for keeping it installed.

Now that writes go through :mod:`zotero.local_write`, the ledger can record the
**actual before/after state** of each touched field. Undo therefore needs no
cooperation from Zotero beyond the same public API the write used. Undo stops
being a plugin feature and becomes our own data.

Design notes
------------
* The file stays JSON Lines so existing ``data/zotero-actions.jsonl`` content
  (which has ``actionId``/``tool``/``operation``, no ``before``) is still
  readable and correctly reported as *not self-reverting*.
* Every entry records ``reversibility``:
    - ``full``    — ``before`` covers every field that was written
    - ``partial`` — only some fields are restorable
    - ``none``    — no snapshot (e.g. a merge); undo is refused, not faked
  Refusing loudly matters: a rollback that claims success while restoring
  nothing is worse than one that says it cannot.
* Losing this file degrades undo to "no history" — it is never the system of
  record for the library, so a write must not fail because the ledger could not
  be appended.

NOT responsible for: talking to Zotero (that is ``local_write``), CLI parsing.
"""

from __future__ import annotations

import json
import os
import time
import uuid

#: Ledger entries past this size trigger a trim to the newest half.
LEDGER_MAX_BYTES = 4_000_000

REVERSIBILITY_FULL = "full"
REVERSIBILITY_PARTIAL = "partial"
REVERSIBILITY_NONE = "none"


def _default_ledger_path() -> str:
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    return os.path.join(repo_root, "data", "zotero-actions.jsonl")


LEDGER_PATH = os.environ.get("ZOTERO_ACTION_LEDGER") or _default_ledger_path()


def new_action_id() -> str:
    """Locally-generated action id: sortable by time and unique per process."""
    return f"act-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def canonical_field(field: str, value):
    """Normalize a field value for comparison.

    Zotero returns tags alphabetically regardless of insertion order, and
    collections in its own order. Comparing raw values therefore reports a
    spurious "changed since the action" conflict for any multi-tag write,
    which silently blocks legitimate undo.
    """
    if field == "tags":
        names = []
        for item in value or []:
            if isinstance(item, dict):
                names.append(item.get("tag"))
            else:
                names.append(item)
        return sorted(n for n in names if n is not None)
    if field == "collections":
        return sorted(value or [])
    return value


def _fields_drifted(expected: dict, current: dict) -> list[str]:
    """Fields whose current value differs from what the action wrote."""
    drifted = []
    for field, written in (expected or {}).items():
        if canonical_field(field, current.get(field)) != canonical_field(field, written):
            drifted.append(field)
    return drifted


class ActionLedger:
    """Append-only JSONL history with snapshot-based undo."""

    def __init__(self, path: str | None = None, *, max_bytes: int = LEDGER_MAX_BYTES):
        self.path = os.path.expanduser(path or LEDGER_PATH)
        self.max_bytes = max_bytes

    # ------------------------------------------------------------------
    #  Read
    # ------------------------------------------------------------------

    def read(self) -> list[dict]:
        """Return entries oldest-first, skipping unreadable lines."""
        if not os.path.exists(self.path):
            return []
        entries: list[dict] = []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict) and parsed.get("actionId"):
                        entries.append(parsed)
        except OSError:
            return []
        return entries

    def select_last(
        self, count: int, *, only_reversible: bool = True
    ) -> tuple[list[dict], list[dict]]:
        """The newest ``count`` entries, as ``(picked, skipped)``, newest-first.

        ``--last N`` is interpreted literally: the window is the N newest
        actions, and among them the replayable ones come back in ``picked`` while
        the rest are reported in ``skipped``. An irreversible action inside the
        window is therefore surfaced to the caller instead of being silently
        swapped for an older action, which would revert something the user never
        asked about.

        Reverts replay **newest-first**. Undoing oldest-first would let an older
        action's snapshot stamp over a newer action's result, because a snapshot
        covers whole fields (``tags``, ``collections``) rather than the single
        tag a particular action added.
        """
        window = list(reversed(self.read()))[: max(0, count)]
        if not only_reversible:
            return window, []
        picked = [e for e in window if self.is_reversible(e)]
        skipped = [e for e in window if not self.is_reversible(e)]
        return picked, skipped

    @staticmethod
    def is_reversible(entry: dict) -> bool:
        """True when this entry carries enough state to be replayed.

        Legacy plugin-journal entries have an ``actionId`` but no snapshot, so
        they are reported as irreversible instead of pretending to revert.
        """
        return bool(
            entry.get("before") and entry.get("reversibility") != REVERSIBILITY_NONE
        )

    def find(self, action_id: str) -> dict | None:
        for entry in reversed(self.read()):
            if entry.get("actionId") == action_id:
                return entry
        return None

    # ------------------------------------------------------------------
    #  Write
    # ------------------------------------------------------------------

    def record(self, entry: dict) -> None:
        """Append one entry. Never fatal — undo history is not the library."""
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._trim_if_needed()
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _trim_if_needed(self) -> None:
        try:
            if not os.path.exists(self.path):
                return
            if os.path.getsize(self.path) <= self.max_bytes:
                return
            with open(self.path, "r", encoding="utf-8") as handle:
                lines = [ln for ln in handle.read().splitlines() if ln.strip()]
            with open(self.path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines[len(lines) // 2:]) + "\n")
        except OSError:
            pass

    def record_write(
        self,
        *,
        operation: str,
        keys: list[str],
        before: dict | None = None,
        after: dict | None = None,
        description: str = "",
        reversibility: str | None = None,
        action_id: str | None = None,
        extra: dict | None = None,
    ) -> str:
        """Record one write and return its action id.

        ``before`` should be a per-key mapping of field -> prior value, as
        produced by :meth:`snapshot_many`. It is what makes the entry replayable.
        """
        if reversibility is None:
            reversibility = REVERSIBILITY_FULL if before else REVERSIBILITY_NONE
        entry = {
            "actionId": action_id or new_action_id(),
            "operation": operation,
            "keys": list(keys),
            "description": description,
            "reversibility": reversibility,
            "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if before is not None:
            entry["before"] = before
        if after is not None:
            entry["after"] = after
        if extra:
            entry.update(extra)
        self.record(entry)
        return entry["actionId"]

    # ------------------------------------------------------------------
    #  Snapshot helpers
    # ------------------------------------------------------------------

    @staticmethod
    def snapshot_many(client, keys: list[str], fields: tuple[str, ...] | None = None) -> dict:
        """Capture ``{key: {field: value}}`` for later restoration.

        ``fields`` narrows the capture to just the fields an action is about to
        write. That scoping is what makes targeted undo safe: a full snapshot of
        ``tags`` taken before action A would, when replayed, also erase a tag
        that action B added afterwards. Capture only what you are about to
        change.
        """
        snapshots: dict[str, dict] = {}
        wanted = fields if fields is not None else getattr(client, "SNAPSHOT_FIELDS", None)
        for key in keys:
            try:
                full = client.snapshot(key)
            except Exception:
                # A key that vanished cannot be snapshotted; the entry simply
                # cannot cover it.
                continue
            if wanted is None:
                snapshots[key] = full
            else:
                snapshots[key] = {f: full.get(f) for f in wanted if f in full}
        return snapshots

    def rollback(self, action_id: str, client, *, dry_run: bool = False) -> dict:
        """Replay the inverse of one recorded action.

        Two kinds of inverse exist:

        * **Field snapshot** — most actions. Restore the captured fields.
        * **Structural** — a merge. Donors must come back out of the trash and
          re-parented children must go home; the snapshot alone cannot express
          that, so the recorded ``childMoves`` are replayed too.

        Returns a report with ``reverted`` and ``conflicts``. Conflicts are
        reported rather than forced: if the item changed after the write, blindly
        stamping the old snapshot back would silently discard someone else's edit.
        """
        entry = self.find(action_id)
        if not entry:
            return {"actionId": action_id, "ok": False, "error": "unknown actionId"}
        if not self.is_reversible(entry):
            return {
                "actionId": action_id,
                "ok": False,
                "reverted": 0,
                "error": (
                    "action has no snapshot and cannot be reverted "
                    "(a legacy plugin-journal entry, or a permanent erase)"
                ),
            }

        before = entry.get("before") or {}
        after = entry.get("after") or {}
        reverted: list[str] = []
        conflicts: list[dict] = []
        structural: dict | None = None

        # Structural inverse first, so children are back under their donors
        # before any donor-level field restoration touches them.
        if entry.get("operation") == "merge_items" and hasattr(client, "unmerge"):
            if dry_run:
                structural = {
                    "childrenToReturn": len(entry.get("childMoves") or []),
                    "donorsToRestore": len(entry.get("others") or []),
                }
            else:
                try:
                    structural = client.unmerge(
                        entry.get("master"),
                        entry.get("others") or [],
                        entry.get("childMoves") or [],
                    )
                except Exception as error:
                    conflicts.append({"key": entry.get("master"), "reason": str(error)})

        for key, fields in before.items():
            if not isinstance(fields, dict) or not fields:
                continue
            try:
                current = client.snapshot(key)
            except Exception as error:
                conflicts.append({"key": key, "reason": f"unreadable: {error}"})
                continue

            # Only revert fields that still hold the value we wrote. Anything
            # else means a third party touched the item after us.
            expected = after.get(key) if isinstance(after, dict) else None
            drifted = []
            if isinstance(expected, dict):
                drifted = _fields_drifted(expected, current)
            if drifted and not dry_run:
                conflicts.append(
                    {"key": key, "reason": "changed since the action", "fields": drifted}
                )
                continue

            if not dry_run:
                try:
                    client.restore_snapshot(key, fields)
                except Exception as error:
                    conflicts.append({"key": key, "reason": str(error)})
                    continue
            reverted.append(key)

        return {
            "actionId": action_id,
            "ok": True,
            "operation": entry.get("operation"),
            "dryRun": dry_run,
            "reverted": len(reverted),
            "keys": reverted,
            "conflicts": conflicts,
            "structural": structural,
            "restored": before if dry_run else None,
        }

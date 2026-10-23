#!/usr/bin/env python3
"""Unit tests for snapshot-based undo history."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from zotero.action_ledger import (  # noqa: E402
    ActionLedger,
    REVERSIBILITY_NONE,
    canonical_field,
    new_action_id,
)


class FakeClient:
    """Minimal stand-in recording restores, so undo can be tested offline."""

    SNAPSHOT_FIELDS = ("tags", "collections", "title", "extra")

    def __init__(self, state: dict):
        self.state = state
        self.restored: list[tuple[str, dict]] = []

    def snapshot(self, key: str) -> dict:
        return dict(self.state[key])

    def restore_snapshot(self, key: str, snapshot: dict) -> None:
        self.restored.append((key, snapshot))
        self.state[key].update(snapshot)


class TempLedger:
    def __enter__(self) -> ActionLedger:
        self._dir = tempfile.TemporaryDirectory(prefix="ledger-test-")
        return ActionLedger(str(Path(self._dir.name) / "actions.jsonl"))

    def __exit__(self, *exc) -> None:
        self._dir.cleanup()


class CanonicalFieldTest(unittest.TestCase):
    """Zotero returns tags alphabetically regardless of write order, so raw
    comparison would report a false conflict and block legitimate undo."""

    def test_tags_compare_order_insensitively(self) -> None:
        written = [{"tag": "b"}, {"tag": "a"}]
        current = [{"tag": "a"}, {"tag": "b"}]
        self.assertEqual(
            canonical_field("tags", written), canonical_field("tags", current)
        )

    def test_collections_compare_order_insensitively(self) -> None:
        self.assertEqual(
            canonical_field("collections", ["X", "Y"]),
            canonical_field("collections", ["Y", "X"]),
        )

    def test_other_fields_compare_strictly(self) -> None:
        self.assertNotEqual(
            canonical_field("title", "a"), canonical_field("title", "b")
        )


class LedgerRecordTest(unittest.TestCase):
    def test_action_ids_are_unique(self) -> None:
        self.assertEqual(len({new_action_id() for _ in range(50)}), 50)

    def test_record_and_read_roundtrip(self) -> None:
        with TempLedger() as ledger:
            ledger.record_write(operation="add_tags", keys=["K1"],
                                before={"K1": {"tags": []}}, description="d")
            entries = ledger.read()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["operation"], "add_tags")

    def test_write_without_snapshot_is_irreversible(self) -> None:
        with TempLedger() as ledger:
            ledger.record_write(operation="merge_items", keys=["K1"], description="m")
            entry = ledger.read()[0]
        self.assertEqual(entry["reversibility"], REVERSIBILITY_NONE)
        self.assertFalse(ledger.is_reversible(entry))

    def test_legacy_plugin_entry_has_no_snapshot(self) -> None:
        """Entries written by the old plugin-journal mirror carry an actionId
        but no before-state; they must be reported as irreversible rather than
        silently 'reverted'."""
        legacy = {"actionId": "act-9", "operation": "rename_attachment",
                  "reversibility": "partial"}
        self.assertFalse(ActionLedger.is_reversible(legacy))


class SelectLastTest(unittest.TestCase):
    def test_returns_newest_first_for_replay(self) -> None:
        """Newest-first matters: undoing oldest-first lets an older snapshot
        stamp over a newer action's result."""
        with TempLedger() as ledger:
            for i in range(3):
                ledger.record_write(operation=f"op{i}", keys=["K"],
                                    before={"K": {"tags": []}})
            picked, skipped = ledger.select_last(2)
        self.assertEqual([e["operation"] for e in picked], ["op2", "op1"])
        self.assertEqual(skipped, [])

    def test_irreversible_inside_the_window_is_surfaced(self) -> None:
        """`--last N` is a literal window over the N newest actions. An
        irreversible action inside it must be reported, not silently skipped in
        favour of an older action the user never asked about."""
        with TempLedger() as ledger:
            ledger.record_write(operation="op0", keys=["K"],
                                before={"K": {"tags": []}})
            ledger.record_write(operation="merge", keys=["K"])  # irreversible
            ledger.record_write(operation="op1", keys=["K"],
                                before={"K": {"tags": []}})
            picked, skipped = ledger.select_last(3)
        self.assertEqual([e["operation"] for e in picked], ["op1", "op0"])
        self.assertEqual([e["operation"] for e in skipped], ["merge"])

    def test_window_excludes_actions_older_than_n(self) -> None:
        with TempLedger() as ledger:
            ledger.record_write(operation="old-merge", keys=["K"])
            for i in range(2):
                ledger.record_write(operation=f"op{i}", keys=["K"],
                                    before={"K": {"tags": []}})
            picked, skipped = ledger.select_last(2)
        self.assertEqual([e["operation"] for e in picked], ["op1", "op0"])
        self.assertEqual(skipped, [], "the merge sits outside the window")

    def test_picks_oldest_first_within_a_batch_for_replay(self) -> None:
        with TempLedger() as ledger:
            for i in range(3):
                ledger.record_write(operation=f"op{i}", keys=["K"],
                                    before={"K": {"tags": []}})
            picked, _ = ledger.select_last(3)
        # The list is newest-first; the caller reverses for actual replay.
        self.assertEqual(picked[0]["operation"], "op2")


class SnapshotScopingTest(unittest.TestCase):
    def test_snapshot_can_be_narrowed_to_written_fields(self) -> None:
        client = FakeClient({"K1": {"tags": ["a"], "collections": ["C"],
                                    "title": "t", "extra": None}})
        captured = ActionLedger.snapshot_many(client, ["K1"], ("tags",))
        self.assertEqual(captured, {"K1": {"tags": ["a"]}})

    def test_unreadable_key_is_skipped(self) -> None:
        client = FakeClient({})
        captured = ActionLedger.snapshot_many(client, ["GONE0001"], ("tags",))
        self.assertEqual(captured, {})


class RollbackTest(unittest.TestCase):
    def _entry(self, ledger, before, after):
        return ledger.record_write(operation="add_tags", keys=["K1"],
                                   before=before, after=after, description="t")

    def test_reverts_when_state_matches(self) -> None:
        with TempLedger() as ledger:
            before = {"K1": {"tags": [{"tag": "old"}]}}
            after = {"K1": {"tags": [{"tag": "old"}, {"tag": "new"}]}}
            aid = self._entry(ledger, before, after)
            client = FakeClient({"K1": {"tags": [{"tag": "old"}, {"tag": "new"}]}})
            report = ledger.rollback(aid, client)
        self.assertTrue(report["ok"])
        self.assertEqual(report["reverted"], 1)
        self.assertEqual(client.restored[0][1], before["K1"])

    def test_conflict_is_reported_not_forced(self) -> None:
        """If someone edited the item after us, stamping the old snapshot back
        would silently discard their work."""
        with TempLedger() as ledger:
            before = {"K1": {"tags": [{"tag": "old"}]}}
            after = {"K1": {"tags": [{"tag": "old"}, {"tag": "new"}]}}
            aid = self._entry(ledger, before, after)
            client = FakeClient({"K1": {"tags": [{"tag": "third-party"}]}})
            report = ledger.rollback(aid, client)
        self.assertEqual(report["reverted"], 0)
        self.assertEqual(len(report["conflicts"]), 1)
        self.assertEqual(client.restored, [], "must not overwrite a third-party edit")

    def test_tag_order_difference_is_not_a_conflict(self) -> None:
        with TempLedger() as ledger:
            before = {"K1": {"tags": [{"tag": "a"}]}}
            after = {"K1": {"tags": [{"tag": "b"}, {"tag": "a"}]}}
            aid = self._entry(ledger, before, after)
            # Same set, opposite order — Zotero normalizes ordering.
            client = FakeClient({"K1": {"tags": [{"tag": "a"}, {"tag": "b"}]}})
            report = ledger.rollback(aid, client)
        self.assertEqual(report["reverted"], 1)
        self.assertEqual(report["conflicts"], [])

    def test_dry_run_changes_nothing(self) -> None:
        with TempLedger() as ledger:
            before = {"K1": {"tags": []}}
            after = {"K1": {"tags": [{"tag": "new"}]}}
            aid = self._entry(ledger, before, after)
            client = FakeClient({"K1": {"tags": [{"tag": "new"}]}})
            report = ledger.rollback(aid, client, dry_run=True)
        self.assertTrue(report["dryRun"])
        self.assertEqual(client.restored, [])

    def test_irreversible_action_is_refused_with_a_reason(self) -> None:
        with TempLedger() as ledger:
            aid = ledger.record_write(operation="merge_items", keys=["K1"])
            client = FakeClient({"K1": {"tags": []}})
            report = ledger.rollback(aid, client)
        self.assertFalse(report["ok"])
        self.assertIn("cannot be reverted", report["error"])

    def test_unknown_action_id(self) -> None:
        with TempLedger() as ledger:
            report = ledger.rollback("nope", FakeClient({}))
        self.assertFalse(report["ok"])
        self.assertEqual(report["error"], "unknown actionId")


if __name__ == "__main__":
    unittest.main()

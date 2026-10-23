#!/usr/bin/env python3
"""Regression tests for tag-plan application.

Covered gap: ``apply_zotero_plan`` wrote tags to every changed item but never
recorded the prior values anywhere a command could read them. A governance sweep
touches 70+ items in one call; the only safety net was a ``<plan>.before.json``
file with no consumer, so the documented ``write undo`` rollback did not cover
governance at all. Each applied item is now recorded in the action ledger with
its previous tag list.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero import tag_governance  # noqa: E402


class ApplyZoteroPlanTest(unittest.TestCase):
    def _config(self) -> SimpleNamespace:
        return SimpleNamespace(
            api_url="http://127.0.0.1:23119", user_id="0"
        )

    def _run(self, plan: dict, *, fail_on: str | None = None):
        ledger = MagicMock()
        client = MagicMock()
        if fail_on:
            client.set_tags.side_effect = lambda key, tags: (
                (_ for _ in ()).throw(RuntimeError("404 not found"))
                if key == fail_on
                else None
            )
        with patch.object(tag_governance, "build_write_client", return_value=client), \
             patch.object(tag_governance, "ActionLedger", return_value=ledger):
            result = tag_governance.apply_zotero_plan(plan, self._config())
        return result, client, ledger

    def _plan(self, rows):
        return {"items": rows}

    def test_records_before_tags_for_every_changed_item(self) -> None:
        plan = self._plan([
            {"key": "AAAABBBB", "before_zotero": ["old/one"],
             "after": ["type/paper", "area/robotics"]},
            {"key": "CCCCDDDD", "before_zotero": ["old/two"],
             "after": ["type/paper"]},
        ])
        result, client, ledger = self._run(plan)
        self.assertEqual(result["changed"], 2)
        client.set_tags.assert_any_call("AAAABBBB", ["type/paper", "area/robotics"])
        ledger.record_write.assert_called_once()
        kwargs = ledger.record_write.call_args.kwargs
        self.assertEqual(sorted(kwargs["keys"]), ["AAAABBBB", "CCCCDDDD"])
        self.assertEqual(kwargs["before"]["AAAABBBB"]["tags"], ["old/one"])
        self.assertEqual(kwargs["before"]["CCCCDDDD"]["tags"], ["old/two"])
        self.assertEqual(kwargs["operation"], "tag_governance_apply")

    def test_only_changed_rows_are_written_and_recorded(self) -> None:
        plan = self._plan([
            {"key": "AAAABBBB", "before_zotero": ["type/paper"],
             "after": ["type/paper"]},
            {"key": "CCCCDDDD", "before_zotero": ["x"],
             "after": ["type/paper"]},
        ])
        result, client, ledger = self._run(plan)
        self.assertEqual(result["changed"], 1)
        client.set_tags.assert_called_once()
        self.assertEqual(ledger.record_write.call_args.kwargs["keys"], ["CCCCDDDD"])

    def test_no_changes_makes_no_write_and_no_ledger_entry(self) -> None:
        plan = self._plan([
            {"key": "AAAABBBB", "before_zotero": ["type/paper"],
             "after": ["type/paper"]},
        ])
        result, client, ledger = self._run(plan)
        self.assertEqual(result["changed"], 0)
        client.set_tags.assert_not_called()
        ledger.record_write.assert_not_called()

    def test_missing_item_is_reported_and_others_still_apply(self) -> None:
        plan = self._plan([
            {"key": "GONE1234", "before_zotero": ["x"], "after": ["type/paper"]},
            {"key": "CCCCDDDD", "before_zotero": ["y"], "after": ["type/paper"]},
        ])
        result, _client, ledger = self._run(plan, fail_on="GONE1234")
        self.assertEqual(result["missing"], ["GONE1234"])
        self.assertEqual(result["changed"], 1)
        # Only the successfully written item is ledgered, so undo never tries to
        # revert a write that did not happen.
        self.assertEqual(ledger.record_write.call_args.kwargs["keys"], ["CCCCDDDD"])


class ApplyObsidianPlanTest(unittest.TestCase):
    def _note(self, tags: list[str]) -> str:
        body = "\n".join(f"  - {t}" for t in tags)
        return f'---\ntitle: "X"\ntags:\n{body}\n---\n\n# Body\n'

    def test_rewrites_tags_and_preserves_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "n.md"
            note.write_text(self._note(["type/paper", "old/tag"]), encoding="utf-8")
            plan = {"items": [{
                "note_path": str(note),
                "before_obsidian": ["old/tag", "type/paper"],
                "after": ["type/paper", "area/robotics", "status/to-read"],
            }]}
            result = tag_governance.apply_obsidian_plan(plan)
            self.assertEqual(result["changed"], 1)
            text = note.read_text(encoding="utf-8")
            self.assertIn("  - area/robotics", text)
            self.assertNotIn("old/tag", text)
            self.assertIn("# Body", text)

    def test_drift_is_reported_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "n.md"
            note.write_text(self._note(["type/paper", "changed/after-plan"]),
                            encoding="utf-8")
            plan = {"items": [{
                "note_path": str(note),
                "before_obsidian": ["type/paper"],
                "after": ["type/paper", "area/robotics"],
            }]}
            result = tag_governance.apply_obsidian_plan(plan)
            self.assertEqual(result["changed"], 0)
            self.assertEqual(result["conflicts"], [str(note)])
            self.assertIn("changed/after-plan", note.read_text(encoding="utf-8"))

    def test_force_overrides_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "n.md"
            note.write_text(self._note(["type/paper", "drifted"]), encoding="utf-8")
            plan = {"items": [{
                "note_path": str(note),
                "before_obsidian": ["type/paper"],
                "after": ["type/paper", "area/robotics"],
            }]}
            result = tag_governance.apply_obsidian_plan(plan, force=True)
            self.assertEqual(result["changed"], 1)
            self.assertIn("area/robotics", note.read_text(encoding="utf-8"))

    def test_missing_note_is_skipped(self) -> None:
        plan = {"items": [{
            "note_path": "/nonexistent/n.md",
            "before_obsidian": [],
            "after": ["type/paper"],
        }]}
        result = tag_governance.apply_obsidian_plan(plan)
        self.assertEqual(result["changed"], 0)
        self.assertEqual(result["conflicts"], [])


if __name__ == "__main__":
    unittest.main()

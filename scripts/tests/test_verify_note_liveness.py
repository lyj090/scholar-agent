#!/usr/bin/env python3
"""Regression tests for verify-note's Zotero-key liveness check.

Covered defect: ``verify-note`` validated the *shape* of ``zotero_key`` (8
uppercase alphanumerics) but never checked that it still resolves. A merge
trashes the donor item and keeps its key, so a note could hold a perfectly
well-formed key pointing at a trashed item and still report all 12 checks
passed. Two notes in this vault were in exactly that state.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero.api import ZoteroAPIError  # noqa: E402
from zotero.commands import cmd_verify_note  # noqa: E402

NOTE_BODY = (
    '---\ntitle: "A Paper"\n'
    "tags:\n  - type/paper\n  - area/robotics\n  - status/to-read\n"
    'authors: ["A"]\nyear: 2026\nvenue: "arXiv"\ndate: 2026-01-01\n'
)


def _note_text(key: str | None = "AAAABBBB") -> str:
    front = NOTE_BODY
    if key:
        front += f"zotero_key: {key}\n"
    return (
        front
        + "---\n\n"
        + "## 快捷链接\n"
        + "- [在 Zotero 中打开](zotero://select/library/items/AAAABBBB)\n\n"
        + "# A Paper\n\n## 1. Intro\n\n"
        + ("Body text long enough to pass the length gate. " * 12)
        + "\n\n## 关联\n\n（暂无）\n"
    )


class KeyAliveTest(unittest.TestCase):
    def _run(self, *, key: str | None, deleted: bool, not_found: bool = False,
             note_name: str = "A Paper.md"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        notes = Path(tmp.name)
        (notes / note_name).write_text(_note_text(key), encoding="utf-8")

        config = SimpleNamespace(
            literature_notes=str(notes),
            vault=str(notes),
            api_url="http://127.0.0.1:23119",
            user_id="0",
            mineru_cache=str(notes),
        )

        api = MagicMock()
        api.get_children.return_value = []
        if not_found:
            api.get_item.side_effect = ZoteroAPIError("404 not found")
        else:
            api.get_item.return_value = {
                "data": {"title": "A Paper", "deleted": deleted},
                "version": 1,
            }

        args = SimpleNamespace(key=key or "AAAABBBB", json=True)
        out = io.StringIO()
        with patch("zotero.commands.cmd_verify_note.ZoteroAPI", return_value=api):
            with patch("zotero.commands.cmd_verify_note.MineruLookup") as lookup:
                lookup.return_value.find_cache_dir.return_value = None
                with redirect_stdout(out), redirect_stderr(io.StringIO()):
                    code = cmd_verify_note._Run(args, config)
        try:
            payload = json.loads(out.getvalue())
        except json.JSONDecodeError:
            payload = {"checks": []}
        return code, payload

    def _named(self, payload: dict, name: str) -> dict | None:
        for check in payload.get("checks", []):
            if check.get("name") == name:
                return check
        return None

    def test_trashed_key_fails_verification(self) -> None:
        """The regression: a merged-away key used to pass every check."""
        code, payload = self._run(key="AAAABBBB", deleted=True)
        alive = self._named(payload, "key_alive")
        self.assertIsNotNone(alive, "key_alive check must be emitted")
        self.assertFalse(alive["pass"])
        self.assertIn("trash", alive["msg"].lower())
        self.assertEqual(code, 1, "a trashed key must fail the command")

    def test_live_key_passes(self) -> None:
        code, payload = self._run(key="AAAABBBB", deleted=False)
        alive = self._named(payload, "key_alive")
        self.assertIsNotNone(alive)
        self.assertTrue(alive["pass"])

    def test_unresolvable_key_fails(self) -> None:
        code, payload = self._run(key="AAAABBBB", deleted=False, not_found=True)
        alive = self._named(payload, "key_alive")
        # The item fetch itself fails first, so verification stops early.
        self.assertEqual(code, 1)
        self.assertIsNotNone(self._named(payload, "item_fetch"))

    def test_missing_key_reports_key_present_failure(self) -> None:
        code, payload = self._run(key=None, deleted=False)
        present = self._named(payload, "zotero_key_present")
        self.assertIsNotNone(present)
        self.assertFalse(present["pass"])
        self.assertIsNone(self._named(payload, "key_alive"),
                          "no liveness check when there is no key")
        self.assertEqual(code, 1)

    def test_resolves_note_whose_filename_differs_from_its_title(self) -> None:
        """Regression: a filename the sanitiser would not produce was reported
        NOT FOUND, so healthy notes failed verification.

        The original fixture used a colon, which Windows forbids in a filename;
        the mismatch, not the character, is what the test needs.
        """
        code, payload = self._run(
            key="AAAABBBB", deleted=False, note_name="A Paper (2024).md"
        )
        exists = self._named(payload, "file_exists")
        self.assertIsNotNone(exists)
        self.assertTrue(exists["pass"], exists)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

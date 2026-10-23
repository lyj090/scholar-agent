#!/usr/bin/env python3
"""Tests for `write-note`'s dry-run contract.

Regression: `--dry-run` sat *after* the "note already exists" refusal, so on an
existing note it printed only the error and no path. Since the entire point of
dry-run is to report the target path, the one case where it mattered most was
the one case it refused to answer.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero.commands import cmd_write_note  # noqa: E402


class DryRunTest(unittest.TestCase):
    def _args(self, **over):
        base = dict(key="ABCD2345", content_file=None, force=False,
                    dry_run=True, json=False)
        base.update(over)
        return SimpleNamespace(**base)

    def _run(self, *, note_exists: bool, keep: bool = False):
        """Run write-note. ``keep=True`` returns the notes dir still populated."""
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        if True:
            notes = Path(tmp)
            title = "A Paper"
            if note_exists:
                (notes / f"{title}.md").write_text("old", encoding="utf-8")

            config = SimpleNamespace(
                literature_notes=str(notes),
                api_url="http://127.0.0.1:23119",
                user_id="0",
            )
            item = {"data": {"title": title}}
            out, err = io.StringIO(), io.StringIO()
            with patch("zotero.commands.cmd_write_note.ZoteroAPI",
                       return_value=MagicMock(get_item=lambda key: item)):
                with redirect_stdout(out), redirect_stderr(err):
                    code = cmd_write_note._Run(self._args(), config)
            listing = sorted(p.name for p in notes.iterdir())
            return code, out.getvalue(), err.getvalue(), listing

    def test_dry_run_reports_path_for_new_note(self) -> None:
        code, out, _err, listing = self._run(note_exists=False)
        self.assertEqual(code, 0)
        self.assertIn("A Paper.md", out.strip())
        self.assertEqual(listing, [])

    def test_dry_run_reports_path_even_when_note_exists(self) -> None:
        """This is the regression: it used to exit 1 with no path."""
        code, out, err, _listing = self._run(note_exists=True)
        self.assertEqual(code, 0, "dry-run must not fail on an existing note")
        self.assertIn("A Paper.md", out.strip(), "dry-run must still print the path")
        self.assertIn("exists", err.lower())
        self.assertIn("--force", err)

    def test_dry_run_writes_nothing(self) -> None:
        code, _out, _err, listing = self._run(note_exists=False)
        self.assertEqual(code, 0)
        self.assertEqual(listing, [], "dry-run must not create the file")


if __name__ == "__main__":
    unittest.main()

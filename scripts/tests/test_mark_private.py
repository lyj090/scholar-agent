#!/usr/bin/env python3
"""`mark-private` inserts the explicit divider above a legacy personal section.

Regression: publication used to cut at a recognised heading. A private section
titled like paper content (`## 10. 证据边界与复现限制`) slipped through. The
command rewrites legacy notes to carry the positive marker instead.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from zotero.commands import cmd_mark_private as MP  # noqa: E402


class MarkPrivateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.notes = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _note(self, name, body):
        (self.notes / name).write_text(body, encoding="utf-8")

    def _run(self, apply=False, file=""):
        class Args:
            json = True
            notes_dir = str(self.notes)
            dry_run = not apply
        Args.apply = apply
        Args.file = file
        config = type("C", (), {"literature_notes": str(self.notes)})()
        return MP._Run(Args(), config)

    def _capture(self, fn):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn()
        import json
        return json.loads(buf.getvalue())

    def test_divider_inserted_above_legacy_heading(self):
        self._note("A.md", "## 1. Intro\ntext\n\n## 9. 项目关联与本笔记判读\n\nhidden\n")
        out = self._capture(lambda: self._run(apply=True))
        self.assertEqual(out["changed_count"], 1)
        text = (self.notes / "A.md").read_text(encoding='utf-8')
        self.assertIn("<!-- private -->", text)
        # divider must sit above the heading, everything after preserved
        idx = text.index("<!-- private -->")
        self.assertLess(idx, text.index("项目关联"))
        self.assertIn("hidden", text)

    def test_dry_run_writes_nothing(self):
        self._note("A.md", "## 1. Intro\n\n## 9. 项目关联与本笔记判读\nx\n")
        before = (self.notes / "A.md").read_text(encoding='utf-8')
        out = self._capture(lambda: self._run(apply=False))
        self.assertEqual(out["changed_count"], 1)
        self.assertEqual((self.notes / "A.md").read_text(encoding='utf-8'), before)

    def test_note_with_marker_is_skipped(self):
        self._note("A.md", "## 1. Intro\n\n<!-- private -->\n\n## 9. 项目关联\nx\n")
        out = self._capture(lambda: self._run(apply=True))
        self.assertEqual(out["changed_count"], 0)

    def test_note_without_personal_section_is_skipped(self):
        self._note("A.md", "## 1. Intro\ntext\n\n## 2. Method\ntext\n")
        out = self._capture(lambda: self._run(apply=True))
        self.assertEqual(out["changed_count"], 0)

    def test_fenced_heading_does_not_move_the_divider(self):
        # a heading inside a code fence is documentation, not the private tail
        body = "## 1. Intro\n\n```md\n## 9. 项目关联与本笔记判读\n```\n\n## 2. Method\nx\n"
        self._note("A.md", body)
        out = self._capture(lambda: self._run(apply=True))
        self.assertEqual(out["changed_count"], 0)
        self.assertNotIn("<!-- private -->", (self.notes / "A.md").read_text(encoding='utf-8'))


if __name__ == "__main__":
    unittest.main()

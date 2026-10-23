#!/usr/bin/env python3
"""Regression tests for the lightweight Zotero/Obsidian audit helpers."""

from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.commands.cmd_audit_notes import _AuditFile  # noqa: E402
from zotero.commands.cmd_audit_tags import _ExtractTags  # noqa: E402


class ZoteroAuditTest(unittest.TestCase):
    def test_tag_audit_uses_yaml_for_quoted_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            path.write_text(
                '---\ntags: ["topic/a,b", area/robotics]\n---\n',
                encoding="utf-8",
            )
            self.assertEqual(_ExtractTags(str(path)), ["topic/a,b", "area/robotics"])

    def test_quick_note_audit_skips_image_check_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            path.write_text(
                "---\n"
                "title: Test\n"
                "tags:\n  - type/paper\n  - area/robotics\n  - status/to-read\n"
                "authors: [Author]\n"
                "year: 2026\n"
                "venue: Test\n"
                "date: 2026\n"
                "---\n"
                "## 关联\n"
                + ("Useful body text. " * 20)
                + "\n![](assets/missing.png)\n",
                encoding="utf-8",
            )
            normal = _AuditFile(str(path), path.name, directory)
            quick = _AuditFile(str(path), path.name, directory, check_images=False)
            self.assertTrue(any("missing image" in issue for issue in normal))
            self.assertFalse(any("missing image" in issue for issue in quick))


if __name__ == "__main__":
    unittest.main()

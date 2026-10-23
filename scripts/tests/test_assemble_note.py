#!/usr/bin/env python3
"""Regression tests for note assembly and section replacement.

Covered defects:

* ``assemble-note`` never called ``finalize_note_links``. The spec routes long
  notes (>15K chars) through it and explicitly forbids the agent from writing
  ``## 快捷链接`` by hand, so every long note came out without a ``zotero_key``
  or quick links and then failed ``verify-note``.
* ``update-section`` rewrote one body section but left a note that predated link
  injection without keys or links, for the same reason.
* ``update-section`` located headings by splitting on ``"\\n"`` of the original
  text; the injected links must not shift or eat the target section.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero.commands import cmd_assemble_note, cmd_update_section  # noqa: E402
from zotero.link_sync import (  # noqa: E402
    extract_zotero_key_from_content,
)

ITEM = {
    "data": {
        "title": "A Paper",
        "tags": [],
    }
}
PDF = {"key": "PDFKEY12", "data": {"contentType": "application/pdf"}}


def _api_mock() -> MagicMock:
    api = MagicMock()
    api.get_item.return_value = ITEM
    api.get_children.return_value = [PDF]
    return api


class AssembleNoteLinksTest(unittest.TestCase):
    def _run(self, *, yaml_text: str) -> tuple[int, str, str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        notes = root / "notes"
        notes.mkdir()
        yaml_file = root / "yaml.md"
        yaml_file.write_text(yaml_text, encoding="utf-8")
        section = root / "s1.md"
        section.write_text("## 1. Intro\n\nBody text.\n", encoding="utf-8")

        config = SimpleNamespace(
            literature_notes=str(notes),
            vault=str(root),
            api_url="http://127.0.0.1:23119",
            user_id="0",
        )
        args = SimpleNamespace(
            key="AAAABBBB",
            yaml=str(yaml_file),
            sections=[str(section)],
            force=True,
            dry_run=False,
        )
        out, err = io.StringIO(), io.StringIO()
        with patch("zotero.commands.cmd_assemble_note.ZoteroAPI", return_value=_api_mock()):
            with redirect_stdout(out), redirect_stderr(err):
                code = cmd_assemble_note._Run(args, config)
        written = notes / "A Paper.md"
        return code, (written.read_text(encoding="utf-8") if written.exists() else ""), err.getvalue()

    def test_injects_zotero_key_and_quick_links(self) -> None:
        """Regression: assembled notes had neither."""
        yaml_text = (
            '---\ntitle: "A Paper"\n'
            "tags:\n  - type/paper\n  - area/robotics\n"
            "authors: [Author]\nyear: 2026\n"
            'venue: "arXiv"\ndate: 2026-01-01\n---\n'
        )
        code, content, err = self._run(yaml_text=yaml_text)
        self.assertEqual(code, 0, err)
        self.assertEqual(extract_zotero_key_from_content(content), "AAAABBBB")
        self.assertIn("## 快捷链接", content)
        self.assertIn("zotero://select/library/items/AAAABBBB", content)
        self.assertIn("zotero_pdf_key: PDFKEY12", content)
        self.assertIn("Body text.", content)

    def test_injection_is_idempotent_when_note_is_reassembled(self) -> None:
        yaml_text = (
            '---\ntitle: "A Paper"\ntags:\n  - type/paper\n---\n'
        )
        _code, first, _err = self._run(yaml_text=yaml_text)
        _code, second, _err = self._run(yaml_text=yaml_text)
        self.assertEqual(first, second)


class UpdateSectionLinksTest(unittest.TestCase):
    def _run(self, original: str, *, heading: str = "## 2. Method",
             new_body: str = "## 2. Method\n\nRewritten.\n") -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        note = root / "note.md"
        note.write_text(original, encoding="utf-8")
        content_file = root / "new.md"
        content_file.write_text(new_body, encoding="utf-8")

        config = SimpleNamespace(
            vault=str(root),
            literature_notes=str(root),
            api_url="http://127.0.0.1:23119",
            user_id="0",
        )
        args = SimpleNamespace(
            note_path=str(note),
            heading=heading,
            content_file=str(content_file),
            dry_run=False,
        )
        out, err = io.StringIO(), io.StringIO()
        with patch("zotero.commands.cmd_update_section.ZoteroAPI", return_value=_api_mock()):
            with redirect_stdout(out), redirect_stderr(err):
                code = cmd_update_section._Run(args, config)
        self.assertEqual(code, 0, err.getvalue())
        return note.read_text(encoding="utf-8")

    def test_backfills_links_on_a_note_that_lacks_them(self) -> None:
        """Regression: the note kept its old section but gained no links."""
        original = (
            '---\ntitle: "A Paper"\nzotero_key: AAAABBBB\n---\n\n'
            "# A Paper\n\n## 1. Intro\n\nKeep me.\n\n## 2. Method\n\nOld text.\n"
        )
        out = self._run(original)
        self.assertIn("## 快捷链接", out)
        self.assertIn("zotero://select/library/items/AAAABBBB", out)
        self.assertEqual(extract_zotero_key_from_content(out), "AAAABBBB")

    def test_replaces_only_the_target_section(self) -> None:
        original = (
            '---\ntitle: "A Paper"\nzotero_key: AAAABBBB\n---\n\n'
            "# A Paper\n\n## 1. Intro\n\nKeep me.\n\n## 2. Method\n\nOld text.\n\n"
            "## 3. Results\n\nAlso keep me.\n"
        )
        out = self._run(original)
        self.assertIn("Rewritten.", out)
        self.assertNotIn("Old text.", out)
        self.assertIn("Keep me.", out)
        self.assertIn("Also keep me.", out)

    def test_injected_links_do_not_become_the_replaced_section(self) -> None:
        """The quick-links block is inserted before heading lookup; it must not
        be treated as part of the section being rewritten."""
        original = (
            '---\ntitle: "A Paper"\nzotero_key: AAAABBBB\n---\n\n'
            "## 2. Method\n\nOld text.\n"
        )
        out = self._run(original)
        self.assertIn("## 快捷链接", out)
        self.assertIn("zotero://select/library/items/AAAABBBB", out)
        self.assertNotIn("Old text.", out)
        self.assertIn("Rewritten.", out)

    def test_is_idempotent(self) -> None:
        original = (
            '---\ntitle: "A Paper"\nzotero_key: AAAABBBB\n---\n\n'
            "## 2. Method\n\nOld text.\n"
        )
        once = self._run(original)
        twice = self._run(once)
        self.assertEqual(once, twice)

    def test_content_file_with_its_own_heading_does_not_duplicate_it(self) -> None:
        """Regression: the command writes ``--heading`` itself, so a content
        file that also began with it produced two identical headings — and
        because the next run replaced from the first match, every edit added
        another copy."""
        original = (
            '---\ntitle: "A Paper"\nzotero_key: AAAABBBB\n---\n\n'
            "## 2. Method\n\nOld text.\n"
        )
        out = self._run(original, new_body="## 2. Method\n\nRewritten.\n")
        self.assertEqual(out.count("## 2. Method"), 1)

    def test_heading_and_bodiless_forms_converge(self) -> None:
        """With or without the heading in the content file, the result is the
        same — so a caller that repeats it is not progressively corrupted."""
        original = (
            '---\ntitle: "A Paper"\nzotero_key: AAAABBBB\n---\n\n'
            "## 2. Method\n\nOld text.\n"
        )
        with_heading = self._run(original, new_body="## 2. Method\n\nRewritten.\n")
        without_heading = self._run(original, new_body="Rewritten.\n")
        self.assertEqual(with_heading, without_heading)

    def test_repeated_edits_do_not_accumulate_headings(self) -> None:
        original = (
            '---\ntitle: "A Paper"\nzotero_key: AAAABBBB\n---\n\n'
            "## 2. Method\n\nOld text.\n"
        )
        content = original
        for _ in range(4):
            content = self._run(content, new_body="## 2. Method\n\nRewritten.\n")
        self.assertEqual(content.count("## 2. Method"), 1)
        self.assertEqual(content.count("Rewritten."), 1)

    def test_note_without_a_zotero_key_is_left_alone(self) -> None:
        original = '---\ntitle: "A Paper"\n---\n\n## 2. Method\n\nOld text.\n'
        out = self._run(original)
        self.assertNotIn("## 快捷链接", out)
        self.assertIn("Rewritten.", out)


if __name__ == "__main__":
    unittest.main()

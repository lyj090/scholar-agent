#!/usr/bin/env python3
"""Regression tests for note path resolution.

Covered defect: every note command derived the filename from the *current*
Zotero title with its own private copy of the sanitiser, and none of them ever
looked at the note already on disk. Two consequences, both silent:

* ``assemble-note``'s copy had drifted (it omitted ``$`` and ``^``), so the same
  paper could map to two different filenames depending on which command ran.
* A note whose filename kept a character the sanitiser strips — every note for a
  title containing ``:`` — could not be found at all. ``verify-note`` reported
  "NOT FOUND" for 15 real, healthy notes, and ``write-note``/``prepare-note``
  would have created a second note for the same paper.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero.frontmatter_tags import (  # noqa: E402
    find_note_by_zotero_key,
    note_filename_for_title,
    resolve_note_path,
)


class NoteFilenameTest(unittest.TestCase):
    def test_strips_obsidian_hostile_characters(self) -> None:
        self.assertEqual(
            note_filename_for_title("Retargeting Matters: General Motion Retargeting"),
            "Retargeting Matters General Motion Retargeting.md",
        )

    def test_strips_latex_math_markers(self) -> None:
        """``$``/``^`` break Obsidian wikilinks and must be removed."""
        self.assertEqual(
            note_filename_for_title("M$^2$E-UAV: A Benchmark"),
            "M2E-UAV A Benchmark.md",
        )

    def test_preserves_title_case_and_spaces(self) -> None:
        self.assertEqual(
            note_filename_for_title("cuRobo: Parallelized Collision-Free"),
            "cuRobo Parallelized Collision-Free.md",
        )

    def test_handles_path_separators(self) -> None:
        self.assertEqual(note_filename_for_title("A/B\\C"), "ABC.md")

    def test_empty_title(self) -> None:
        self.assertEqual(note_filename_for_title(""), ".md")


class NoteResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.notes = Path(self._tmp.name)

    def _note(self, name: str, key: str = "AAAABBBB") -> Path:
        path = self.notes / name
        path.write_text(
            f'---\ntitle: "X"\nzotero_key: {key}\n---\n\n# Body\n',
            encoding="utf-8",
        )
        return path

    def test_title_derived_name_wins(self) -> None:
        expected = self._note("A Paper.md")
        self.assertEqual(resolve_note_path(str(self.notes), "A Paper", "AAAABBBB"),
                         str(expected))

    def test_falls_back_to_zotero_key_when_filename_differs(self) -> None:
        """The regression: the filename differs from the title-derived name.

        The original fixture used ``A: Paper.md`` to model the colon that Zotero
        puts in titles. Windows forbids ``:`` in a filename, so the test could
        not create the file it then asserted on. A trailing marker reproduces
        the same mismatch without an invalid character.
        """
        actual = self._note("A Paper (2024).md")
        self.assertEqual(resolve_note_path(str(self.notes), "A Paper", "AAAABBBB"),
                         str(actual))

    def test_find_by_key_ignores_unrelated_notes(self) -> None:
        self._note("Other.md", key="ZZZZ9999")
        wanted = self._note("Whatever.md", key="AAAABBBB")
        self.assertEqual(find_note_by_zotero_key(str(self.notes), "AAAABBBB"),
                         str(wanted))

    def test_find_by_key_returns_none_when_absent(self) -> None:
        self._note("Other.md", key="ZZZZ9999")
        self.assertIsNone(find_note_by_zotero_key(str(self.notes), "AAAABBBB"))

    def test_find_by_key_skips_files_without_frontmatter(self) -> None:
        (self.notes / "loose.md").write_text("# No frontmatter\n", encoding="utf-8")
        self.assertIsNone(find_note_by_zotero_key(str(self.notes), "AAAABBBB"))

    def test_find_by_key_ignores_non_markdown(self) -> None:
        (self.notes / "note.txt").write_text(
            "zotero_key: AAAABBBB\n", encoding="utf-8"
        )
        self.assertIsNone(find_note_by_zotero_key(str(self.notes), "AAAABBBB"))

    def test_resolution_returns_none_for_missing_note(self) -> None:
        self.assertIsNone(resolve_note_path(str(self.notes), "Nope", "AAAABBBB"))

    def test_resolution_survives_missing_directory(self) -> None:
        self.assertIsNone(resolve_note_path("/nonexistent/dir", "Nope", "AAAABBBB"))
        self.assertIsNone(find_note_by_zotero_key("/nonexistent/dir", "AAAABBBB"))

    def test_find_by_key_handles_quoted_value(self) -> None:
        path = self.notes / "Q.md"
        path.write_text(
            '---\ntitle: "X"\nzotero_key: "AAAABBBB"\n---\n', encoding="utf-8"
        )
        # A quoted key is not the canonical shape the writers emit; resolution
        # must not crash and must simply not match.
        self.assertIsNone(find_note_by_zotero_key(str(self.notes), "AAAABBBB"))
        self.assertEqual(resolve_note_path(str(self.notes), "Q", "AAAABBBB"),
                         str(path))

    def test_empty_key_returns_none(self) -> None:
        self._note("A.md")
        self.assertIsNone(find_note_by_zotero_key(str(self.notes), ""))


if __name__ == "__main__":
    unittest.main()

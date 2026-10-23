#!/usr/bin/env python3
"""Tests for exact MinerU cache discovery and bounded section reads."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.mineru_catalog import (  # noqa: E402
    MineruAmbiguousError,
    MineruCatalog,
    MineruNoteIndex,
)


class FakeAPI:
    def __init__(
        self,
        default_attachment: str | None = None,
        items: dict[str, dict] | None = None,
    ):
        self.default_attachment = default_attachment
        self.items = items or {}

    def get_item(self, key: str) -> dict:
        if key in self.items:
            return self.items[key]
        if not self.default_attachment:
            return {"links": {}}
        return {
            "links": {
                "attachment": {
                    "href": f"http://localhost/items/{self.default_attachment}"
                }
            }
        }


class MineruCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_cache(
        self,
        cache_id: int,
        attachment_key: str,
        parent_key: str,
        filename: str,
        markdown: str,
    ) -> None:
        cache_dir = self.root / str(cache_id)
        cache_dir.mkdir()
        (cache_dir / "full.md").write_text(markdown, encoding="utf-8")
        (cache_dir / "_llm_source.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "attachmentKey": attachment_key,
                    "parentItemKey": parent_key,
                    "sourceFilename": filename,
                    "parsedAt": f"2026-01-{cache_id:02d}T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    def test_exact_attachment_key_beats_parent_title_guessing(self) -> None:
        self._write_cache(1, "CHAPTER1", "BOOKKEY1", "Chapter 1.pdf", "## Chapter 1\nA")
        self._write_cache(2, "CHAPTER2", "BOOKKEY1", "Chapter 2.pdf", "## Chapter 2\nB")
        catalog = MineruCatalog(str(self.root))
        self.assertEqual(catalog.resolve("CHAPTER2").cache_id, 2)

    def test_parent_with_multiple_chapters_is_ambiguous(self) -> None:
        self._write_cache(1, "CHAPTER1", "BOOKKEY1", "Chapter 1.pdf", "## Chapter 1\nA")
        self._write_cache(2, "CHAPTER2", "BOOKKEY1", "Chapter 2.pdf", "## Chapter 2\nB")
        catalog = MineruCatalog(str(self.root))
        with self.assertRaises(MineruAmbiguousError):
            catalog.resolve("BOOKKEY1")
        self.assertEqual(catalog.resolve("BOOKKEY1", match="Chapter 2").cache_id, 2)

    def test_parent_uses_explicit_default_attachment_when_available(self) -> None:
        self._write_cache(1, "CHAPTER1", "BOOKKEY1", "Chapter 1.pdf", "## Chapter 1\nA")
        self._write_cache(2, "CHAPTER2", "BOOKKEY1", "Chapter 2.pdf", "## Chapter 2\nB")
        catalog = MineruCatalog(str(self.root), FakeAPI("CHAPTER2"))
        self.assertEqual(catalog.resolve("BOOKKEY1").cache_id, 2)

    def test_title_skips_leading_image_and_reads_first_heading(self) -> None:
        self._write_cache(
            1,
            "CHAPTER1",
            "BOOKKEY1",
            "Chapter 1.pdf",
            "![](images/cover.jpg)\n\n## Useful Title\nBody",
        )
        self.assertEqual(MineruCatalog(str(self.root)).records()[0].title, "Useful Title")

    def test_outline_section_span_includes_subsections_only(self) -> None:
        markdown = (
            "## 6.1 Mean estimation\nIntro\n"
            "### 6.1.1 Incremental form\nDetails\n"
            "## 6.2 Robbins-Monro\nRM body\n"
            "## 6.3 SGD\nSGD body\n"
        )
        self._write_cache(1, "CHAPTER1", "BOOKKEY1", "Chapter 6.pdf", markdown)
        catalog = MineruCatalog(str(self.root))
        record = catalog.resolve("CHAPTER1")
        self_only = catalog.read(record, section="6.1")
        self.assertNotIn("6.1.1 Incremental form", self_only.content)
        subtree = catalog.read(record, section="6.1", scope="subtree")
        self.assertIn("6.1.1 Incremental form", subtree.content)
        self.assertNotIn("6.2 Robbins-Monro", subtree.content)

    def test_note_index_recognizes_zotero_pdf_link(self) -> None:
        self._write_cache(1, "CHAPTER1", "BOOKKEY1", "Chapter 1.pdf", "## Chapter 1\nA")
        vault = self.root / "vault"
        vault.mkdir()
        (vault / "note.md").write_text(
            "[原文](zotero://open-pdf/library/items/CHAPTER1)", encoding="utf-8"
        )
        record = MineruCatalog(str(self.root)).resolve("CHAPTER1")
        self.assertTrue(MineruNoteIndex(str(vault)).has_note(record))

    def test_logical_groups_use_pdf_md5_and_prefer_richer_parent(self) -> None:
        self._write_cache(1, "CHAPTER1", "BOOKKEY1", "Chapter 1.pdf", "## Chapter 1\nA")
        self._write_cache(2, "CHAPTER2", "BOOKKEY2", "Chapter 1.pdf", "## Chapter 1\nA")
        api = FakeAPI(
            items={
                "CHAPTER1": {"data": {"md5": "same-pdf"}},
                "CHAPTER2": {"data": {"md5": "same-pdf"}},
                "BOOKKEY1": {"data": {"title": "book", "creators": []}},
                "BOOKKEY2": {
                    "data": {
                        "title": "A complete and descriptive book title",
                        "creators": [{"lastName": "Author"}],
                        "date": "2026",
                        "ISBN": "123",
                    }
                },
            }
        )
        catalog = MineruCatalog(str(self.root), api)
        groups = catalog.logical_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(catalog.representative(groups[0]).parent_item_key, "BOOKKEY2")


if __name__ == "__main__":
    unittest.main()

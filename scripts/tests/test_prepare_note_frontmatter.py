#!/usr/bin/env python3
"""Regression tests for the frontmatter emitted by ``prepare-note``.

Two defects are covered:

* The SOP tells the agent to copy ``frontmatter`` from ``prepare-note --json``
  verbatim and states it "includes ``zotero_key`` / ``zotero_pdf_key``". It did
  not: both were added later, by ``write-note``/``assemble-note`` link
  injection, so an agent that hand-wrote the note from this block produced one
  with no key at all.
* The PDF value must be the attachment **key** (8 uppercase alphanumerics),
  because it feeds ``zotero://open-pdf/library/items/<key>`` and
  ``link_sync``/``verify-note``. ``_ResolvePdfPath`` returns an absolute
  filesystem path and is for reading the PDF locally — wiring it in produced
  ``zotero_pdf_key: /home/.../file.pdf``.
"""

from __future__ import annotations

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

from zotero.commands import cmd_prepare_note  # noqa: E402

ITEM_KEY = "ITEM0001"
PDF_KEY = "PDF00001"


class PrepareNoteFrontmatterTest(unittest.TestCase):
    def _frontmatter(self, *, pdf_key: str | None = PDF_KEY, selected: bool = False) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)/"cache"
            cache.mkdir()
            (cache/"full.md").write_text("# Introduction\nA synthetic paper.\n", encoding='utf-8')
            config = SimpleNamespace(
                vault=tmp,
                literature_notes=tmp,
                mineru_cache=str(cache),
                api_url="http://127.0.0.1:23119",
                user_id="0",
            )
            item = {
                "data": {
                    "title": "Synthetic Paper",
                    "creators": [],
                    "tags": [],
                    "date": "2025",
                    "DOI": "10.0000/example",
                    "libraryCatalog": "arXiv",
                }
            }
            api = MagicMock()
            api.get_item.side_effect = lambda key: {"data": {"parentItem": ITEM_KEY, "contentType": "application/pdf"}} if key == PDF_KEY else item
            api.get_children.return_value = (
                [{"key": pdf_key, "data": {"contentType": "application/pdf"}}]
                if pdf_key
                else []
            )
            args = SimpleNamespace(key=ITEM_KEY, force=True, json=True, attachment_key=PDF_KEY if selected else None)
            out = __import__("io").StringIO()
            with patch("zotero.commands.cmd_prepare_note.ZoteroAPI", return_value=api), patch("zotero.commands.cmd_prepare_note.MineruLookup") as lookup:
                lookup.return_value.find_cache_dir.return_value = str(cache)
                with redirect_stdout(out), redirect_stderr(__import__("io").StringIO()):
                    code = cmd_prepare_note._Run(args, config)
            self.assertEqual(code, 0)
            lookup.return_value.find_cache_dir.assert_called_once_with(PDF_KEY if selected else ITEM_KEY)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["status"], "ready")
            return payload["frontmatter"]

    def test_exact_attachment_survives_prepare(self) -> None:
        fm = self._frontmatter(selected=True)
        self.assertIn(f"zotero_pdf_key: {PDF_KEY}", fm)

    def test_emits_zotero_key(self) -> None:
        fm = self._frontmatter()
        self.assertIn(f"zotero_key: {ITEM_KEY}", fm)

    def test_emits_pdf_key_not_a_filesystem_path(self) -> None:
        fm = self._frontmatter()
        self.assertIn(f"zotero_pdf_key: {PDF_KEY}", fm)
        # The regression: a path leaked into the key field.
        self.assertNotIn("/home/", fm)
        self.assertNotIn(".pdf", fm)

    def test_omits_pdf_key_when_there_is_no_pdf(self) -> None:
        fm = self._frontmatter(pdf_key=None)
        self.assertIn("zotero_key:", fm)
        self.assertNotIn("zotero_pdf_key", fm)

    def test_frontmatter_parses_as_yaml(self) -> None:
        import yaml

        fm = self._frontmatter()
        body = fm.strip()
        self.assertTrue(body.startswith("---"))
        self.assertTrue(body.endswith("---"))
        parsed = yaml.safe_load(body.strip("-").strip())
        self.assertEqual(str(parsed["zotero_key"]), ITEM_KEY)
        self.assertEqual(str(parsed["zotero_pdf_key"]), PDF_KEY)

    def test_frontmatter_round_trips_through_link_injection(self) -> None:
        """The block must survive the injection that write-note applies."""
        from zotero.link_sync import ensure_frontmatter_zotero_keys

        fm = self._frontmatter()
        content = fm + "\n\n# Body\n"
        once = ensure_frontmatter_zotero_keys(content, ITEM_KEY, PDF_KEY)
        twice = ensure_frontmatter_zotero_keys(once, ITEM_KEY, PDF_KEY)
        self.assertEqual(once, twice)
        self.assertIn(f"zotero_pdf_key: {PDF_KEY}", once)
        self.assertIn("# Body", once)


if __name__ == "__main__":
    unittest.main()

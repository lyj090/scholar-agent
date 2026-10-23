#!/usr/bin/env python3
"""Tests for the plugin-free arXiv/DOI import path.

Import no longer builds a Zotero-internal JS translator script; it fetches
metadata itself and writes through the native write API. These tests pin the
pieces that decide whether an import is safe: identifier classification, correct
targeting of fetched metadata, and refusal to create a duplicate.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from arxiv.commands.cmd_import import (  # noqa: E402
    _AttachPdf,
    _ClassifyTarget,
    _CleanArxivId,
    _DefaultPdfDir,
    _DownloadPdf,
    _ImportOneItem,
    _PdfFilename,
)
from zotero.local_write import ZoteroWriteError  # noqa: E402
from zotero.metadata_fetch import MetadataError  # noqa: E402


class ClassifyTargetTest(unittest.TestCase):
    def test_plain_arxiv_id(self) -> None:
        self.assertEqual(
            _ClassifyTarget("2405.20321"),
            {"type": "arxiv", "identifier": "2405.20321", "title": ""},
        )

    def test_arxiv_url_and_version_suffix(self) -> None:
        for target in ("https://arxiv.org/abs/2405.20321v2",
                       "https://arxiv.org/pdf/2405.20321"):
            self.assertEqual(_ClassifyTarget(target)["type"], "arxiv")
            self.assertEqual(_ClassifyTarget(target)["identifier"], "2405.20321")

    def test_doi_forms(self) -> None:
        self.assertEqual(_ClassifyTarget("10.1234/abc")["type"], "doi")
        self.assertEqual(
            _ClassifyTarget("https://doi.org/10.1234/abc")["identifier"], "10.1234/abc"
        )

    def test_unrecognised_target_is_none(self) -> None:
        self.assertIsNone(_ClassifyTarget("just some words"))

    def test_clean_arxiv_id_strips_version(self) -> None:
        self.assertEqual(_CleanArxivId("2405.20321v3"), "2405.20321")
        self.assertEqual(_CleanArxivId("2405.20321"), "2405.20321")


class ImportOneItemTest(unittest.TestCase):
    """`_ImportOneItem` decides whether anything is written at all."""

    def test_metadata_failure_writes_nothing(self) -> None:
        """A bad identifier must fail before a half-created item exists."""
        with patch("arxiv.commands.cmd_import.fetch_metadata",
                   side_effect=MetadataError("no such paper")):
            with patch("arxiv.commands.cmd_import.load_config") as load:
                ok, msg = _ImportOneItem(
                    {"type": "arxiv", "identifier": "9999.99999"}, "ABCD1234"
                )
        self.assertFalse(ok)
        self.assertIn("metadata fetch failed", msg)
        load.assert_not_called()

    def test_unknown_type_is_refused(self) -> None:
        ok, msg = _ImportOneItem({"type": "isbn", "identifier": "x"}, "ABCD1234")
        self.assertFalse(ok)
        self.assertIn("unknown type", msg)

    def test_existing_item_is_not_duplicated(self) -> None:
        """Re-importing must file the existing item, not create a second one."""
        entry = type("E", (), {"item_key": "EXIST001"})()
        client = MagicMock()
        with patch("arxiv.commands.cmd_import.fetch_metadata",
                   return_value={"itemType": "preprint", "title": "T"}):
            with patch("arxiv.commands.cmd_import.load_config"):
                with patch("arxiv.commands.cmd_import.build_write_client",
                           return_value=client):
                    with patch("arxiv.commands.cmd_import._ExistingEntry",
                               return_value=entry):
                        ok, msg = _ImportOneItem(
                            {"type": "arxiv", "identifier": "2405.20321"}, "ABCD1234"
                        )
        self.assertTrue(ok)
        self.assertIn("ALREADY PRESENT", msg)
        client.create_items.assert_not_called()
        client.add_to_collection.assert_called_once_with("EXIST001", "ABCD1234")

    def test_new_item_is_created_and_filed(self) -> None:
        client = MagicMock()
        client.create_items.return_value = ["NEWKEY01"]
        with patch("arxiv.commands.cmd_import.fetch_metadata",
                   return_value={"itemType": "preprint", "title": "A Paper"}):
            with patch("arxiv.commands.cmd_import.load_config"):
                with patch("arxiv.commands.cmd_import.build_write_client",
                           return_value=client):
                    with patch("arxiv.commands.cmd_import._ExistingEntry",
                               return_value=None):
                        ok, msg = _ImportOneItem(
                            {"type": "arxiv", "identifier": "2405.20321"},
                            "ABCD1234",
                            fetch_pdf=False,
                        )
        self.assertTrue(ok)
        self.assertIn("NEWKEY01", msg)
        client.add_to_collection.assert_called_once_with("NEWKEY01", "ABCD1234")

    def test_unfiled_item_is_reported_not_hidden(self) -> None:
        """If creation succeeds but filing fails, say so — a bare 'failed' would
        invite a retry that creates a duplicate."""
        client = MagicMock()
        client.create_items.return_value = ["NEWKEY01"]
        client.add_to_collection.side_effect = ZoteroWriteError("collection gone")
        with patch("arxiv.commands.cmd_import.fetch_metadata",
                   return_value={"itemType": "preprint", "title": "A Paper"}):
            with patch("arxiv.commands.cmd_import.load_config"):
                with patch("arxiv.commands.cmd_import.build_write_client",
                           return_value=client):
                    with patch("arxiv.commands.cmd_import._ExistingEntry",
                               return_value=None):
                        ok, msg = _ImportOneItem(
                            {"type": "arxiv", "identifier": "2405.20321"},
                            "ABCD1234",
                            fetch_pdf=False,
                        )
        self.assertFalse(ok)
        self.assertIn("created NEWKEY01", msg)
        self.assertIn("could not file", msg)


class AttachPdfTest(unittest.TestCase):
    """The PDF stage is what makes an import read-ready, not just a citation.

    Previously this path created metadata only, so every imported paper arrived
    with no child attachment and had to be completed by hand.
    """

    def _client(self) -> MagicMock:
        client = MagicMock()
        client.create_attachment.return_value = "ATT00001"
        return client

    def test_pdf_is_downloaded_and_uploaded(self) -> None:
        client = self._client()
        with patch("arxiv.commands.cmd_import._DownloadPdf",
                   return_value="/tmp/x.pdf") as download:
            note = _AttachPdf(
                client, "NEWKEY01",
                {"type": "arxiv", "identifier": "2405.20321"},
                None, None,
                [{"lastName": "Hines"}], "2026-10-12", "A Paper",
            )
        download.assert_called_once()
        client.create_attachment.assert_called_once()
        client.upload_file.assert_called_once_with("ATT00001", "/tmp/x.pdf")
        self.assertIn("PDF attached", note)

    def test_download_failure_does_not_fail_the_import(self) -> None:
        """A PDF that cannot be fetched must degrade, not discard good metadata."""
        client = self._client()
        with patch("arxiv.commands.cmd_import._DownloadPdf",
                   side_effect=ZoteroWriteError("HTTP 403")):
            note = _AttachPdf(
                client, "NEWKEY01",
                {"type": "arxiv", "identifier": "2405.20321"}, None, None,
            )
        self.assertIn("PDF NOT ATTACHED", note)
        self.assertIn("403", note)
        client.create_attachment.assert_not_called()

    def test_upload_failure_is_reported_not_raised(self) -> None:
        client = self._client()
        client.upload_file.side_effect = ZoteroWriteError("upload rejected")
        with patch("arxiv.commands.cmd_import._DownloadPdf",
                   return_value="/tmp/x.pdf"):
            note = _AttachPdf(
                client, "NEWKEY01",
                {"type": "arxiv", "identifier": "2405.20321"}, None, None,
            )
        self.assertIn("PDF NOT ATTACHED", note)

    def test_import_reports_missing_pdf_but_succeeds(self) -> None:
        client = MagicMock()
        client.create_items.return_value = ["NEWKEY01"]
        with patch("arxiv.commands.cmd_import.fetch_metadata",
                   return_value={"itemType": "preprint", "title": "A Paper"}):
            with patch("arxiv.commands.cmd_import.load_config"):
                with patch("arxiv.commands.cmd_import.build_write_client",
                           return_value=client):
                    with patch("arxiv.commands.cmd_import._ExistingEntry",
                               return_value=None):
                        with patch("arxiv.commands.cmd_import._DownloadPdf",
                                   side_effect=ZoteroWriteError("offline")):
                            ok, msg = _ImportOneItem(
                                {"type": "arxiv", "identifier": "2405.20321"},
                                "ABCD1234",
                            )
        self.assertTrue(ok, "metadata import must survive a PDF failure")
        self.assertIn("PDF NOT ATTACHED", msg)

    def test_existing_item_without_pdf_gets_one(self) -> None:
        """The pre-fix import left items PDF-less; re-import should complete them."""
        entry = type("E", (), {"item_key": "EXIST001", "has_pdf": False})()
        client = self._client()
        with patch("arxiv.commands.cmd_import.fetch_metadata",
                   return_value={"itemType": "preprint", "title": "T"}):
            with patch("arxiv.commands.cmd_import.load_config"):
                with patch("arxiv.commands.cmd_import.build_write_client",
                           return_value=client):
                    with patch("arxiv.commands.cmd_import._ExistingEntry",
                               return_value=entry):
                        with patch("arxiv.commands.cmd_import._DownloadPdf",
                                   return_value="/tmp/x.pdf"):
                            ok, msg = _ImportOneItem(
                                {"type": "arxiv", "identifier": "2405.20321"},
                                "ABCD1234",
                            )
        self.assertTrue(ok)
        self.assertIn("ALREADY PRESENT", msg)
        self.assertIn("PDF attached", msg)
        client.create_items.assert_not_called()

    def test_existing_item_with_pdf_is_left_alone(self) -> None:
        entry = type("E", (), {"item_key": "EXIST001", "has_pdf": True})()
        client = self._client()
        with patch("arxiv.commands.cmd_import.fetch_metadata",
                   return_value={"itemType": "preprint", "title": "T"}):
            with patch("arxiv.commands.cmd_import.load_config"):
                with patch("arxiv.commands.cmd_import.build_write_client",
                           return_value=client):
                    with patch("arxiv.commands.cmd_import._ExistingEntry",
                               return_value=entry):
                        with patch("arxiv.commands.cmd_import._DownloadPdf") as dl:
                            _ImportOneItem(
                                {"type": "arxiv", "identifier": "2405.20321"},
                                "ABCD1234",
                            )
        dl.assert_not_called()
        client.create_attachment.assert_not_called()


class DefaultPdfDirTest(unittest.TestCase):
    def test_is_repo_data_pdf_cache(self) -> None:
        """The staging directory must sit under ``<repo>/data``.

        An off-by-one in the ``dirname`` chain put it in ``scripts/data/``
        instead — a stray tree inside the source directory, created silently by
        the first import.
        """
        repo_root = Path(__file__).resolve().parents[2]
        self.assertEqual(
            Path(_DefaultPdfDir()).resolve(),
            (repo_root / "data" / "pdf-cache").resolve(),
        )


class PdfFilenameTest(unittest.TestCase):
    def test_author_year_title(self) -> None:
        self.assertEqual(
            _PdfFilename("The EventCV Library", [{"lastName": "Hines"}],
                         "2026-10-12", "2609.21330"),
            "Hines - 2026 - The EventCV Library.pdf",
        )

    def test_path_separators_are_stripped(self) -> None:
        """A title is attacker-controlled text, not a safe filename."""
        name = _PdfFilename("A: Weird/Title?*<>|", [], "", "1234.5678")
        for bad in '/\\:*?"<>|':
            self.assertNotIn(bad, name)

    def test_falls_back_to_identifier(self) -> None:
        self.assertEqual(_PdfFilename("", [], "", "1234.5678"), "1234.5678.pdf")

    def test_long_title_is_bounded(self) -> None:
        name = _PdfFilename("word " * 100, [], "", "")
        self.assertLessEqual(len(name), 130)


class DownloadPdfTest(unittest.TestCase):
    def test_non_pdf_response_is_rejected(self) -> None:
        """arXiv answers a bad ID with an HTML page and status 200. Writing it to
        a .pdf path would upload the error page as the paper."""
        import io
        from contextlib import contextmanager

        @contextmanager
        def fake_urlopen(request, timeout=None):
            response = MagicMock()
            response.read.return_value = b"<html>not found</html>"
            response.headers = {"Content-Type": "text/html"}
            yield response

        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(ZoteroWriteError) as ctx:
                _DownloadPdf("9999.99999", "arxiv", "/tmp/pdf-test-dir")
        self.assertIn("did not return a PDF", str(ctx.exception))

    def test_doi_is_refused_rather_than_guessed(self) -> None:
        with self.assertRaises(ZoteroWriteError) as ctx:
            _DownloadPdf("10.1109/x", "doi", "/tmp/pdf-test-dir")
        self.assertIn("only supported for arXiv", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

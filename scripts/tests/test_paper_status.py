#!/usr/bin/env python3
"""Tests for the local-first paper preflight (four-state classification)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.library_index import LibraryEntry, LibraryIndex  # noqa: E402
from zotero.paper_status import (  # noqa: E402
    ABSENT,
    IN_LIBRARY_NO_PDF,
    LOCAL_READY,
    LOCAL_UNPARSED,
    preflight,
)


class FakeRecord:
    def __init__(self, cache_id, parent, title="T", attachment="ATT00001"):
        self.cache_id = cache_id
        self.parent_item_key = parent
        self.title = title
        self.attachment_key = attachment
        self.cache_dir = f"/tmp/cache/{cache_id}"


class FakeCatalog:
    def __init__(self, records):
        self._records = records

    def records(self):
        return list(self._records)

    def candidates_for_key(self, key):
        """Mirror MineruCatalog: cache id, then attachment key, then parent."""
        ref = key.strip().upper()
        if ref.isdigit():
            return [r for r in self._records if r.cache_id == int(ref)]
        hits = [r for r in self._records if (r.attachment_key or "").upper() == ref]
        if hits:
            return hits
        return [r for r in self._records if (r.parent_item_key or "").upper() == ref]


class FakeConfig:
    api_url = "http://localhost:23199"
    user_id = "1"
    mineru_cache = "/tmp/cache"


class PreflightTest(unittest.TestCase):
    def _index(self, entries):
        return LibraryIndex(entries)

    def _run(self, refs, entries, records):
        return preflight(
            FakeConfig(),
            refs,
            catalog=FakeCatalog(records),
            index=self._index(entries),
        ).statuses

    def test_local_ready_when_cache_exists(self):
        entry = LibraryEntry(
            item_key="AAAA1111", title="Paper", item_type="preprint",
            arxiv_id="2405.20321", pdf_attachment_keys=["ATT00001"],
        )
        status = self._run(
            ["2405.20321"], [entry], [FakeRecord(7, "AAAA1111")]
        )[0]
        self.assertEqual(status.state, LOCAL_READY)
        self.assertEqual(status.cache_id, 7)
        self.assertTrue(status.is_local)

    def test_resolves_by_doi_and_url_too(self):
        entry = LibraryEntry(
            item_key="AAAA1111", title="Paper", item_type="preprint",
            doi="10.1234/x", pdf_attachment_keys=["ATT00001"],
        )
        records = [FakeRecord(7, "AAAA1111")]
        for ref in ("10.1234/x", "https://doi.org/10.1234/x"):
            status = self._run([ref], [entry], records)[0]
            self.assertEqual(status.state, LOCAL_READY, ref)

    def test_local_unparsed_when_pdf_but_no_cache(self):
        entry = LibraryEntry(
            item_key="BBBB2222", title="Paper", item_type="preprint",
            arxiv_id="2405.20321", pdf_attachment_keys=["ATT00002"],
        )
        status = self._run(["2405.20321"], [entry], [])[0]
        self.assertEqual(status.state, LOCAL_UNPARSED)
        self.assertTrue(status.is_local)
        self.assertEqual(status.pdf_attachment_keys, ["ATT00002"])

    def test_in_library_no_pdf(self):
        entry = LibraryEntry(
            item_key="CCCC3333", title="Paper", item_type="preprint",
            arxiv_id="2405.20321", pdf_attachment_keys=[],
        )
        status = self._run(["2405.20321"], [entry], [])[0]
        self.assertEqual(status.state, IN_LIBRARY_NO_PDF)
        # No PDF means no local read is possible yet.
        self.assertFalse(status.is_local)

    def test_absent_is_only_fetchable_state(self):
        status = self._run(["2609.99999"], [], [])[0]
        self.assertEqual(status.state, ABSENT)
        self.assertFalse(status.is_local)
        self.assertIn("web", status.action)

    def test_duplicate_caches_reported_as_candidates(self):
        entry = LibraryEntry(
            item_key="AAAA1111", title="Paper", item_type="preprint",
            arxiv_id="2405.20321", pdf_attachment_keys=["ATT00001"],
        )
        records = [
            FakeRecord(1, "AAAA1111"),
            FakeRecord(2, "AAAA1111"),
        ]
        status = self._run(["2405.20321"], [entry], records)[0]
        self.assertEqual(status.state, LOCAL_READY)
        self.assertEqual(sorted(status.candidates), [1, 2])

    def test_resolves_by_zotero_key_directly(self):
        entry = LibraryEntry(
            item_key="DDDD4444", title="Paper", item_type="preprint",
            pdf_attachment_keys=["ATT00004"],
        )
        status = self._run(["DDDD4444"], [entry], [FakeRecord(9, "DDDD4444")])[0]
        self.assertEqual(status.state, LOCAL_READY)
        self.assertEqual(status.item_key, "DDDD4444")

    def test_attachment_key_resolves_even_though_index_only_holds_top_items(self):
        """Regression: the library index contains top-level items only, so an
        attachment key never resolves there. Without a catalog fallback the same
        key that `mineru resolve` accepts was reported ABSENT — the exact signal
        that sends an agent to the web instead of the local cache."""
        entry = LibraryEntry(
            item_key="DDDD4444", title="Paper", item_type="preprint",
            pdf_attachment_keys=["PDFKEY01"],
        )
        records = [FakeRecord(11, "DDDD4444", attachment="PDFKEY01")]
        status = self._run(["PDFKEY01"], [entry], records)[0]
        self.assertEqual(status.state, LOCAL_READY)
        self.assertEqual(status.cache_id, 11)
        self.assertEqual(status.attachment_key, "PDFKEY01")
        # The parent is still reported, so the caller can address the paper.
        self.assertEqual(status.item_key, "DDDD4444")

    def test_bare_cache_id_resolves(self):
        records = [FakeRecord(42, "EEEE5555", attachment="ATT00042")]
        status = self._run(["42"], [], records)[0]
        self.assertEqual(status.state, LOCAL_READY)
        self.assertEqual(status.cache_id, 42)

    def test_unknown_key_is_still_absent(self):
        """The fallback must not turn every unknown key into a false positive."""
        status = self._run(["ZZZZ9999"], [], [FakeRecord(1, "AAAA1111")])[0]
        self.assertEqual(status.state, ABSENT)

    def test_batch_splits_states(self):
        entries = [
            LibraryEntry(item_key="A1", title="a", item_type="preprint",
                         arxiv_id="2405.00001", pdf_attachment_keys=["P1"]),
            LibraryEntry(item_key="A2", title="b", item_type="preprint",
                         arxiv_id="2405.00002", pdf_attachment_keys=[]),
        ]
        statuses = self._run(
            ["2405.00001", "2405.00002", "2405.00003"],
            entries,
            [FakeRecord(1, "A1")],
        )
        self.assertEqual(
            [s.state for s in statuses],
            [LOCAL_READY, IN_LIBRARY_NO_PDF, ABSENT],
        )

    def test_json_shape_is_stable(self):
        entry = LibraryEntry(
            item_key="AAAA1111", title="Paper", item_type="preprint",
            arxiv_id="2405.20321", pdf_attachment_keys=["ATT00001"],
        )
        status = self._run(["2405.20321"], [entry], [FakeRecord(7, "AAAA1111")])[0]
        payload = status.to_dict()
        for key in ("state", "action", "item_key", "cache_id", "attachment_key"):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()

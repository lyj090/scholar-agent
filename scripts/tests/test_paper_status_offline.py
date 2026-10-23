#!/usr/bin/env python3
"""Preflight must answer from local caches even with no readable library.

Regression: ``paper_status.preflight`` used to raise when ``build_library_index``
failed, which is exactly the state of a machine with no Zotero database and no
API. That turned "I cannot see your library" into "I cannot answer", and the
natural recovery from an exception in an agent loop is to go to the web — for a
paper that was already parsed on disk. The four-state classifier only needs the
library to map arXiv/DOI/title references; cache-key references resolve without
it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero import paper_status  # noqa: E402
from zotero.library_index import LibraryIndex  # noqa: E402
from zotero.paper_status import ABSENT, LOCAL_READY, preflight  # noqa: E402


class FakeRecord:
    def __init__(self, cache_id, attachment):
        self.cache_id = cache_id
        self.attachment_key = attachment
        self.parent_item_key = None
        self.title = "Cached Paper"
        self.cache_dir = f"/tmp/cache/{cache_id}"

    def to_dict(self):
        return {"cache_id": self.cache_id, "attachment_key": self.attachment_key}


class FakeCatalog:
    def __init__(self, records):
        self._records = records

    def records(self):
        return list(self._records)

    def candidates_for_key(self, key):
        for record in self._records:
            if key.upper() in {
                (record.attachment_key or "").upper(),
                str(record.cache_id),
            }:
                return [record]
        return []


class Config:
    api_url = "http://127.0.0.1:29999"
    user_id = "0"
    mineru_cache = "/tmp/cache"


class DegradedIndexTest(unittest.TestCase):
    def test_cache_key_resolves_when_index_build_fails(self) -> None:
        catalog = FakeCatalog([FakeRecord(4242, "DEMO4242")])
        with mock.patch(
            "zotero.library_index.build_library_index",
            side_effect=RuntimeError("no library"),
        ):
            result = preflight(Config(), ["DEMO4242"], catalog=catalog)

        self.assertEqual(len(result.statuses), 1)
        self.assertEqual(result.statuses[0].state, LOCAL_READY)

    def test_unknown_reference_is_absent_not_an_exception(self) -> None:
        catalog = FakeCatalog([FakeRecord(4242, "DEMO4242")])
        with mock.patch(
            "zotero.library_index.build_library_index",
            side_effect=RuntimeError("no library"),
        ):
            result = preflight(Config(), ["2501.99999"], catalog=catalog)

        self.assertEqual(result.statuses[0].state, ABSENT)

    def test_empty_index_is_still_constructible(self) -> None:
        """The fallback builds a real empty index, not a sentinel object."""
        self.assertEqual(LibraryIndex([]).entries, [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for data-driven Inbox triage suggestions."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.commands import cmd_prepare_triage  # noqa: E402


class FakeAPI:
    def __init__(self):
        self.requested_collection = None

    def get_items(self, *, limit, collection, sort, direction):
        self.requested_collection = collection
        return [{
            "data": {
                "key": "ABCD1234",
                "title": "BEHAVE: Human Object Interaction Dataset",
                "abstractNote": "A manipulation dataset.",
                "DOI": "10.1109/CVPR.9879007",
                "libraryCatalog": "DOI.org (Crossref)",
                "url": "https://ieeexplore.ieee.org/document/9879007/",
                "tags": [],
            }
        }]

    def get_collections(self):
        return [
            {"data": {"key": "INBOX123", "name": "90_Inbox"}},
            {"data": {"key": "MNP12345", "name": "Manipulation"}},
        ]


class PrepareTriageTest(unittest.TestCase):
    def test_uses_configured_inbox_and_resolves_collection_key(self) -> None:
        api = FakeAPI()
        config = SimpleNamespace(
            api_url="http://localhost:23119",
            user_id="123",
            inbox_collection_key="INBOX123",
        )
        args = SimpleNamespace(collection=None, limit=1, json=True)
        output = io.StringIO()

        with patch.object(cmd_prepare_triage, "ZoteroAPI", return_value=api):
            with contextlib.redirect_stdout(output):
                self.assertEqual(cmd_prepare_triage._Run(args, config), 0)

        self.assertEqual(api.requested_collection, "INBOX123")
        row = json.loads(output.getvalue())[0]
        self.assertEqual(row["suggested_folder"], "Manipulation")
        self.assertEqual(row["suggested_folder_key"], "MNP12345")
        self.assertNotIn("source/arxiv", row["suggested_tags"])
        self.assertIn("type/dataset", row["suggested_tags"])

    def test_arxiv_source_is_metadata_driven(self) -> None:
        tags, _folder = cmd_prepare_triage._SuggestTags(
            "A paper", "", {"DOI": "10.48550/arXiv.1234"}
        )
        self.assertIn("source/arxiv", tags)


if __name__ == "__main__":
    unittest.main()

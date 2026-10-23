#!/usr/bin/env python3
"""Unit tests for the Zotero Local API client."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.api import ZoteroAPI, ZoteroAPIError, normalize_key  # noqa: E402


class FakeResponse:
    def __init__(self, payload: object):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


class ZoteroAPITest(unittest.TestCase):
    def test_normalize_key_accepts_case_and_rejects_bad_values(self) -> None:
        self.assertEqual(normalize_key(" abcd1234 ", "item key"), "ABCD1234")
        with self.assertRaises(ZoteroAPIError):
            normalize_key("too-short", "item key")

    def test_get_items_normalizes_collection_key(self) -> None:
        api = ZoteroAPI("http://localhost:23119/", "123")
        with patch("zotero.api.urlopen", return_value=FakeResponse([])) as open_url:
            self.assertEqual(api.get_items(limit=5, collection="abcd1234"), [])

        request = open_url.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(urlparse(request.full_url).path, "/api/users/123/collections/ABCD1234/items/top")
        self.assertEqual(query["limit"], ["5"])

    def test_get_all_paginates_until_short_page(self) -> None:
        api = ZoteroAPI("http://localhost:23119", "123")
        responses = [FakeResponse([{"id": i} for i in range(100)]), FakeResponse([{"id": 100}])]

        with patch("zotero.api.urlopen", side_effect=responses) as open_url:
            rows = api.get_all("/items/top")

        self.assertEqual(len(rows), 101)
        self.assertEqual([call.args[0].full_url for call in open_url.call_args_list], [
            "http://localhost:23119/api/users/123/items/top?limit=100&start=0&format=json",
            "http://localhost:23119/api/users/123/items/top?limit=100&start=100&format=json",
        ])

    def test_get_all_respects_max_items(self) -> None:
        api = ZoteroAPI("http://localhost:23119", "123")
        with patch("zotero.api.urlopen", return_value=FakeResponse([{"id": i} for i in range(100)])) as open_url:
            rows = api.get_all("/items/top", max_items=3)

        self.assertEqual([row["id"] for row in rows], [0, 1, 2])
        query = parse_qs(urlparse(open_url.call_args.args[0].full_url).query)
        self.assertEqual(query["limit"], ["3"])

    def test_get_items_paginates_large_limits(self) -> None:
        api = ZoteroAPI("http://localhost:23119", "123")
        responses = [FakeResponse([{"id": i} for i in range(100)]), FakeResponse([{"id": 100}])]
        with patch("zotero.api.urlopen", side_effect=responses) as open_url:
            rows = api.get_items(limit=101)

        self.assertEqual(len(rows), 101)
        self.assertEqual(len(open_url.call_args_list), 2)

    def test_search_rejects_empty_query(self) -> None:
        api = ZoteroAPI("http://localhost:23119", "123")
        with self.assertRaisesRegex(ZoteroAPIError, "must not be empty"):
            api.search("  ")

    def test_http_error_includes_bounded_response_body(self) -> None:
        api = ZoteroAPI("http://localhost:23119", "123")
        error = HTTPError(
            "http://localhost:23119/api/users/123/items",
            503,
            "unavailable",
            {},
            io.BytesIO(b"service unavailable"),
        )
        with patch("zotero.api.urlopen", side_effect=error):
            with self.assertRaisesRegex(ZoteroAPIError, r"HTTP 503.*service unavailable"):
                api.get_items()

    def test_invalid_json_is_reported_as_api_error(self) -> None:
        class InvalidResponse(FakeResponse):
            def read(self) -> bytes:
                return b"not json"

        api = ZoteroAPI("http://localhost:23119", "123")
        with patch("zotero.api.urlopen", return_value=InvalidResponse(None)):
            with self.assertRaisesRegex(ZoteroAPIError, "invalid JSON"):
                api.get_items()


if __name__ == "__main__":
    unittest.main()

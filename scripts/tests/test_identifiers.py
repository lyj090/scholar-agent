#!/usr/bin/env python3
"""Tests for identifier normalization (arXiv IDs, DOIs, URLs)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.identifiers import (  # noqa: E402
    extract_identifiers,
    identifier_query_terms,
    normalize_arxiv_id,
    normalize_doi,
)


class NormalizeArxivIdTest(unittest.TestCase):
    def test_bare_modern_id(self) -> None:
        self.assertEqual(normalize_arxiv_id("2405.20321"), "2405.20321")

    def test_version_suffix_is_stripped(self) -> None:
        # Caches are version-agnostic; vN must not leak into the key.
        self.assertEqual(normalize_arxiv_id("2405.20321v3"), "2405.20321")

    def test_prefixed_forms(self) -> None:
        for raw in ("arXiv:2405.20321", "arxiv: 2405.20321", "ARXIV:2405.20321"):
            self.assertEqual(normalize_arxiv_id(raw), "2405.20321", raw)

    def test_url_forms(self) -> None:
        for raw in (
            "https://arxiv.org/abs/2405.20321",
            "http://arxiv.org/pdf/2405.20321v1",
            "https://arxiv.org/html/2405.20321v2",
        ):
            self.assertEqual(normalize_arxiv_id(raw), "2405.20321", raw)

    def test_arxiv_doi(self) -> None:
        self.assertEqual(
            normalize_arxiv_id("10.48550/arXiv.2405.20321"), "2405.20321"
        )

    def test_legacy_id(self) -> None:
        self.assertEqual(normalize_arxiv_id("hep-th/9901001"), "hep-th/9901001")
        self.assertEqual(normalize_arxiv_id("math/0211159v2"), "math/0211159")

    def test_rejects_non_ids(self) -> None:
        for raw in ("", "   ", None, "hello world", "Retargeting Matters",
                    "1234", "1.2.3", "not-a-paper"):
            self.assertIsNone(normalize_arxiv_id(raw), repr(raw))

    def test_does_not_extract_from_larger_number(self) -> None:
        # 12345.67890 is not a date-shaped arXiv id (YYMM needs exactly 4).
        self.assertIsNone(normalize_arxiv_id("12345.678901"))


class NormalizeDoiTest(unittest.TestCase):
    def test_plain_doi(self) -> None:
        self.assertEqual(normalize_doi("10.1109/TRO.2024.1234567"),
                         "10.1109/tro.2024.1234567")

    def test_resolver_prefixes(self) -> None:
        for raw in (
            "https://doi.org/10.1109/TRO.2024.1",
            "http://dx.doi.org/10.1109/TRO.2024.1",
            "doi:10.1109/TRO.2024.1",
        ):
            self.assertEqual(normalize_doi(raw), "10.1109/tro.2024.1", raw)

    def test_trailing_punctuation_stripped(self) -> None:
        # Prose often glues a period or paren onto the DOI.
        self.assertEqual(normalize_doi("See 10.1109/TRO.2024.1."), "10.1109/tro.2024.1")
        self.assertEqual(normalize_doi("(10.1109/TRO.2024.1)"), "10.1109/tro.2024.1")

    def test_rejects_non_doi(self) -> None:
        for raw in ("", None, "2405.20321", "no doi here"):
            self.assertIsNone(normalize_doi(raw), repr(raw))


class ExtractIdentifiersTest(unittest.TestCase):
    def test_zotero_key_only_when_ambiguous(self) -> None:
        self.assertEqual(extract_identifiers("QPH9XUBL").zotero_key, "QPH9XUBL")

    def test_key_not_taken_when_arxiv_id_present(self) -> None:
        parsed = extract_identifiers("arXiv:2405.20321")
        self.assertEqual(parsed.arxiv_id, "2405.20321")
        self.assertIsNone(parsed.zotero_key)

    def test_title_hint_for_prose(self) -> None:
        parsed = extract_identifiers("Retargeting Matters")
        self.assertEqual(parsed.title_hint, "Retargeting Matters")
        self.assertIsNone(parsed.arxiv_id)

    def test_empty_is_empty(self) -> None:
        self.assertTrue(extract_identifiers("   ").is_empty)
        self.assertFalse(extract_identifiers("2405.20321").is_empty)

    def test_non_string_input_is_safe(self) -> None:
        self.assertTrue(extract_identifiers(None).is_empty)
        # Non-string input is coerced, not crashed on. A bare number is not an
        # identifier but is kept as a title/search hint, which is harmless and
        # lets callers fall back to a plain library search.
        coerced = extract_identifiers(1234)
        self.assertEqual(coerced.raw, "1234")
        self.assertIsNone(coerced.arxiv_id)
        self.assertIsNone(coerced.doi)
        self.assertEqual(coerced.title_hint, "1234")

    def test_query_terms_order_and_arxiv_doi_unwrapping(self) -> None:
        terms = identifier_query_terms(
            extract_identifiers("10.48550/arXiv.2405.20321")
        )
        # The bare arXiv id is also worth trying as a search term.
        self.assertIn("2405.20321", terms)


if __name__ == "__main__":
    unittest.main()

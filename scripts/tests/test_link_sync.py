#!/usr/bin/env python3
"""Regression tests for Obsidian <-> Zotero link injection.

These cover four production defects that were all silent — each one produced a
plausible-looking note while corrupting or refusing to fix real data:

1. ``ensure_frontmatter_zotero_keys`` was not idempotent. It carried the blank
   line after the opening fence as a list element and re-appended it, so every
   ``sync-links`` run pushed one more blank line into the frontmatter of all
   106 notes.
2. The same function **deleted** an existing ``zotero_pdf_key`` whenever the
   caller could not resolve the attachment (e.g. a transient API failure), so a
   readable key could vanish without any error.
3. The frontmatter delimiter was located with ``content.find("---", 3)``, which
   matches the dash run inside a quoted title such as ``title: "A --- B"`` and
   cuts the block mid-value.
4. :func:`inject_quick_links_block` required a trailing newline after every
   bullet, so a ``## 快捷链接`` block at end-of-file left its last line behind —
   duplicating links instead of replacing them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero.frontmatter_tags import (  # noqa: E402
    extract_frontmatter,
    ensure_literature_status_tag,
    frontmatter_bounds,
    split_frontmatter,
)
from zotero.link_sync import (  # noqa: E402
    ensure_frontmatter_zotero_keys,
    extract_title_from_content,
    extract_zotero_key_from_content,
    inject_quick_links_block,
)

PLAIN = '---\ntitle: "X"\nzotero_key: AAAABBBB\n---\n\n# Body\n'
PADDED = '---\n\n\ntitle: "X"\nzotero_key: AAAABBBB\n---\n\n# Body\n'
DASHED = '---\ntitle: "A --- B"\nzotero_key: AAAABBBB\n---\n\n# Body\n'


class FrontmatterSplitTest(unittest.TestCase):
    def test_round_trips_losslessly(self) -> None:
        for content in (PLAIN, PADDED, DASHED, '---\ntitle: "X"\n---'):
            with self.subTest(content=content[:24]):
                parsed = split_frontmatter(content)
                self.assertIsNotNone(parsed)
                inner, tail = parsed
                self.assertEqual("---" + inner + tail, content)

    def test_rejects_content_without_a_block(self) -> None:
        for content in ("# Heading only\n", "---\ntitle: X\n", "", "no fence\n"):
            with self.subTest(content=content):
                self.assertIsNone(split_frontmatter(content))

    def test_dashes_inside_title_are_not_a_delimiter(self) -> None:
        """The closing fence is line-anchored, so an inline ``---`` cannot cut it."""
        bounds = frontmatter_bounds(DASHED)
        self.assertIsNotNone(bounds)
        inner_start, close_start = bounds
        inner = DASHED[inner_start:close_start]
        self.assertIn('title: "A --- B"', inner)

    def test_extract_frontmatter_reads_dashed_title(self) -> None:
        parsed = extract_frontmatter(DASHED)
        self.assertEqual(parsed.get("title"), "A --- B")
        self.assertEqual(parsed.get("zotero_key"), "AAAABBBB")


class EnsureZoteroKeysTest(unittest.TestCase):
    def test_is_idempotent_for_every_padding_shape(self) -> None:
        """Regression: each call used to add exactly one blank line."""
        for content in (PLAIN, PADDED, DASHED):
            with self.subTest(content=content[:24]):
                once = ensure_frontmatter_zotero_keys(content, "AAAABBBB", "CCCCDDDD")
                twice = ensure_frontmatter_zotero_keys(once, "AAAABBBB", "CCCCDDDD")
                self.assertEqual(once, twice)

    def test_does_not_accumulate_leading_blank_lines(self) -> None:
        out = ensure_frontmatter_zotero_keys(PADDED, "AAAABBBB", "CCCCDDDD")
        self.assertTrue(out.startswith('---\n'))
        self.assertFalse(out.startswith("---\n\n"))

    def test_missing_pdf_key_is_preserved_when_unresolved(self) -> None:
        """Regression: ``pdf_key=None`` used to delete a valid existing key."""
        content = (
            '---\ntitle: "X"\nzotero_key: AAAABBBB\n'
            "zotero_pdf_key: KEEPME12\n---\n\n# Body\n"
        )
        out = ensure_frontmatter_zotero_keys(content, "AAAABBBB", None)
        self.assertIn("zotero_pdf_key: KEEPME12", out)

    def test_resolved_pdf_key_replaces_the_old_one(self) -> None:
        content = (
            '---\ntitle: "X"\nzotero_key: AAAABBBB\n'
            "zotero_pdf_key: OLDOLD12\n---\n\n# Body\n"
        )
        out = ensure_frontmatter_zotero_keys(content, "AAAABBBB", "NEWNEW12")
        self.assertIn("zotero_pdf_key: NEWNEW12", out)
        self.assertNotIn("OLDOLD12", out)

    def test_legacy_numeric_item_id_is_dropped(self) -> None:
        content = '---\ntitle: "X"\nzotero_item_id: 12345\n---\n\n# Body\n'
        out = ensure_frontmatter_zotero_keys(content, "AAAABBBB", "CCCCDDDD")
        self.assertNotIn("zotero_item_id", out)
        self.assertIn("zotero_key: AAAABBBB", out)

    def test_body_is_untouched(self) -> None:
        out = ensure_frontmatter_zotero_keys(PLAIN, "AAAABBBB", "CCCCDDDD")
        self.assertIn("\n# Body\n", out)
        self.assertTrue(out.endswith("# Body\n"))

    def test_title_survives_the_rewrite(self) -> None:
        out = ensure_frontmatter_zotero_keys(DASHED, "AAAABBBB", "CCCCDDDD")
        self.assertIn('title: "A --- B"', out)
        self.assertEqual(extract_title_from_content(out), "A --- B")
        self.assertEqual(extract_zotero_key_from_content(out), "AAAABBBB")


class InjectQuickLinksTest(unittest.TestCase):
    BLOCK = "## 快捷链接\n- [在 Zotero 中打开](zotero://select/library/items/AAAABBBB)"

    def test_is_idempotent(self) -> None:
        once = inject_quick_links_block(PLAIN, self.BLOCK)
        twice = inject_quick_links_block(once, self.BLOCK)
        self.assertEqual(once, twice)

    def test_replaces_a_block_that_ends_the_file(self) -> None:
        """Regression: a block at EOF left its last bullet behind, so the
        rewrite appended new links instead of replacing the old ones."""
        content = (
            '---\ntitle: "X"\nzotero_key: AAAABBBB\n---\n\n'
            "## 快捷链接\n- [a](zotero://x)\n- [b](zotero://y)"
        )
        out = inject_quick_links_block(content, self.BLOCK)
        self.assertNotIn("zotero://x", out)
        self.assertNotIn("zotero://y", out)
        self.assertIn("zotero://select", out)

    def test_replaces_a_mid_document_block_without_eating_the_body(self) -> None:
        content = (
            '---\ntitle: "X"\nzotero_key: AAAABBBB\n---\n\n'
            "## 快捷链接\n- old\n\n# Intro\n\nReal body text.\n"
        )
        out = inject_quick_links_block(content, self.BLOCK)
        self.assertNotIn("- old", out)
        self.assertIn("# Intro", out)
        self.assertIn("Real body text.", out)

    def test_inserts_when_no_block_exists(self) -> None:
        out = inject_quick_links_block(PLAIN, self.BLOCK)
        self.assertIn("## 快捷链接", out)
        self.assertIn("# Body", out)
        self.assertEqual(inject_quick_links_block(out, self.BLOCK), out)

    def test_replaces_a_block_with_a_blank_line_after_the_header(self) -> None:
        """Regression: a hand-edited note had ``## 快捷链接`` followed by a
        blank line before the bullets. The matcher did not consume that block,
        so injection appended a second copy of the links instead of replacing
        the first."""
        content = (
            '---\ntitle: "X"\nzotero_key: AAAABBBB\n---\n\n'
            "## 快捷链接\n\n- [old](zotero://old)\n\n# Body\n"
        )
        out = inject_quick_links_block(content, self.BLOCK)
        self.assertNotIn("zotero://old", out)
        self.assertEqual(out.count("## 快捷链接"), 1)
        self.assertEqual(out.count("zotero://select"), 1)

    def test_content_without_frontmatter_gets_a_block(self) -> None:
        out = inject_quick_links_block("# Loose note\n", self.BLOCK)
        self.assertTrue(out.startswith("## 快捷链接"))
        self.assertIn("# Loose note", out)


class EnsureStatusTagTest(unittest.TestCase):
    def test_adds_status_when_absent(self) -> None:
        content = '---\ntitle: "X"\ntags:\n  - type/paper\n---\n\n# Body\n'
        out = ensure_literature_status_tag(content)
        self.assertIn("status/to-read", out)
        self.assertEqual(extract_frontmatter(out).get("title"), "X")

    def test_is_idempotent(self) -> None:
        content = '---\ntitle: "X"\ntags:\n  - type/paper\n---\n\n# Body\n'
        once = ensure_literature_status_tag(content)
        self.assertEqual(once, ensure_literature_status_tag(once))

    def test_keeps_an_existing_status(self) -> None:
        content = (
            '---\ntitle: "X"\ntags:\n  - type/paper\n  - status/deep-read\n---\n\n# Body\n'
        )
        self.assertEqual(ensure_literature_status_tag(content), content)

    def test_preserves_dashed_title(self) -> None:
        content = '---\ntitle: "A --- B"\ntags:\n  - type/paper\n---\n\n# Body\n'
        out = ensure_literature_status_tag(content)
        self.assertIn('title: "A --- B"', out)
        self.assertIn("status/to-read", out)


class PdfKeyConsistencyTest(unittest.TestCase):
    """Frontmatter and the quick-links block must never disagree about the PDF.

    A trashed parent (post-merge) makes ``get_children`` fail, so the resolver
    returns None. Preserving the frontmatter key while dropping the PDF link
    would leave a note that contradicts itself.
    """

    def test_existing_pdf_key_is_reused_when_resolution_fails(self) -> None:
        from unittest.mock import MagicMock, patch

        from zotero.link_sync import inject_quick_links

        content = (
            '---\ntitle: "X"\nzotero_key: AAAABBBB\n'
            "zotero_pdf_key: KEEPME12\n---\n\n# Body\n"
        )
        api = MagicMock()
        api.get_children.side_effect = RuntimeError("parent is trashed")
        out = inject_quick_links(content, "AAAABBBB", api)
        self.assertIn("zotero_pdf_key: KEEPME12", out)
        self.assertIn("zotero://open-pdf/library/items/KEEPME12", out)

    def test_resolved_key_overrides_the_stale_one_everywhere(self) -> None:
        from unittest.mock import MagicMock

        from zotero.link_sync import inject_quick_links

        content = (
            '---\ntitle: "X"\nzotero_key: AAAABBBB\n'
            "zotero_pdf_key: OLDOLD12\n---\n\n# Body\n"
        )
        api = MagicMock()
        api.get_children.return_value = [
            {"key": "NEWNEW12", "data": {"contentType": "application/pdf"}}
        ]
        out = inject_quick_links(content, "AAAABBBB", api)
        self.assertIn("zotero_pdf_key: NEWNEW12", out)
        self.assertIn("zotero://open-pdf/library/items/NEWNEW12", out)
        self.assertNotIn("OLDOLD12", out)

    def test_extract_pdf_key_reads_frontmatter(self) -> None:
        from zotero.link_sync import extract_pdf_key_from_content

        content = (
            '---\ntitle: "X"\nzotero_key: AAAABBBB\n'
            "zotero_pdf_key: KEEPME12\n---\n"
        )
        self.assertEqual(extract_pdf_key_from_content(content), "KEEPME12")
        self.assertIsNone(extract_pdf_key_from_content('---\ntitle: "X"\n---\n'))
        self.assertIsNone(extract_pdf_key_from_content("# no frontmatter\n"))

    def test_full_injection_is_idempotent(self) -> None:
        from unittest.mock import MagicMock

        from zotero.link_sync import inject_quick_links

        content = '---\ntitle: "X"\nzotero_key: AAAABBBB\n---\n\n# Body\n'
        api = MagicMock()
        api.get_children.return_value = [
            {"key": "PDFKEY12", "data": {"contentType": "application/pdf"}}
        ]
        once = inject_quick_links(content, "AAAABBBB", api)
        twice = inject_quick_links(once, "AAAABBBB", api)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()

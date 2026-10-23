#!/usr/bin/env python3
"""Tests for the shared two-voice structural rules (`note_structure`).

Why this module is tested directly: `verify-note` and `audit-notes` used to
each re-implement the closing-section check with *different* regexes
(`^## 关联` vs `^#{2,3} 关联`). After the 2026-10 restructure moved `关联`
inside the private section, one reported 121/121 failures while the other
passed everything. These tests pin the shared behaviour so the two commands
cannot silently disagree again, and pin the published-region rule that catches
project references leaking into a paper's numbered sections.
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero.note_structure import (  # noqa: E402
    find_dangling_references,
    find_private_wikilinks,
    find_public_region_violations,
    has_personal_section,
    has_related_section,
    public_region,
)

CLEAN = """---
title: "A Paper"
---

## 1. Introduction

The authors propose a method and report its limits.

## 2. 项目关联与本笔记判读

> 本节为本地私有内容，不发布到 paper_garden。

### 我的判断

This is mine.

### 关联

- [[Another Paper Note]]
"""

# Same note, but the public number section carries a project reference.
LEAKING_BODY = "该论文与当前项目的 synthetic-private-token 相关。"


class PersonalSectionTest(unittest.TestCase):
    def test_numbered_heading_is_detected(self):
        self.assertTrue(has_personal_section(CLEAN))

    def test_bare_heading_is_detected(self):
        self.assertTrue(
            has_personal_section("## 项目关联与本笔记判读\n\n### 关联\n\n- [[X]]\n")
        )

    def test_absent_heading_is_not_detected(self):
        self.assertFalse(has_personal_section("## 1. Intro\n\ntext\n"))

    def test_related_inside_private_section(self):
        self.assertTrue(has_related_section(CLEAN))

    def test_legacy_top_level_related_still_accepted(self):
        # not-yet-migrated notes must not fail the audit
        self.assertTrue(has_related_section("## 关联\n\n- [[X]]\n"))


class PublicRegionTest(unittest.TestCase):
    def setUp(self):
        mock = patch("zotero.note_structure.private_terms", return_value=("synthetic-private-token",))
        mock.start()
        self.addCleanup(mock.stop)

    def test_clean_note_has_no_violations(self):
        self.assertEqual(find_public_region_violations(CLEAN), [])

    def test_project_reference_in_numbered_section_is_flagged(self):
        bad = CLEAN.replace(
            "The authors propose a method and report its limits.", LEAKING_BODY
        )
        terms = {t for _, t, _ in find_public_region_violations(bad)}
        # one term is reported per line (first match wins), so assert the line
        # was caught rather than every term on it
        self.assertTrue(terms & {"当前项目", "synthetic-private-token"})
        self.assertEqual(len(find_public_region_violations(bad)), 1)

    def test_project_reference_inside_private_section_is_allowed(self):
        # the private section is never published, so it may say anything
        ok = CLEAN.replace("This is mine.", LEAKING_BODY)
        self.assertEqual(find_public_region_violations(ok), [])

    def test_pipeline_vocabulary_is_flagged(self):
        # regression: a whole `synthetic-private-token / private-field` comparison table was published
        bad = CLEAN.replace(
            "The authors propose a method and report its limits.",
            "| 组件 | synthetic-private-token | private-field |\n|---|---|---|\n| a | b | c |",
        )
        terms = {t for _, t, _ in find_public_region_violations(bad)}
        self.assertTrue(terms & {"synthetic-private-token", "private-field"})

    def test_inline_exemption_is_honoured(self):
        ok = CLEAN.replace(
            "The authors propose a method and report its limits.",
            "本文使用的 EVK4 是论文自身的硬件。 <!-- check:isolation-ok -->",
        )
        # EVK4 is not in the shared list, but the marker path must not raise
        self.assertEqual(find_public_region_violations(ok), [])

    def test_public_region_excludes_trailing_section(self):
        self.assertNotIn("我的判断", public_region(CLEAN))


class PrivateMarkerTest(unittest.TestCase):
    """The explicit divider hides everything below it, whatever the headings."""

    def test_marker_cuts_to_eof_including_paper_like_heading(self):
        body = "## 1. Intro\nEvidence\n\n<!-- private -->\n\n## 10. 证据边界\nlocal only\n"
        self.assertEqual(public_region(body), "## 1. Intro\nEvidence\n\n")

    def test_marker_tail_is_not_a_personal_section_error(self):
        body = "## 1. Intro\nEvidence\n\n<!-- private -->\n\n## 10. 证据边界\nlocal\n"
        self.assertFalse(has_personal_section(body))

    def test_marker_angle_variant(self):
        self.assertEqual(public_region("A\n\n<<< private >>>\nB\n"), "A\n\n")

    def test_marker_inside_fence_does_not_cut(self):
        body = "```md\n<!-- private -->\n```\n## Results\nEvidence\n"
        self.assertIn("Evidence", public_region(body))


class DanglingReferenceTest(unittest.TestCase):
    """A public `§N` pointer must not point at a section below the divider.

    Regression: the PRISM evidence-boundary section moved below the private
    divider, but four public-region "见 §10" pointers were left behind. On the
    published page they referenced a section that is cut before publication.
    """

    def test_reference_into_hidden_tail_is_flagged(self):
        body = (
            "## 1. Intro\nsee the caveats in §10 for details\n\n"
            "<!-- private -->\n\n## 10. 证据边界\nlocal only\n"
        )
        self.assertEqual(find_dangling_references(body), [(2, "10")])

    def test_reference_to_a_public_section_is_allowed(self):
        body = "## 1. Intro\nsee §2\n\n## 2. Method\ntext\n"
        self.assertEqual(find_dangling_references(body), [])

    def test_paper_section_reference_survives_a_hidden_private_section(self):
        # §4.2 is the paper's own section; the private tail happens to number a
        # heading 10, so only the 10 reference is dangling.
        body = "## 4. 方法\n见 §4.2\n\n<!-- private -->\n\n## 10. 备注\nx\n"
        self.assertEqual(find_dangling_references(body), [])

    def test_no_divider_means_nothing_is_hidden(self):
        body = "## 1. Intro\nsee §9\n\n## 9. Notes\nx\n"
        self.assertEqual(find_dangling_references(body), [])


class PrivateWikilinkTest(unittest.TestCase):
    def test_link_into_private_partition_is_flagged(self):
        # a private link in the PUBLIC region (the private section may hold any)
        bad = CLEAN.replace(
            "The authors propose a method and report its limits.",
            "- [[01-Projects/Some-Project/Knowledge/Method/内部方法文档]]",
        )
        hits = find_private_wikilinks(bad)
        self.assertEqual(len(hits), 1)
        self.assertIn("01-Projects", hits[0][1])

    def test_paper_to_paper_link_is_allowed(self):
        self.assertEqual(find_private_wikilinks(CLEAN), [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Unit tests for reusable paper tag governance."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.tag_governance import (  # noqa: E402
    canonicalize_tags,
    is_connector_noise,
    replace_note_tags,
)


class TagGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        tags = [
            "type/paper",
            "type/survey",
            "area/robotics",
            "area/computer-vision",
            "area/control",
            "topic/event-vision",
            "topic/perception",
            "topic/manipulation",
            "topic/slam",
            "method/reinforcement-learning",
            "method/imitation-learning",
            "method/amp",
            "task/feature-tracking",
            "source/arxiv",
            "rel/thesis-core",
            "rel/thesis-related",
            "status/to-read",
            "status/deep-read",
        ]
        self.taxonomy = {
            "allowed_tags": set(tags),
            "limits": {
                "type": 1,
                "area": 2,
                "topic": 3,
                "method": 3,
                "task": 2,
                "source": 1,
                "rel": 1,
                "status": 1,
            },
        }

    def test_connector_noise_is_dropped_not_blocking(self) -> None:
        """Regression: arXiv categories used to be reported as `unknown`, and
        `apply` refuses to run while any `unknown` exists. Every arXiv-imported
        item therefore made the whole governance chain permanently unusable."""
        item = {"title": "Event-based Vision Survey", "DOI": "10.48550/arXiv.1234"}
        tags, unknown, dropped = canonicalize_tags(
            item,
            {
                "topic/event-camera",
                "source/zotero",
                "Computer Science - Robotics",
                "Machine Learning (cs.LG)",
                "FOS: Computer and information sciences",
                "/unread",
            },
            set(),
            self.taxonomy,
        )
        self.assertIn("topic/event-vision", tags)
        self.assertIn("type/survey", tags)
        self.assertIn("area/robotics", tags)
        self.assertIn("status/to-read", tags)
        # Genuine unmapped intents still block apply.
        self.assertEqual(unknown, ["source/zotero", "topic/event-camera"])
        # Machine noise never reaches either side and never blocks.
        self.assertEqual(
            dropped,
            [
                "/unread",
                "Computer Science - Robotics",
                "FOS: Computer and information sciences",
                "Machine Learning (cs.LG)",
            ],
        )
        for noise in dropped:
            self.assertNotIn(noise, tags)

    def test_a_bare_tag_is_unknown_not_noise(self) -> None:
        """The noise classifier must stay narrow: a sloppy human tag is a
        decision to surface, not something to silently delete."""
        self.assertFalse(is_connector_noise("robotics"))
        self.assertFalse(is_connector_noise("强化学习"))
        self.assertFalse(is_connector_noise("some-random"))
        # Contains a category name but is not one: a human tag, not noise.
        self.assertFalse(is_connector_noise("my Computer Science - Robotics notes"))
        self.assertTrue(is_connector_noise("/unread"))
        self.assertTrue(is_connector_noise("Computer Science - Robotics"))
        self.assertTrue(
            is_connector_noise(
                "Electrical Engineering and Systems Science - Signal Processing"
            )
        )
        self.assertTrue(is_connector_noise("Robotics (cs.RO)"))
        self.assertTrue(is_connector_noise("FOS: Computer and information sciences"))

    def test_note_status_wins_over_zotero_status(self) -> None:
        item = {"title": "PPO", "DOI": "10.48550/arXiv.1707.06347"}
        tags, _unknown, _dropped = canonicalize_tags(
            item,
            {"status/to-read"},
            {"status/deep-read"},
            self.taxonomy,
        )
        self.assertIn("status/deep-read", tags)
        self.assertNotIn("status/to-read", tags)

    def test_frontmatter_rewrite_preserves_body(self) -> None:
        content = "---\ntitle: Test\ntags:\n  - type/paper\n---\n\n## Body\nKeep this.\n"
        updated = replace_note_tags(content, ["type/paper", "area/robotics", "status/to-read"])
        self.assertIn("## Body\nKeep this.", updated)
        self.assertIn("  - area/robotics", updated)
        self.assertNotIn("  - type/paper\n---\n\n## Body", updated)

    def test_frontmatter_rewrite_survives_dashes_in_title(self) -> None:
        """Regression: ``content.find("---", 3)`` cut the block at the dash run
        inside a quoted title, corrupting the rewritten frontmatter."""
        content = (
            '---\ntitle: "A --- B"\ntags:\n  - type/paper\n---\n\n## Body\n'
        )
        updated = replace_note_tags(content, ["type/paper", "status/to-read"])
        self.assertIn('title: "A --- B"', updated)
        self.assertIn("  - status/to-read", updated)
        self.assertIn("## Body", updated)

    def test_frontmatter_rewrite_is_idempotent(self) -> None:
        content = '---\ntitle: "X"\ntags:\n  - type/paper\n---\n\n# Body\n'
        target = ["type/paper", "area/robotics", "status/to-read"]
        once = replace_note_tags(content, target)
        twice = replace_note_tags(once, target)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()

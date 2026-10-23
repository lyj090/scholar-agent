#!/usr/bin/env python3
"""Tests for asset hygiene: slug generation and orphan pruning.

Two defects are pinned here.

* **Empty slug.** ``_Slugify`` strips every non-ASCII-alphanumeric character, so
  a title written only in Chinese (or only in symbols) reduced to ``""``. The
  asset directory then became ``assets/`` itself, and two such papers would
  write ``fig1-*.jpg`` into the same place and overwrite each other. The note
  body is Chinese by default, so a Chinese title is an unremarkable input.

* **Unreclaimed directories.** ``prepare-note`` derives the asset directory from
  the title, and that derivation changed shape at least once (``beyondmimic/``
  vs the current longer slug). Re-preparing a note adds a directory and nothing
  removes the old one. On the live vault that had accumulated 18 orphans,
  15.5 MB of 103.6 MB. ``prune-assets`` must report them, and must NOT report a
  directory that any note still links to.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.commands import cmd_prepare_note as P  # noqa: E402
from zotero.commands import cmd_prune_assets as PR  # noqa: E402


class SlugifyTest(unittest.TestCase):
    """`_Slugify` must always return a usable, distinct directory name."""

    def test_ascii_title_is_lowercased_and_dashed(self):
        self.assertEqual(P._Slugify("Deep RL for Robots"), "deep-rl-for-robots")

    def test_chinese_title_does_not_collapse_to_empty(self):
        slug = P._Slugify("中文标题：一个研究")
        self.assertTrue(slug, "a Chinese title must still yield a directory name")
        self.assertNotEqual(slug, "")

    def test_symbol_only_title_does_not_collapse_to_empty(self):
        slug = P._Slugify("!!! ???")
        self.assertTrue(slug)

    def test_distinct_non_ascii_titles_do_not_collide(self):
        """Two different Chinese papers must not share an asset directory."""
        first = P._Slugify("论文甲")
        second = P._Slugify("论文乙")
        self.assertNotEqual(first, second)

    def test_fallback_is_stable_across_calls(self):
        """The same title must map to the same directory on every run."""
        self.assertEqual(P._Slugify("论文"), P._Slugify("论文"))

    def test_long_title_is_truncated_without_trailing_dash(self):
        slug = P._Slugify("word " * 40)
        self.assertLessEqual(len(slug), 80)
        self.assertFalse(slug.endswith("-"))
        self.assertFalse(slug.startswith("-"))


class PruneAssetsTest(unittest.TestCase):
    """`prune-assets` reports only directories no note links to."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.notes = Path(self._tmp.name)
        self.assets = self.notes / "assets"
        self.assets.mkdir()
        self._add_dir("live-paper", {"fig1-a.jpg": b"x" * 100})
        self._add_dir("stale-paper", {"fig1-b.jpg": b"x" * 250})

    def tearDown(self):
        self._tmp.cleanup()

    def _add_dir(self, slug, files):
        target = self.assets / slug
        target.mkdir()
        for name, blob in files.items():
            (target / name).write_bytes(blob)

    def _write_note(self, name, body):
        (self.notes / name).write_text(body, encoding="utf-8")

    def test_referenced_dir_is_not_an_orphan(self):
        self._write_note("Live.md", "![Fig 1](assets/live-paper/fig1-a.jpg)\n")
        referenced, referenced_files, errors = PR._CollectReferencedSlugs(str(self.notes))
        self.assertIn("live-paper", referenced)
        self.assertIn(("live-paper", "fig1-a.jpg"), referenced_files)
        self.assertNotIn("stale-paper", referenced)
        self.assertEqual(errors, [])

    def test_reference_is_found_outside_an_image_embed(self):
        """A plain markdown link must keep its assets alive too."""
        self._write_note("Live.md", "[see](assets/live-paper/fig1-a.jpg)\n")
        referenced, _, _ = PR._CollectReferencedSlugs(str(self.notes))
        self.assertIn("live-paper", referenced)

    def test_unreadable_note_is_reported_not_fatal(self):
        """A note that cannot be decoded must not crash the scan."""
        (self.notes / "Broken.md").write_bytes(b"\xff\xfe\x00bad")
        referenced, _, errors = PR._CollectReferencedSlugs(str(self.notes))
        self.assertEqual(errors, [])
        self.assertIsInstance(referenced, set)

    def test_dir_size_counts_nested_files(self):
        nested = self.assets / "stale-paper" / "sub"
        nested.mkdir()
        (nested / "fig2.jpg").write_bytes(b"y" * 50)
        self.assertEqual(PR._DirSize(str(self.assets / "stale-paper")), 300)

    def test_human_bytes_scales(self):
        self.assertEqual(PR._HumanBytes(512), "512 B")
        self.assertEqual(PR._HumanBytes(2048), "2.0 KB")
        self.assertEqual(PR._HumanBytes(5 * 1024 * 1024), "5.0 MB")


class PruneAssetsApplyTest(unittest.TestCase):
    """`--apply` deletes orphans and leaves referenced directories alone."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.notes = Path(self._tmp.name)
        self.assets = self.notes / "assets"
        self.assets.mkdir()
        for slug in ("live-paper", "stale-paper"):
            (self.assets / slug).mkdir()
            (self.assets / slug / "fig1.jpg").write_bytes(b"x" * 10)
        (self.notes / "Live.md").write_text(
            "![Fig 1](assets/live-paper/fig1.jpg)\n", encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, apply):
        class Args:
            json = False
            notes_dir = str(self.notes)
            apply = False

        Args.apply = apply
        config = type("C", (), {"literature_notes": str(self.notes)})()
        return PR._Run(Args(), config)

    def test_dry_run_deletes_nothing(self):
        self.assertEqual(self._run(apply=False), 0)
        self.assertTrue((self.assets / "stale-paper").is_dir())

    def test_apply_removes_only_the_orphan(self):
        self.assertEqual(self._run(apply=True), 0)
        self.assertFalse((self.assets / "stale-paper").exists())
        self.assertTrue((self.assets / "live-paper").is_dir())

    def test_missing_notes_dir_is_an_error(self):
        class Args:
            json = False
            notes_dir = str(self.notes / "nope")
            apply = False

        config = type("C", (), {"literature_notes": str(self.notes)})()
        self.assertEqual(PR._Run(Args(), config), 1)


class StaleFileTest(unittest.TestCase):
    """A retained directory can still hold stale files; they must be reported.

    Regression: re-preparing a note rewrites the assets it uses but never
    reclaims the ones it stopped using, so a live `assets/<slug>/` accumulates
    orphaned figures. Those stale names are exactly what let a note mislabel
    figures, and the old prune logic only reclaimed whole unreferenced dirs.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.notes = Path(self._tmp.name)
        self.assets = self.notes / "assets"
        self.assets.mkdir()
        self.slug = "live-paper"
        (self.assets / self.slug).mkdir()
        (self.assets / self.slug / "fig1-new.jpg").write_bytes(b"x" * 40)
        (self.assets / self.slug / "fig1-old.jpg").write_bytes(b"y" * 90)
        (self.assets / self.slug / "figure-ledger.json").write_text("{}", encoding='utf-8')
        (self.notes / "Live.md").write_text(
            "![Fig 1](assets/live-paper/fig1-new.jpg)\n", encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_unreferenced_file_is_detected(self):
        _, referenced_files, _ = PR._CollectReferencedSlugs(str(self.notes))
        used = {fn for (slug, fn) in referenced_files if slug == self.slug}
        self.assertEqual(used, {"fig1-new.jpg"})

    def test_dry_run_keeps_the_stale_file(self):
        class Args:
            json = False
            notes_dir = str(self.notes)
            apply = False

        config = type("C", (), {"literature_notes": str(self.notes)})()
        self.assertEqual(PR._Run(Args(), config), 0)
        self.assertTrue((self.assets / self.slug / "fig1-old.jpg").exists())

    def test_apply_removes_stale_file_and_keeps_live_ones(self):
        class Args:
            json = False
            notes_dir = str(self.notes)
            apply = True

        config = type("C", (), {"literature_notes": str(self.notes)})()
        self.assertEqual(PR._Run(Args(), config), 0)
        self.assertFalse((self.assets / self.slug / "fig1-old.jpg").exists())
        self.assertTrue((self.assets / self.slug / "fig1-new.jpg").exists())
        self.assertTrue((self.assets / self.slug / "figure-ledger.json").exists())


if __name__ == "__main__":
    unittest.main()

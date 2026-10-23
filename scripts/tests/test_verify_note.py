#!/usr/bin/env python3
"""Tests for the note verifier's figure baseline.

Regression: the figure check grepped ``full.md`` for ``![](images/<hex>)``, but
published MinerU caches keep image references in ``content_list.json`` instead —
0 matches in every real cache. The baseline was therefore always 0 and the check
reported "no baseline to compare" as a **pass**, so figure coverage was never
actually verified while the gate claimed it was.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero.commands.cmd_verify_note import _count_cache_images  # noqa: E402


class CountCacheImagesTest(unittest.TestCase):
    def _cache(self, tmp: str, *, content_list=None, with_full_md=False) -> str:
        cache_dir = Path(tmp)
        if content_list is not None:
            (cache_dir / "content_list.json").write_text(
                json.dumps(content_list), encoding="utf-8"
            )
        if with_full_md:
            # The real caches have a full.md with NO image references — this is
            # what made the old implementation return 0.
            (cache_dir / "full.md").write_text("# Title\n\nBody text.\n",
                                               encoding="utf-8")
        return str(cache_dir)

    def test_counts_image_blocks_in_content_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp, content_list=[
                {"type": "text", "text": "x"},
                {"type": "image", "img_path": "images/a.jpg"},
                {"type": "image", "img_path": "images/b.jpg"},
                {"type": "table", "text": "t"},
            ], with_full_md=True)
            self.assertEqual(_count_cache_images(cache), 2)

    def test_full_md_alone_is_not_a_baseline(self) -> None:
        """A cache whose full.md has no image refs and no content_list yields 0,
        which is exactly the case the verifier must treat as "no baseline"."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp, with_full_md=True)
            self.assertEqual(_count_cache_images(cache), 0)

    def test_falls_back_to_images_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "images").mkdir()
            (cache / "images" / "a.jpg").write_bytes(b"x")
            (cache / "images" / "b.png").write_bytes(b"x")
            self.assertEqual(_count_cache_images(str(cache)), 2)

    def test_content_list_wins_over_images_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "images").mkdir()
            for name in ("a.jpg", "b.jpg", "c.jpg"):
                (cache / "images" / name).write_bytes(b"x")
            (cache / "content_list.json").write_text(
                json.dumps([{"type": "image"}]), encoding="utf-8"
            )
            self.assertEqual(_count_cache_images(str(cache)), 1)

    def test_malformed_content_list_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "content_list.json").write_text("{not json", encoding="utf-8")
            (cache / "images").mkdir()
            (cache / "images" / "a.jpg").write_bytes(b"x")
            self.assertEqual(_count_cache_images(str(cache)), 1)

    def test_missing_directory_is_zero(self) -> None:
        self.assertEqual(_count_cache_images("/nonexistent/cache/dir"), 0)


if __name__ == "__main__":
    unittest.main()

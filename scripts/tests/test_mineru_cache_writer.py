#!/usr/bin/env python3
"""Tests for plugin-compatible MinerU cache writing.

The critical property: a cache written here must be indistinguishable from one
the plugin wrote, because the plugin's only presence gate is ``full.md``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
import io
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.mineru_cache_writer import (  # noqa: E402
    build_manifest,
    normalize_files,
    strip_local_image_embeds,
    write_mineru_cache,
)
from zotero.mineru_client import MineruParseError, MineruResult, _unzip_result  # noqa: E402


class StripImageEmbedsTest(unittest.TestCase):
    def test_local_embeds_removed(self) -> None:
        text = "# T\n\n![fig](images/a.png)\n\ntext\n"
        self.assertNotIn("images/a.png", strip_local_image_embeds(text))
        self.assertIn("text", strip_local_image_embeds(text))

    def test_external_links_kept(self) -> None:
        text = "![x](https://example.com/a.png)\n"
        self.assertIn("https://example.com/a.png", strip_local_image_embeds(text))

    def test_html_img_local_removed(self) -> None:
        text = '<img src="images/a.jpg" />\n'
        self.assertNotIn("images/a.jpg", strip_local_image_embeds(text))

    def test_blank_run_collapsed(self) -> None:
        text = "a\n\n\n\n\nb\n"
        self.assertNotIn("\n\n\n", strip_local_image_embeds(text))


class NormalizeFilesTest(unittest.TestCase):
    def test_nested_cloud_layout_flattens_images(self) -> None:
        files = {
            "paper/auto/paper.md": "# T\n",
            "paper/auto/images/f1.png": b"img",
            "paper/auto/content_list.json": b"[]",
            "paper/auto/layout.json": b"{}",
        }
        normalized, _ = normalize_files(files, "paper/auto/paper.md")
        self.assertIn("images/f1.png", normalized)
        self.assertIn("content_list.json", normalized)
        self.assertIn("full.md", normalized)
        # Non-durable artifacts must be dropped.
        self.assertNotIn("layout.json", normalized)

    def test_content_list_renamed(self) -> None:
        files = {
            "x/full.md": "# T\n",
            "x/abc_content_list.json": b"[]",
        }
        normalized, _ = normalize_files(files, "x/full.md")
        self.assertIn("content_list.json", normalized)
        self.assertNotIn("abc_content_list.json", normalized)

    def test_unsafe_paths_rejected(self) -> None:
        files = {
            "x/full.md": "# T\n",
            "../../etc/passwd": b"nope",
            "/abs/evil.png": b"nope",
            "x/images/ok.png": b"ok",
        }
        normalized, _ = normalize_files(files, "x/full.md")
        self.assertNotIn("/abs/evil.png", normalized)
        self.assertFalse(any("passwd" in k for k in normalized))

    def test_macosx_skipped(self) -> None:
        files = {"x/full.md": "# T\n", "__MACOSX/x/images/a.png": b"junk"}
        normalized, _ = normalize_files(files, "x/full.md")
        self.assertFalse(any(k.startswith("__MACOSX") for k in normalized))

    def test_markdown_replaced_by_canonical_full_md(self) -> None:
        files = {"x/full.md": "# T\n\n![a](images/a.png)\n", "x/images/a.png": b"i"}
        normalized, canonical = normalize_files(files, "x/full.md")
        # The stored full.md must have embeds stripped...
        self.assertNotIn(b"images/a.png", normalized["full.md"])
        # ...while the canonical text preserves them for manifest building.
        self.assertIn("images/a.png", canonical)


class BuildManifestTest(unittest.TestCase):
    def test_two_or_fewer_sections_is_no_sections(self) -> None:
        # Matches the plugin: a 2-heading manifest is useless for chunking.
        manifest = build_manifest("# Alpha\n\n## Bravo\n\ntext\n", None)
        self.assertTrue(manifest["noSections"])
        self.assertEqual(manifest["sections"], [])
        self.assertEqual(manifest["totalChars"], len("# Alpha\n\n## Bravo\n\ntext\n"))

    def test_sections_have_spans(self) -> None:
        md = "# Alpha\n\nbody\n\n# Bravo\n\nmore\n\n# Charlie\n\ntail\n"
        manifest = build_manifest(md, None)
        self.assertNotIn("noSections", manifest)
        self.assertEqual(len(manifest["sections"]), 3)
        first = manifest["sections"][0]
        self.assertEqual(first["heading"], "Alpha")
        self.assertLess(first["charStart"], first["charEnd"])
        # Sections must tile the document without gaps.
        self.assertEqual(first["charEnd"], manifest["sections"][1]["charStart"])

    def test_figures_from_content_list(self) -> None:
        md = "# Alpha\n\na\n\n# Bravo\n\nb\n\n# Charlie\n\nc\n"
        content = [
            {"type": "image", "img_path": "images/f.png",
             "image_caption": ["Figure 1: A thing."], "page_idx": 2},
        ]
        manifest = build_manifest(md, content)
        self.assertEqual(len(manifest["allFigures"]), 1)
        self.assertEqual(manifest["allFigures"][0]["path"], "images/f.png")
        self.assertEqual(manifest["totalPages"], 3)

    def test_caption_truncated_to_300(self) -> None:
        md = "# Alpha\n\na\n\n# Bravo\n\nb\n\n# Charlie\n\nc\n"
        content = [{"type": "image", "img_path": "i.png",
                    "image_caption": ["x" * 500]}]
        manifest = build_manifest(md, content)
        self.assertEqual(len(manifest["allFigures"][0]["caption"]), 300)


class WriteCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.result = MineruResult(
            markdown="# Tango\n\n![a](images/a.png)\n\n# Bravo\n\nb\n\n# Charlie\n\nc\n",
            files={
                "x/full.md": b"# T\n",
                "x/images/a.png": b"imgbytes",
                "x/content_list.json": b"[]",
            },
            content_list=[],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, **kwargs):
        params = dict(
            cache_root=self.root,
            attachment_id=42,
            result=self.result,
            markdown_name="x/full.md",
            attachment_key="ABCD1234",
            parent_item_key="EFGH5678",
        )
        params.update(kwargs)
        return write_mineru_cache(**params)

    def test_writes_expected_layout(self) -> None:
        written = self._write()
        cache = Path(written.cache_dir)
        self.assertTrue((cache / "full.md").is_file())
        self.assertTrue((cache / "manifest.json").is_file())
        self.assertTrue((cache / "_llm_source.json").is_file())
        self.assertTrue((cache / "images" / "a.png").is_file())

    def test_provenance_shape(self) -> None:
        written = self._write()
        prov = json.loads((Path(written.cache_dir) / "_llm_source.json").read_text(encoding='utf-8'))
        self.assertEqual(prov["kind"], "llm-for-zotero/mineru-cache-source")
        self.assertEqual(prov["version"], 2)
        self.assertEqual(prov["attachmentId"], 42)
        self.assertEqual(prov["origin"], "parsed")
        self.assertEqual(prov["attachmentKey"], "ABCD1234")
        self.assertIn("parsedAt", prov)

    def test_refuses_to_overwrite_by_default(self) -> None:
        self._write()
        with self.assertRaises(FileExistsError):
            self._write()

    def test_overwrite_prunes_non_durable(self) -> None:
        written = self._write()
        stale = Path(written.cache_dir) / "layout.json"
        stale.write_text("{}", encoding='utf-8')
        self._write(overwrite=True)
        self.assertFalse(stale.exists())

    def test_reported_chars_match_written_file(self) -> None:
        written = self._write()
        actual = len((Path(written.cache_dir) / "full.md").read_text(encoding='utf-8'))
        self.assertEqual(written.markdown_chars, actual)


class UnzipResultTest(unittest.TestCase):
    def _zip(self, entries: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return buffer.getvalue()

    def test_valid_zip(self) -> None:
        data = self._zip({"nested/full.md": b"# T\n"})
        result = _unzip_result(data)
        self.assertIn("T", result.markdown)

    def test_rejects_non_zip(self) -> None:
        with self.assertRaises(MineruParseError):
            _unzip_result(b"<html>not a zip</html>")

    def test_rejects_empty_markdown(self) -> None:
        with self.assertRaises(MineruParseError):
            _unzip_result(self._zip({"full.md": b"   \n"}))

    def test_prefers_full_md_over_other_markdown(self) -> None:
        data = self._zip({"a/summary.md": b"# wrong\n", "a/full.md": b"# right\n"})
        self.assertIn("right", _unzip_result(data).markdown)

    def test_skips_macosx(self) -> None:
        data = self._zip({"full.md": b"# T\n", "__MACOSX/junk.md": b"junk"})
        result = _unzip_result(data)
        self.assertNotIn("__MACOSX", result.files)


if __name__ == "__main__":
    unittest.main()

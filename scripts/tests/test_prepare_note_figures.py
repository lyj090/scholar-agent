#!/usr/bin/env python3
"""Regression tests for figure extraction in ``prepare-note``.

Extraction regressions are pinned here using synthetic fixtures whose figures had to be corrected by hand:

* **Charts were dropped.** Only ``type == "image"`` blocks were collected, so
  figures that MinerU emits as ``chart`` (Fig. 6 and Fig. 8 of this paper) never
  became assets. 1,011 chart blocks exist across the local corpus.
* **A thin manifest erased every caption.** ``_FigureMetadata`` read only
  ``sections[].figures``; a cache exporting just the flattened ``allFigures``
  produced an empty index, so every figure was named ``figN-figure.jpg``.
* **Columns were fused and interleaved.** A left-column block (x 127–439) and a
  right-column block (x 547–867) at the same height were merged into one
  composite, and ``(top, left)`` ordering split the two Figure 7 panels apart.
* **Drifted captions mis-numbered the set.** The block captioned ``Fig. 7`` is
  really Figure 8, and the one captioned ``Fig. 8`` is really Figure 9, which
  yielded two ``fig7-*`` assets, no Figure 9, and Figure 9 carrying Figure 8's
  caption.
"""

from __future__ import annotations

import json
import io
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero.commands import cmd_prepare_note as P  # noqa: E402


def _block(kind, path, page, bbox, caption=""):
    item = {"type": kind, "img_path": path, "page_idx": page, "bbox": list(bbox)}
    if caption:
        key = "chart_caption" if kind == "chart" else "image_caption"
        item[key] = [caption]
    return item


class RefreshAssetTest(unittest.TestCase):
    def test_refresh_copies_changed_cache_image_and_updates_candidate_hash(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cache = root/'cache'; (cache/'images').mkdir(parents=True)
            source = cache/'images/abc.png'; source.write_bytes(b'first-image')
            (cache/'full.md').write_text('![](images/abc.png)\nFigure 1: Overview.\n', encoding='utf-8')
            api = MagicMock(); api.get_item.return_value = {'data': {'title': 'Synthetic paper', 'creators': [], 'tags': []}}; api.get_children.return_value = []
            config = SimpleNamespace(literature_notes=str(root/'notes'), vault=tmp, api_url='http://localhost:23119', user_id='0')
            args = SimpleNamespace(key='ITEM0001', force=True, json=True)
            with patch.object(P, 'ZoteroAPI', return_value=api), patch.object(P, 'MineruLookup') as lookup:
                lookup.return_value.find_cache_dir.return_value = str(cache)
                for content in (b'first-image', b'updated-image'):
                    source.write_bytes(content); out = io.StringIO()
                    with redirect_stdout(out): self.assertEqual(P._Run(args, config), 0)
                    result = json.loads(out.getvalue()); asset = Path(result['asset_abs_dir'])/result['figures'][0]['filename']
                    self.assertEqual(asset.read_bytes(), content)
                    candidates = json.loads(Path(result['figure_candidates']).read_text(encoding='utf-8'))
                    self.assertEqual(candidates['assets'][0]['sha256'], hashlib.sha256(content).hexdigest())
                    self.assertEqual(candidates['assets'][0]['status'], 'needs-review')


class FigureMetadataTest(unittest.TestCase):
    def test_flattened_all_figures_is_read(self) -> None:
        """2 of 254 local caches export only ``allFigures``. Ignoring it made
        every caption vanish, silently, because a missing record looks like
        'this figure has no caption'."""
        manifest = {
            "sections": [{"figures": []}],
            "allFigures": [
                {"path": "images/a.png", "label": "Fig. 1.", "caption": "First"},
            ],
        }
        metadata = P._FigureMetadata(manifest)
        self.assertEqual(metadata["images/a.png"]["label"], "Fig. 1.")
        self.assertEqual(metadata["images/a.png"]["caption"], "First")

    def test_sections_still_work(self) -> None:
        manifest = {
            "sections": [
                {"figures": [{"path": "images/a.png", "label": "Fig. 2.",
                              "caption": "Second"}]}
            ]
        }
        metadata = P._FigureMetadata(manifest)
        self.assertEqual(metadata["images/a.png"]["label"], "Fig. 2.")

    def test_sections_win_over_a_duplicate_in_all_figures(self) -> None:
        manifest = {
            "sections": [{"figures": [{"path": "i.png", "label": "Fig. 3.",
                                       "caption": "S"}]}],
            "allFigures": [{"path": "i.png", "label": "image-3", "caption": ""}],
        }
        metadata = P._FigureMetadata(manifest)
        self.assertEqual(metadata["i.png"]["label"], "Fig. 3.")
        self.assertEqual(metadata["i.png"]["caption"], "S")


class ContentListCaptionTest(unittest.TestCase):
    def test_chart_captions_are_collected(self) -> None:
        """Charts carry real figure captions; only image_caption was read."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "content_list.json"), "w", encoding="utf-8") as fh:
                json.dump([
                    _block("chart", "images/c.jpg", 5, [0, 0, 10, 10], "Fig. 6. Latency"),
                    _block("image", "images/i.jpg", 5, [0, 0, 10, 10], "Fig. 7. Pipeline"),
                ], fh)
            captions = P._ContentListCaptions(tmp)
        self.assertEqual(captions["images/c.jpg"], "Fig. 6. Latency")
        self.assertEqual(captions["images/i.jpg"], "Fig. 7. Pipeline")


class ColumnGroupingTest(unittest.TestCase):
    def test_side_by_side_blocks_are_not_merged(self) -> None:
        """The measured over-merge: two columns at the same height became one
        composite asset describing two unrelated figures."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "content_list.json"), "w", encoding="utf-8") as fh:
                json.dump([
                    _block("chart", "images/left.jpg", 6, [147, 60, 419, 166]),
                    _block("image", "images/right.jpg", 6, [547, 60, 867, 244]),
                ], fh)
            groups = P._ExtractFigureGroups(None, tmp, "")
        self.assertEqual(len(groups), 2, "one asset per column, not one fused asset")

    def test_a_chart_is_included_as_a_figure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "content_list.json"), "w", encoding="utf-8") as fh:
                json.dump([
                    _block("chart", "images/c.jpg", 5, [508, 257, 911, 390],
                           "Fig. 6. Latency of event counting"),
                ], fh)
            groups = P._ExtractFigureGroups(None, tmp, "")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["source_paths"], ["images/c.jpg"])

    def test_tables_are_not_treated_as_figures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            table = _block("table", "images/t.jpg", 1, [81, 109, 913, 299])
            table["table_caption"] = ["TABLE I"]
            with open(os.path.join(tmp, "content_list.json"), "w", encoding="utf-8") as fh:
                json.dump([table], fh)
            groups = P._ExtractFigureGroups(None, tmp, "")
        self.assertEqual(groups, [])

    def test_adjacent_narrow_blocks_in_one_column_still_merge(self) -> None:
        """The column rule must not stop genuine multi-crop figures merging."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "content_list.json"), "w", encoding="utf-8") as fh:
                json.dump([
                    _block("image", "images/top.jpg", 6, [127, 60, 439, 166]),
                    _block("image", "images/bottom.jpg", 6, [127, 169, 439, 306]),
                ], fh)
            groups = P._ExtractFigureGroups(None, tmp, "")
        self.assertEqual(len(groups), 2, "proximity is not evidence of shared figure identity")


class ReadingOrderTest(unittest.TestCase):
    def test_two_columns_are_read_column_major(self) -> None:
        """``(top, left)`` sorting interleaves columns; a reader finishes the
        left column first."""
        page = [
            {"bbox": [100.0, 60.0, 400.0, 160.0], "path": "L1"},
            {"bbox": [550.0, 60.0, 850.0, 160.0], "path": "R1"},
            {"bbox": [100.0, 200.0, 400.0, 300.0], "path": "L2"},
            {"bbox": [550.0, 200.0, 850.0, 300.0], "path": "R2"},
        ]
        ordered = [item["path"] for item in P._PageReadingOrder(page)]
        self.assertEqual(ordered, ["L1", "L2", "R1", "R2"])

    def test_full_width_block_does_not_create_a_column(self) -> None:
        page = [
            {"bbox": [50.0, 50.0, 950.0, 150.0], "path": "WIDE"},
            {"bbox": [100.0, 200.0, 400.0, 300.0], "path": "A"},
            {"bbox": [550.0, 200.0, 850.0, 300.0], "path": "B"},
        ]
        ordered = [item["path"] for item in P._PageReadingOrder(page)]
        self.assertEqual(ordered[0], "WIDE")


class FigureNumberingTest(unittest.TestCase):
    def _groups(self):
        return [
            {"caption": "Fig. 1. One", "label": "Fig. 1."},
            {"caption": "Fig. 4. Four", "label": "image-4"},
            {"caption": "Fig. 7. Seven", "label": "Fig. 7."},
            {"caption": "", "label": "image-7"},
        ]

    def test_uncaptioned_blocks_remain_unresolved(self):
        groups = self._groups()
        P._RepairFigureNumbers(groups, "Figure 8: An unrelated caption.")
        self.assertEqual([g["figure_number"] for g in groups], [1, 4, 7, None])
        self.assertTrue(all(g["label"].startswith("Asset ") for g in groups))
        self.assertTrue(all(g["figure_number_source"] != "verified" for g in groups))

    def test_repeated_panels_and_appendix_numbers_are_preserved_as_candidates(self):
        groups = [{"caption": c} for c in ["Fig. 3. Left", "Fig. 3. Right", "Figure S.1: Supplement", "Fig. B.3. Supplement", "Figure 1: Appendix restart"]]
        P._RepairFigureNumbers(groups, "")
        self.assertEqual([g["figure_number"] for g in groups], [3, 3, "S.1", "B.3", 1])

    def test_prose_mentions_do_not_supply_an_identity(self):
        groups = [{"caption": "Comparison with Figure 2."}, {"caption": ""}]
        P._RepairFigureNumbers(groups, "")
        self.assertEqual([g["figure_number"] for g in groups], [None, None])


class CaptionParsingTest(unittest.TestCase):
    def test_fig_dot_form_is_parsed(self) -> None:
        """The previous regex demanded ``Figure N:``; arXiv papers write
        ``Fig. N.``, so the caption map came back empty and the repair pass that
        depends on it silently did nothing."""
        captions = P._ExtractFigureCaptions(
            "Fig. 1. First caption.\nFig. 2. Second caption.\n"
        )
        self.assertEqual(captions["1"], "First caption.")
        self.assertEqual(captions["2"], "Second caption.")

    def test_figure_colon_form_still_parsed(self) -> None:
        captions = P._ExtractFigureCaptions("Figure 3: Third caption.\n")
        self.assertEqual(captions["3"], "Third caption.")

    def test_caption_does_not_swallow_the_next_label(self) -> None:
        captions = P._ExtractFigureCaptions(
            "Fig. 1. First line.\ncontinued line.\nFig. 2. Second.\n"
        )
        self.assertEqual(captions["1"], "First line. continued line.")
        self.assertEqual(captions["2"], "Second.")

    def test_figure_numbers_finds_every_mention(self) -> None:
        self.assertEqual(
            P._FigureNumbers("Fig. 2. A. Fig. 3. B."), [2, 3]
        )


class SemanticFilenameTest(unittest.TestCase):
    def test_verified_number_beats_the_label(self) -> None:
        """The label is exactly the drift-prone text numbering repair corrects,
        so deriving the filename from it would reintroduce the wrong number."""
        name = P._SemanticFilename(
            3, "Fig. 8.", "Capture rig for the case study", ".jpg", 9
        )
        self.assertTrue(name.startswith("asset-003-"), name)
        self.assertNotIn("asset-008", name)

    def test_label_used_when_no_verified_number(self) -> None:
        self.assertEqual(
            P._SemanticFilename(1, "Fig. 2.", "Some caption", ".jpg"),
            "asset-001-some-caption.jpg",
        )

    def test_position_used_when_nothing_else(self) -> None:
        self.assertEqual(
            P._SemanticFilename(4, "", "", ".jpg"), "asset-004-figure.jpg"
        )


if __name__ == "__main__":
    unittest.main()

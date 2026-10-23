#!/usr/bin/env python3
"""Tests for the plugin-free MinerU auto-parse equivalent.

The plugin parsed new PDFs by registering a Zotero `Notifier` observer — an
in-process hook. `mineru-sync` reproduces the effect by polling. These tests pin
the two properties that make it safe to run unattended from cron:

  1. it never re-parses a PDF that already has a cache (so it cannot fight the
     plugin, or a concurrent run, and cannot burn API credit twice);
  2. it skips course material and scans by default, because a run that silently
     started paying to parse 58 textbooks would be an expensive surprise.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

from zotero.commands import cmd_auto_parse as ap  # noqa: E402

# The failure state is real machine-local state (it is what stops an unattended
# run from re-paying for a PDF the service keeps rejecting). Point it at a temp
# file for the whole module so a test run can never write the operator's copy —
# the same class of bug that once polluted the action ledger.
_STATE_TMPDIR: tempfile.TemporaryDirectory | None = None
_ORIGINAL_STATE_PATH = None


def setUpModule() -> None:
    global _STATE_TMPDIR, _ORIGINAL_STATE_PATH
    _STATE_TMPDIR = tempfile.TemporaryDirectory(prefix="mineru-sync-state-")
    _ORIGINAL_STATE_PATH = ap._failure_state_path
    ap._failure_state_path = lambda: str(Path(_STATE_TMPDIR.name) / "failures.json")


def tearDownModule() -> None:
    global _STATE_TMPDIR
    if _ORIGINAL_STATE_PATH is not None:
        ap._failure_state_path = _ORIGINAL_STATE_PATH
    if _STATE_TMPDIR is not None:
        _STATE_TMPDIR.cleanup()
        _STATE_TMPDIR = None


class ExcludeTest(unittest.TestCase):
    def test_matches_title_and_filename(self) -> None:
        self.assertEqual(ap._excluded("数值分析 讲义", "/x/a.pdf", ("讲义",)), "讲义")
        self.assertEqual(ap._excluded("paper", "/x/homework-1.pdf", ("homework",)), "homework")

    def test_case_insensitive(self) -> None:
        self.assertEqual(ap._excluded("A LECTURE Note", "/x/a.pdf", ("lecture",)), "lecture")

    def test_returns_none_when_clean(self) -> None:
        self.assertIsNone(ap._excluded("A Real Paper", "/x/p.pdf", ("讲义", "homework")))

    def test_default_exclusions_cover_course_material(self) -> None:
        for title in ("矩阵分析 补充讲义", "新中特 课程资料",
                      "Lecture 3 slides", "Problem Set 2", "作业3"):
            self.assertIsNotNone(
                ap._excluded(title, "/x/a.pdf", ap.DEFAULT_EXCLUDE),
                f"{title!r} should be excluded by default",
            )

    def test_papers_are_not_excluded(self) -> None:
        for title in ("Mathematical Foundations of Reinforcement Learning",
                      "Event-based Human Action Recognition",
                      "Linear Systems Theory"):
            self.assertIsNone(
                ap._excluded(title, "/x/a.pdf", ap.DEFAULT_EXCLUDE),
                f"{title!r} should not be excluded",
            )


class PendingTest(unittest.TestCase):
    """`_pending` is where "already parsed" and "should skip" are decided."""

    def _config(self, cache_root: str) -> SimpleNamespace:
        return SimpleNamespace(
            api_url="http://127.0.0.1:23119",
            user_id="0",
            mineru_cache=cache_root,
            data_dir="/tmp/zotero-data",
        )

    def _rows(self):
        return [
            {"item_id": 1, "attachment_key": "CACHED01", "title": "Already parsed",
             "pdf_path": "/tmp/a.pdf", "parent_item_key": None, "date_added": ""},
            {"item_id": 2, "attachment_key": "NEWPDF01", "title": "A Real Paper",
             "pdf_path": "/tmp/b.pdf", "parent_item_key": None, "date_added": ""},
            {"item_id": 3, "attachment_key": "COURSE01", "title": "数值分析 讲义",
             "pdf_path": "/tmp/c.pdf", "parent_item_key": None, "date_added": ""},
            {"item_id": 4, "attachment_key": "NOPDF001", "title": "Missing file",
             "pdf_path": "/tmp/does-not-exist.pdf", "parent_item_key": None,
             "date_added": ""},
        ]

    def _run(self, *, include_all: bool, extra=None):
        """Real temp PDFs are used rather than patching os.path.isfile: patching
        it through the module would mutate the shared `os` module and break
        every other file check in the call path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / "cache"
            (cache_root / "1").mkdir(parents=True)     # item_id 1 already cached

            pdfs = {}
            for name in ("a", "b", "c"):
                pdf = root / f"{name}.pdf"
                pdf.write_bytes(b"%PDF-1.4")
                pdfs[name] = str(pdf)

            rows = [
                {"item_id": 1, "attachment_key": "CACHED01", "title": "Already parsed",
                 "pdf_path": pdfs["a"], "parent_item_key": None, "date_added": "",
                 "item_type": "journalArticle"},
                {"item_id": 2, "attachment_key": "NEWPDF01", "title": "A Real Paper",
                 "pdf_path": pdfs["b"], "parent_item_key": None, "date_added": "",
                 "item_type": "conferencePaper"},
                {"item_id": 3, "attachment_key": "COURSE01", "title": "数值分析 讲义",
                 "pdf_path": pdfs["c"], "parent_item_key": None, "date_added": "",
                 "item_type": "journalArticle"},
                {"item_id": 4, "attachment_key": "NOPDF001", "title": "Missing file",
                 "pdf_path": str(root / "gone.pdf"), "parent_item_key": None,
                 "date_added": "", "item_type": "journalArticle"},
                {"item_id": 5, "attachment_key": "BOOK0001", "title": "Some Textbook",
                 "pdf_path": pdfs["a"], "parent_item_key": None, "date_added": "",
                 "item_type": "book"},
            ]

            catalog = MagicMock()
            catalog.records.return_value = [MagicMock(cache_id=1)]
            database = MagicMock()
            database.__enter__ = lambda s: s
            database.__exit__ = lambda *a: False
            database.all_pdf_attachments.return_value = rows

            with patch("zotero.mineru_catalog.MineruCatalog", return_value=catalog), \
                 patch("zotero.api.ZoteroAPI"), \
                 patch("zotero.commands.cmd_auto_parse.ZoteroDatabase", return_value=database):
                return ap._pending(
                    self._config(str(cache_root)),
                    include_all=include_all,
                    extra=extra or [],
                )

    def test_cached_pdf_is_not_reparsed(self) -> None:
        pending, skipped = self._run(include_all=False)
        keys = [p["attachment_key"] for p in pending]
        self.assertNotIn("CACHED01", keys, "an existing cache must never be re-parsed")

    def test_missing_file_is_ignored(self) -> None:
        pending, _skipped = self._run(include_all=False)
        self.assertNotIn("NOPDF001", [p["attachment_key"] for p in pending])

    def test_course_material_skipped_by_default(self) -> None:
        pending, skipped = self._run(include_all=False)
        self.assertNotIn("COURSE01", [p["attachment_key"] for p in pending])
        self.assertIn("COURSE01", [s["attachment_key"] for s in skipped])

    def test_include_all_opts_into_books(self) -> None:
        pending, _skipped = self._run(include_all=True)
        self.assertIn("BOOK0001", [p["attachment_key"] for p in pending])

    def test_course_notes_stay_excluded_even_under_all(self) -> None:
        """--all widens the *item type* filter, not the filename filter: notes
        that were typed as a paper are still not papers worth paying to parse."""
        pending, _skipped = self._run(include_all=True)
        self.assertNotIn("COURSE01", [p["attachment_key"] for p in pending])

    def test_extra_exclusion_is_honoured(self) -> None:
        pending, skipped = self._run(include_all=False, extra=["Real Paper"])
        self.assertNotIn("NEWPDF01", [p["attachment_key"] for p in pending])
        self.assertIn("NEWPDF01", [s["attachment_key"] for s in skipped])


class StateIsolationTest(unittest.TestCase):
    """A test run must never write the operator's real retry counters.

    Regression: an earlier version of this module wrote `BAD00001` into
    `data/mineru-sync-failures.json`, exactly the way the action ledger was once
    polluted. Real state that influences unattended spend must be isolated.
    """

    def test_state_path_is_redirected_outside_the_repo(self) -> None:
        real = Path(ap._failure_state_path()).resolve()
        repo_data = (Path(__file__).resolve().parents[2] / "data").resolve()
        self.assertFalse(
            str(real).startswith(str(repo_data)),
            f"tests would write the real failure state: {real}",
        )

    def test_a_failing_run_lands_in_the_temp_state(self) -> None:
        pending = [{"attachment_key": "POLLUTE1", "title": "bad",
                    "pdf_path": "/x.pdf", "item_id": 1, "parent_item_key": None}]
        with patch.object(ap, "_pending", return_value=(pending, [])):
            with patch("zotero.commands.cmd_mineru_parse.ParseOne",
                       side_effect=RuntimeError("api down")):
                ap._Run(SimpleNamespace(limit=1, dry_run=False, all=False,
                                        exclude=None, mode="cloud",
                                        json=False, quiet=True), object())
        self.assertIn("POLLUTE1", ap._load_failures())


class FailureRetryTest(unittest.TestCase):
    """Unattended runs must not retry a permanently-failing PDF forever.

    Without this, a scanned book the MinerU service rejects costs one API call
    per cron tick, indefinitely — the failure is silent and the spend is not.
    """

    def test_records_attempt_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "failures.json"
            with patch.object(ap, "_failure_state_path", return_value=str(state)):
                ap._save_failures({"K1": {"attempts": 2, "error": "boom"}})
                got = ap._load_failures()
        self.assertEqual(got["K1"]["attempts"], 2)

    def test_missing_state_file_is_empty_not_an_error(self) -> None:
        with patch.object(ap, "_failure_state_path",
                          return_value="/nonexistent/f.json"):
            self.assertEqual(ap._load_failures(), {})

    def test_corrupt_state_file_degrades_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "f.json"
            state.write_text("{not json", encoding="utf-8")
            with patch.object(ap, "_failure_state_path", return_value=str(state)):
                self.assertEqual(ap._load_failures(), {})

    def test_save_is_atomic(self) -> None:
        """A half-written file would silently reset every retry counter."""
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "f.json"
            with patch.object(ap, "_failure_state_path", return_value=str(state)):
                ap._save_failures({"A": {"attempts": 1}})
                ap._save_failures({"A": {"attempts": 1}, "B": {"attempts": 2}})
                got = ap._load_failures()
            self.assertEqual(set(got), {"A", "B"})
            self.assertFalse((Path(tmp) / "f.json.tmp").exists())

    def test_signature_changes_with_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.pdf"
            f.write_bytes(b"one")
            first = ap._signature(str(f))
            f.write_bytes(b"two-longer")
            self.assertNotEqual(first, ap._signature(str(f)))

    def test_signature_of_missing_file_is_empty(self) -> None:
        self.assertEqual(ap._signature("/nonexistent/x.pdf"), "")

    def test_exhausted_pdf_is_skipped(self) -> None:
        rows = [{"item_id": 2, "attachment_key": "FLAKY001", "title": "A Paper",
                 "pdf_path": "/tmp/a.pdf", "parent_item_key": None,
                 "date_added": "", "item_type": "journalArticle"}]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "a.pdf"
            pdf.write_bytes(b"%PDF")
            rows[0]["pdf_path"] = str(pdf)
            sig = ap._signature(str(pdf))
            catalog = MagicMock()
            catalog.records.return_value = []
            database = MagicMock()
            database.__enter__ = lambda s: s
            database.__exit__ = lambda *a: False
            database.all_pdf_attachments.return_value = rows
            config = SimpleNamespace(api_url="u", user_id="0",
                                     mineru_cache=tmp, data_dir=tmp)
            state = Path(tmp) / "failures.json"
            with patch("zotero.commands.cmd_auto_parse.ZoteroDatabase", return_value=database), \
                 patch("zotero.mineru_catalog.MineruCatalog", return_value=catalog), \
                 patch("zotero.api.ZoteroAPI"), \
                 patch.object(ap, "_failure_state_path", return_value=str(state)):
                ap._save_failures({"FLAKY001": {
                    "attempts": ap.MAX_ATTEMPTS, "signature": sig, "error": "no"}})
                pending, skipped = ap._pending(config, include_all=False, extra=[])
        self.assertEqual(pending, [])
        self.assertIn("FLAKY001", [s["attachment_key"] for s in skipped])
        self.assertIn("failed", skipped[0]["reason"])

    def test_replaced_file_is_retried(self) -> None:
        """A new file under the same key deserves a fresh attempt."""
        rows = [{"item_id": 2, "attachment_key": "FLAKY001", "title": "A Paper",
                 "pdf_path": "/tmp/a.pdf", "parent_item_key": None,
                 "date_added": "", "item_type": "journalArticle"}]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "a.pdf"
            pdf.write_bytes(b"%PDF")
            rows[0]["pdf_path"] = str(pdf)
            catalog = MagicMock()
            catalog.records.return_value = []
            database = MagicMock()
            database.__enter__ = lambda s: s
            database.__exit__ = lambda *a: False
            database.all_pdf_attachments.return_value = rows
            config = SimpleNamespace(api_url="u", user_id="0",
                                     mineru_cache=tmp, data_dir=tmp)
            state = Path(tmp) / "failures.json"
            with patch("zotero.commands.cmd_auto_parse.ZoteroDatabase", return_value=database), \
                 patch("zotero.mineru_catalog.MineruCatalog", return_value=catalog), \
                 patch("zotero.api.ZoteroAPI"), \
                 patch.object(ap, "_failure_state_path", return_value=str(state)):
                ap._save_failures({"FLAKY001": {
                    "attempts": ap.MAX_ATTEMPTS, "signature": "stale:0",
                    "error": "no"}})
                pending, _skipped = ap._pending(config, include_all=False, extra=[])
        self.assertEqual([p["attachment_key"] for p in pending], ["FLAKY001"])


class RunTest(unittest.TestCase):
    def _args(self, **over):
        base = dict(limit=5, dry_run=True, all=False, exclude=None,
                    mode="cloud", json=False, quiet=True)
        base.update(over)
        return SimpleNamespace(**base)

    def test_dry_run_parses_nothing(self) -> None:
        pending = [{"attachment_key": "K1", "title": "T", "pdf_path": "/x.pdf",
                    "item_id": 1, "parent_item_key": None}]
        with patch.object(ap, "_pending", return_value=(pending, [])):
            with patch("zotero.commands.cmd_mineru_parse.ParseOne") as parse_one:
                code = ap._Run(self._args(dry_run=True), object())
        self.assertEqual(code, 0)
        parse_one.assert_not_called()

    def test_limit_bounds_the_run(self) -> None:
        pending = [
            {"attachment_key": f"K{i}", "title": "T", "pdf_path": "/x.pdf",
             "item_id": i, "parent_item_key": None}
            for i in range(10)
        ]
        with patch.object(ap, "_pending", return_value=(pending, [])):
            with patch("zotero.commands.cmd_mineru_parse.ParseOne",
                       return_value={"cache_id": 1}) as parse_one:
                code = ap._Run(self._args(dry_run=False, limit=3), object())
        self.assertEqual(code, 0)
        self.assertEqual(parse_one.call_count, 3, "a cron tick must stay bounded")

    def test_already_cached_race_is_a_skip_not_a_failure(self) -> None:
        """If another run (or the plugin) wins the race, that is success."""
        pending = [{"attachment_key": "K1", "title": "T", "pdf_path": "/x.pdf",
                    "item_id": 1, "parent_item_key": None}]
        with patch.object(ap, "_pending", return_value=(pending, [])):
            with patch("zotero.commands.cmd_mineru_parse.ParseOne",
                       side_effect=FileExistsError("already cached")):
                code = ap._Run(self._args(dry_run=False), object())
        self.assertEqual(code, 0, "a lost race must not fail the cron job")

    def test_one_failure_does_not_abort_the_batch(self) -> None:
        pending = [
            {"attachment_key": "BAD00001", "title": "bad", "pdf_path": "/x.pdf",
             "item_id": 1, "parent_item_key": None},
            {"attachment_key": "GOOD0001", "title": "good", "pdf_path": "/y.pdf",
             "item_id": 2, "parent_item_key": None},
        ]
        calls = []

        def parse_one(*a, **kw):
            calls.append(kw["attachment_id"])
            if kw["attachment_id"] == 1:
                raise RuntimeError("api down")
            return {"cache_id": 2}

        with patch.object(ap, "_pending", return_value=(pending, [])):
            with patch("zotero.commands.cmd_mineru_parse.ParseOne", side_effect=parse_one):
                code = ap._Run(self._args(dry_run=False), object())
        self.assertEqual(code, 0)
        self.assertEqual(calls, [1, 2], "the second item must still be attempted")

    def test_nothing_pending_is_success(self) -> None:
        with patch.object(ap, "_pending", return_value=([], [])):
            code = ap._Run(self._args(dry_run=False), object())
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

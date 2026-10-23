#!/usr/bin/env python3
"""Tests for the two cache/lifecycle defects that made a local paper look absent.

Two failures are pinned here, both observed on a real library:

1. **Zotero detection matched the caller.** The launcher used
   ``pgrep -f zotero``; because the pattern is matched against full command
   lines, the probing process saw *its own* command line and concluded Zotero
   was running. The launcher then waited for an API that would never answer
   instead of starting the editor.

2. **A stale identifier index hid a freshly attached PDF.** The cache was
   governed by a TTL alone, so between an attachment being created and the TTL
   expiring, ``paper-status`` kept answering from a view that predated the PDF —
   the exact signal that sends an agent to the web for a paper it already owns.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from zotero import library_index as li  # noqa: E402
from zotero.launcher import ZoteroLauncher, _running_pids  # noqa: E402


class RunningPidsTest(unittest.TestCase):
    def test_does_not_report_the_caller_itself(self) -> None:
        """The regression that started this: a process whose *arguments* mention
        zotero is not Zotero. Detection is by executable name, so the test
        process (whose argv contains this file's text) must never appear."""
        pids = _running_pids()
        self.assertNotIn(os.getpid(), pids)

    def test_returns_only_ints(self) -> None:
        for pid in _running_pids():
            self.assertIsInstance(pid, int)


class LauncherDetectionTest(unittest.TestCase):
    def _launcher(self) -> ZoteroLauncher:
        config = MagicMock()
        config.api_url = "http://127.0.0.1:23999"
        config.user_id = "0"
        return ZoteroLauncher(config)

    def test_port_comes_from_config_not_a_constant(self) -> None:
        """Hardcoding 23119 reported the wrong thing on a moved port."""
        launcher = self._launcher()
        self.assertEqual(launcher._port, 23999)

    def test_start_is_attempted_when_api_is_down_and_no_process_runs(self) -> None:
        launcher = self._launcher()
        with patch.object(ZoteroLauncher, "is_zotero_ready", return_value=False):
            with patch.object(ZoteroLauncher, "is_zotero_running", return_value=False):
                with patch.object(ZoteroLauncher, "_find_binary", return_value="/bin/true"):
                    with patch("subprocess.Popen") as popen:
                        with patch.object(
                            ZoteroLauncher, "is_zotero_ready",
                            side_effect=[False, True],
                        ):
                            result = launcher.ensure_zotero_running(max_wait=5)
        self.assertEqual(result["status"], "started")
        popen.assert_called_once()

    def test_no_binary_is_a_precise_error(self) -> None:
        launcher = self._launcher()
        with patch.object(ZoteroLauncher, "is_zotero_ready", return_value=False):
            with patch.object(ZoteroLauncher, "is_zotero_running", return_value=False):
                with patch.object(ZoteroLauncher, "_find_binary", return_value=None):
                    result = launcher.ensure_zotero_running(max_wait=1)
        self.assertEqual(result["status"], "error")
        self.assertIn("could not be started", result["message"])

    def test_already_ready_short_circuits(self) -> None:
        launcher = self._launcher()
        with patch.object(ZoteroLauncher, "is_zotero_ready", return_value=True):
            result = launcher.ensure_zotero_running(max_wait=1)
        self.assertEqual(result["status"], "ready")


class LibraryVersionTest(unittest.TestCase):
    def test_offline_version_is_read(self) -> None:
        from zotero.config import load_config
        from zotero.zotero_db import ZoteroDatabase

        # load_config() hard-exits when a machine has no vault or library, which
        # is the normal state in CI. Skipping is correct: this test exercises a
        # real Zotero database, and there is nothing to exercise without one.
        try:
            config = load_config()
        except SystemExit:
            self.skipTest("no configured library")
        if not os.path.isfile(config.sqlite_path):
            self.skipTest("no local Zotero database")
        with ZoteroDatabase(config.data_dir) as database:
            version = database.library_version()
        self.assertTrue(version.isdigit(), f"expected a numeric version, got {version!r}")

    def test_unknown_version_is_empty_not_zero(self) -> None:
        """'' must mean unknown, so a comparison cannot treat it as 'unchanged'.
        A missing database is the realistic way to reach that state."""
        from zotero.zotero_db import ZoteroDatabase

        with tempfile.TemporaryDirectory() as tmp:
            database = ZoteroDatabase(os.path.join(tmp, "no-such-data-dir"))
            self.assertEqual(database.library_version(), "")


class LibraryIndexCacheTest(unittest.TestCase):
    def _config(self, data_dir: str):
        config = MagicMock()
        config.api_url = "http://127.0.0.1:23119"
        config.user_id = "0"
        config.data_dir = data_dir
        return config

    def _entry(self, key: str, arxiv: str) -> li.LibraryEntry:
        return li.LibraryEntry(
            item_key=key, title="T", item_type="preprint", arxiv_id=arxiv
        )

    def test_version_change_forces_a_rebuild(self) -> None:
        """The core fix: a TTL that has not expired must not win over a library
        that has actually changed."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.json")
            config = self._config(tmp)
            calls: list[int] = []
            # Stands in for libraries.version, which Zotero bumps on every write.
            holder = {"v": "100"}

            def fake_offline(data_dir):
                calls.append(1)
                return [self._entry("AAAA1111", "2405.00001")], {}, holder["v"]

            with patch.object(li, "_fetch_library_offline", side_effect=fake_offline):
                with patch.object(
                    li, "current_library_version",
                    side_effect=lambda _config: holder["v"],
                ):
                    first = li.build_library_index(config, cache_path=path, force=True)
                    self.assertEqual(first.library_version, "100")

                    # A PDF gets attached: the library version moves on while the
                    # cache file is still well inside its TTL.
                    holder["v"] = "200"

                    second = li.build_library_index(config, cache_path=path)
                    self.assertEqual(second.library_version, "200")
                    self.assertEqual(len(calls), 2, "version change must trigger a rescan")

    def test_unchanged_version_serves_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.json")
            config = self._config(tmp)
            calls: list[int] = []

            def fake_offline(data_dir):
                calls.append(1)
                return [self._entry("AAAA1111", "2405.00001")], {}, "100"

            with patch.object(li, "_fetch_library_offline", side_effect=fake_offline):
                with patch.object(li, "current_library_version", return_value="100"):
                    li.build_library_index(config, cache_path=path, force=True)
                    li.build_library_index(config, cache_path=path)
            self.assertEqual(len(calls), 1, "an unchanged library must reuse the cache")

    def test_unknown_version_falls_back_to_ttl(self) -> None:
        """If the version cannot be read, the cache must still be usable rather
        than rebuild on every single call."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.json")
            config = self._config(tmp)
            calls: list[int] = []

            def fake_offline(data_dir):
                calls.append(1)
                return [self._entry("AAAA1111", "2405.00001")], {}, ""

            with patch.object(li, "_fetch_library_offline", side_effect=fake_offline):
                with patch.object(li, "current_library_version", return_value=""):
                    li.build_library_index(config, cache_path=path, force=True)
                    li.build_library_index(config, cache_path=path)
            self.assertEqual(len(calls), 1)

    def test_expired_ttl_forces_a_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.json")
            config = self._config(tmp)
            calls: list[int] = []

            def fake_offline(data_dir):
                calls.append(1)
                return [self._entry("AAAA1111", "2405.00001")], {}, "100"

            with patch.object(li, "_fetch_library_offline", side_effect=fake_offline):
                li.build_library_index(config, cache_path=path, force=True)
                li.build_library_index(config, cache_path=path, max_age_seconds=0)
            self.assertEqual(len(calls), 2)

    def test_invalidate_removes_the_cache_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.json")
            config = self._config(tmp)
            with patch.object(
                li, "_fetch_library_offline",
                return_value=([self._entry("AAAA1111", "2405.00001")], {}, "100"),
            ):
                li.build_library_index(config, cache_path=path, force=True)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(li.invalidate_library_index(path))
            self.assertFalse(os.path.isfile(path))
            self.assertFalse(li.invalidate_library_index(path))

    def test_schema_bump_discards_an_old_cache(self) -> None:
        """v1 stored no version, so it could never be compared. Old caches must
        be rebuilt once instead of being trusted forever."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "entries": []}, handle)
            self.assertIsNone(li.LibraryIndex.load(path))

    def test_round_trip_preserves_the_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.json")
            index = li.LibraryIndex([self._entry("AAAA1111", "2405.00001")], "777")
            index.save(path)
            loaded = li.LibraryIndex.load(path)
            self.assertEqual(loaded.library_version, "777")


class ArchiveIndexFreshnessTest(unittest.TestCase):
    """The reported symptom, end to end: a PDF attached now must be visible."""

    def test_new_attachment_is_visible_without_waiting_for_the_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.json")
            config = MagicMock()
            config.api_url = "http://127.0.0.1:23119"
            config.user_id = "0"
            config.data_dir = tmp

            before = li.LibraryEntry(
                item_key="TEST0001", title="EventCV", item_type="preprint",
                arxiv_id="2609.21330", pdf_attachment_keys=[],
            )
            after = li.LibraryEntry(
                item_key="TEST0001", title="EventCV", item_type="preprint",
                arxiv_id="2609.21330", pdf_attachment_keys=["5JF22FK4"],
            )

            # The version probe is pinned: it reads the local SQLite database and
            # then the HTTP API, so an unpinned run returns the machine's real
            # version. This test is about the freshness rule, not the machine.
            with patch.object(
                li, "_fetch_library_offline", return_value=([before], {}, "10")
            ), patch.object(li, "current_library_version", return_value="10"):
                index = li.build_library_index(config, cache_path=path, force=True)
            self.assertFalse(index.by_arxiv("2609.21330").has_pdf)

            with patch.object(
                li, "_fetch_library_offline", return_value=([after], {}, "11")
            ), patch.object(li, "current_library_version", return_value="11"):
                index = li.build_library_index(config, cache_path=path)
            self.assertTrue(
                index.by_arxiv("2609.21330").has_pdf,
                "an attach must be visible immediately, not after the TTL",
            )


if __name__ == "__main__":
    unittest.main()

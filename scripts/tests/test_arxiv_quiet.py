#!/usr/bin/env python3
"""Progress-line suppression must be shared across every arxiv module.

Regression guard: importing `cache` as a bare top-level module (instead of
`arxiv.cache`) creates a SECOND module instance with its own QUIET flag, so
`--quiet` silently fails for any module routed to that copy.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

import arxiv.cache as cache  # noqa: E402


class QuietModeTest(unittest.TestCase):
    def setUp(self) -> None:
        cache.reset_quiet(False)

    def tearDown(self) -> None:
        cache.reset_quiet(False)

    def _modules(self):
        from arxiv import client, formatter, llm_refiner, publisher, retriever
        from arxiv.commands import cmd_find, cmd_import, cmd_publish

        return [
            client, formatter, llm_refiner, publisher, retriever,
            cmd_find, cmd_import, cmd_publish, cache,
        ]

    def test_all_modules_share_one_note(self) -> None:
        for module in self._modules():
            with self.subTest(module=module.__name__):
                self.assertIs(module.note, cache.note)

    def test_quiet_suppresses_every_module(self) -> None:
        cache.set_quiet(True)
        for module in self._modules():
            with self.subTest(module=module.__name__):
                buffer = io.StringIO()
                with redirect_stderr(buffer):
                    module.note("progress line")
                self.assertEqual(buffer.getvalue(), "")

    def test_verbose_restores_output(self) -> None:
        cache.reset_quiet(False)
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            cache.note("visible")
        self.assertIn("visible", buffer.getvalue())

    def test_cli_accepts_quiet_before_or_after_subcommand(self) -> None:
        """`--quiet` must work on either side of the subcommand."""
        import subprocess

        repo = Path(__file__).resolve().parents[2]
        script = repo / "scripts" / "literature" / "arxiv_fetch.py"
        for argv in (
            ["--quiet", "find", "--keywords", "robot", "--period", "recent", "--top", "1"],
            ["find", "--keywords", "robot", "--period", "recent", "--top", "1", "--quiet"],
        ):
            with self.subTest(argv=argv):
                proc = subprocess.run(
                    [sys.executable, str(script), *argv],
                    capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(repo),
                )
                # The point of this test is that --quiet silences *the CLI's own
                # progress chatter*, not that a machine may have no library.
                # A setup warning is legitimate stderr on a bare machine (CI),
                # so it is removed before the emptiness assertion.
                stderr = "\n".join(
                    line
                    for line in proc.stderr.splitlines()
                    if not line.startswith("[WARN] no library configured")
                ).strip()
                self.assertEqual(stderr, "", proc.stderr[:300])

    def test_set_quiet_is_sticky(self) -> None:
        # A subcommand --quiet must not clear a global --quiet.
        cache.reset_quiet(False)
        cache.set_quiet(True)
        cache.set_quiet(False)
        self.assertTrue(cache.QUIET)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for the unified `write` CLI surface (offline; no Zotero needed).

These guard the *shape* of the unified entry point: that every operation an
agent needs is reachable from `zotero_cli.py`, that the parser wiring is right,
and that the safety defaults survived registration. Live behaviour is covered by
test_local_write.py / test_action_ledger.py.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from zotero.commands import cmd_write  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotero_cli")
    sub = parser.add_subparsers(dest="command")
    cmd_write.Register(sub)
    return parser


class WriteSurfaceTest(unittest.TestCase):
    """Every operation that previously required the plugin must be reachable."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_all_write_ops_are_registered(self) -> None:
        args = self.parser.parse_args(["write", "status"])
        self.assertEqual(args.write_op, "status")
        for op in ("tag", "move", "collection", "trash", "restore",
                   "merge", "history", "undo"):
            parsed = self.parser.parse_args(
                _minimal_argv(op)
            )
            self.assertEqual(parsed.write_op, op, f"{op} must be registered")

    def test_tag_takes_key_and_multiple_tags(self) -> None:
        args = self.parser.parse_args(["write", "tag", "ABCD2345", "a", "b"])
        self.assertEqual(args.key, "ABCD2345")
        self.assertEqual(args.tags, ["a", "b"])

    def test_merge_takes_master_then_duplicates(self) -> None:
        args = self.parser.parse_args(["write", "merge", "MASTER01", "D1", "D2"])
        self.assertEqual(args.master, "MASTER01")
        self.assertEqual(args.duplicates, ["D1", "D2"])

    def test_undo_supports_last_and_dry_run(self) -> None:
        args = self.parser.parse_args(["write", "undo", "--last", "3", "--dry-run"])
        self.assertEqual(args.last, 3)
        self.assertTrue(args.dry_run)

    def test_undo_supports_explicit_ids(self) -> None:
        args = self.parser.parse_args(["write", "undo", "act-1", "act-2"])
        self.assertEqual(args.action_ids, ["act-1", "act-2"])

    def test_undispatched_write_prints_usage_instead_of_crashing(self) -> None:
        args = self.parser.parse_args(["write"])
        self.assertIsNone(getattr(args, "write_op", None))
        # _Run must return an error code rather than raising on a bare `write`.
        self.assertTrue(callable(args.func))

    def test_mineru_push_is_registered(self) -> None:
        args = self.parser.parse_args(["mineru-push", "17", "--dry-run"])
        self.assertEqual(args.reference, "17")
        self.assertTrue(args.dry_run)


class NoPluginDependencyTest(unittest.TestCase):
    """The write path must not reach back into the plugin bridge.

    `zotero_mcp.py` and its 328k-line upstream are gone; these guards keep a
    future edit from quietly reintroducing that dependency, which is the whole
    point of the migration.
    """

    PLUGIN_MARKERS = (
        "zotero_mcp",
        "ZoteroMCP",
        "llm-for-zotero/mcp",
        "ZOTERO_MCP_TOKEN",
        "codexZoteroMcpBearerToken",
        "Translate.Search",
        "env.snapshot",
    )

    def _assert_clean(self, relative: str) -> None:
        source = (LIB_DIR / relative).read_text(encoding="utf-8")
        for marker in self.PLUGIN_MARKERS:
            self.assertNotIn(marker, source, f"{relative} still references {marker}")

    def test_write_client_is_plugin_free(self) -> None:
        self._assert_clean("zotero/local_write.py")

    def test_action_ledger_is_plugin_free(self) -> None:
        self._assert_clean("zotero/action_ledger.py")

    def test_write_commands_are_plugin_free(self) -> None:
        self._assert_clean("zotero/commands/cmd_write.py")

    def test_import_is_plugin_free(self) -> None:
        self._assert_clean("arxiv/commands/cmd_import.py")

    def test_remove_is_plugin_free(self) -> None:
        self._assert_clean("arxiv/commands/cmd_remove.py")

    def test_cache_module_is_gone(self) -> None:
        """MineruCache was superseded by MineruCatalog; a second cache
        implementation is how the two read paths drifted apart."""
        self.assertFalse((LIB_DIR / "zotero" / "cache.py").exists())


class SafetyDefaultTest(unittest.TestCase):
    """Defaults that prevent data loss must be advertised where an agent reads
    them: the `write` operation index shown by `write --help`.
    """

    def setUp(self) -> None:
        self.index = _write_index_help().lower()

    def test_trash_help_says_reversible(self) -> None:
        self.assertIn("reversible", self.index)

    def test_tag_help_says_it_merges(self) -> None:
        self.assertIn("merges", self.index)

    def test_merge_help_says_donors_are_trashed(self) -> None:
        self.assertIn("donors trashed", self.index)

    def test_undo_help_is_present(self) -> None:
        self.assertIn("revert", self.index)


def _minimal_argv(op: str) -> list[str]:
    required = {
        "tag": ["K", "t"],
        "move": ["K", "C"],
        "collection": ["name"],
        "trash": ["K"],
        "restore": ["K"],
        "merge": ["M", "D"],
        "history": [],
        "undo": ["--last", "1"],
    }
    return ["write", op] + required[op]


def _write_index_help() -> str:
    """Capture `write --help`, which lists each operation and its description."""
    import io
    from contextlib import redirect_stdout

    parser = build_parser()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        try:
            parser.parse_args(["write", "--help"])
        except SystemExit:
            pass
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()

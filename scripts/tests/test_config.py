#!/usr/bin/env python3
"""Tests for configuration resolution.

`paths.local.yaml` is an *override* file: values it omits must be discovered.
These tests pin that inversion, because the failure mode is silent — a config
loader that quietly returns an empty string makes every consumer fail somewhere
far away from the cause.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from zotero import config as cfg_mod  # noqa: E402
from zotero.config import ZoteroConfig, _parse_yaml_flat, _pick, explain  # noqa: E402


def _isolated_module():
    """Neutralise every probe so a test controls each resolution layer."""
    return (
        patch.object(cfg_mod, "_zotero_data_dir", return_value=None),
        patch.object(cfg_mod, "_api_reachable", return_value=True),
        patch.object(cfg_mod, "_discover_inbox_key", return_value=None),
        patch.object(cfg_mod, "_discover_vault", return_value=(None, None, {}, [])),
    )


class ParseYamlTest(unittest.TestCase):
    def test_flat_two_level(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("obsidian:\n  vault: ~/V\nzotero:\n  user_id: '42'\n")
            path = handle.name
        try:
            parsed = _parse_yaml_flat(path)
        finally:
            os.unlink(path)
        self.assertEqual(parsed["obsidian.vault"], "~/V")
        self.assertEqual(parsed["zotero.user_id"], "42")

    def test_comments_and_blanks_ignored(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("# a comment\n\nobsidian:\n  # inline\n  vault: ~/V\n")
            path = handle.name
        try:
            parsed = _parse_yaml_flat(path)
        finally:
            os.unlink(path)
        self.assertEqual(parsed, {"obsidian.vault": "~/V"})

    def test_missing_file_is_empty_not_an_error(self) -> None:
        self.assertEqual(_parse_yaml_flat("/nonexistent/x.yaml"), {})


class PickTest(unittest.TestCase):
    """The precedence chain is the whole contract: yaml > env > discover > default."""

    def test_yaml_wins(self) -> None:
        sources: dict = {}
        value = _pick({"k": "from-yaml"}, "k", "SOME_ENV", lambda: "from-probe",
                      "default", sources)
        self.assertEqual(value, "from-yaml")
        self.assertEqual(sources["k"], "yaml")

    def test_env_wins_over_probe(self) -> None:
        sources: dict = {}
        with patch.dict(os.environ, {"SOME_ENV": "from-env"}):
            value = _pick({}, "k", "SOME_ENV", lambda: "from-probe", "default", sources)
        self.assertEqual(value, "from-env")
        self.assertEqual(sources["k"], "env:SOME_ENV")

    def test_probe_wins_over_default(self) -> None:
        sources: dict = {}
        value = _pick({}, "k", None, lambda: "from-probe", "default", sources)
        self.assertEqual(value, "from-probe")
        self.assertEqual(sources["k"], "discovered")

    def test_default_used_last(self) -> None:
        sources: dict = {}
        value = _pick({}, "k", None, lambda: None, "default", sources)
        self.assertEqual(value, "default")
        self.assertEqual(sources["k"], "default")

    def test_missing_when_nothing_resolves(self) -> None:
        sources: dict = {}
        self.assertIsNone(_pick({}, "k", None, None, None, sources))
        self.assertEqual(sources["k"], "missing")


class ApiReachableTest(unittest.TestCase):
    """A 404 from the server still proves the port is live. Probing `/` used to
    report a running Zotero as down, because HTTPError subclasses URLError."""

    def test_http_error_counts_as_reachable(self) -> None:
        import urllib.error

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError("u", 404, "nf", {}, None)):
            self.assertTrue(cfg_mod._api_reachable("http://127.0.0.1:23119"))

    def test_connection_error_is_unreachable(self) -> None:
        import urllib.error

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("refused")):
            self.assertFalse(cfg_mod._api_reachable("http://127.0.0.1:9"))

    def test_probes_the_api_path_not_the_root(self) -> None:
        seen: list[str] = []

        class Ok:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def spy(request, *a, **kw):
            seen.append(request.full_url if hasattr(request, "full_url") else str(request))
            return Ok()

        with patch("urllib.request.urlopen", side_effect=spy):
            cfg_mod._api_reachable("http://127.0.0.1:23119")
        self.assertTrue(seen and seen[0].endswith("/api/"), seen)


class LoadConfigDiscoveryTest(unittest.TestCase):
    """With no YAML at all, discoverable values must still resolve."""

    def test_discovers_without_any_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Vault"
            (vault / "02-Literature" / "ZoteroNotes").mkdir(parents=True)
            (vault / "00-Inbox").mkdir()
            (vault / "03-Concepts").mkdir()
            data_dir = Path(tmp) / "Zotero"
            (data_dir / cfg_mod.MINERU_SUBDIR).mkdir(parents=True)

            mocks = _isolated_module()
            with mocks[0], mocks[1], mocks[2]:
                with patch.object(
                    cfg_mod, "_discover_vault",
                    return_value=(str(vault),
                                  str(vault / "02-Literature" / "ZoteroNotes"),
                                  {"vault": str(vault),
                                   "literature_notes": str(vault / "02-Literature" / "ZoteroNotes")},
                                  []),
                ):
                    with patch.object(cfg_mod, "_zotero_data_dir", return_value=str(data_dir)):
                        with patch.object(cfg_mod, "_discover_inbox_key", return_value="INBOX123"):
                            config = cfg_mod.load_config(str(Path(tmp) / "absent.yaml"))

        self.assertEqual(config.inbox_collection_key, "INBOX123")
        self.assertEqual(config.mineru_cache, str(data_dir / cfg_mod.MINERU_SUBDIR))
        self.assertEqual(config.vault, str(vault))
        self.assertEqual(config.user_id, "0")
        self.assertEqual(config.api_url, cfg_mod.DEFAULT_API)
        self.assertEqual(config.sources["zotero.inbox_collection_key"], "discovered")
        self.assertEqual(config.sources["zotero.user_id"], "default")

    def test_tied_vaults_warn_instead_of_guessing(self) -> None:
        """Notes are written to the vault path, so an unresolved tie must be
        surfaced rather than silently picked."""
        import io
        from contextlib import redirect_stderr

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Vault"
            (vault / "02-Literature" / "ZoteroNotes").mkdir(parents=True)
            data_dir = Path(tmp) / "Zotero"
            (data_dir / cfg_mod.MINERU_SUBDIR).mkdir(parents=True)

            buffer = io.StringIO()
            with patch.object(cfg_mod, "_api_reachable", return_value=True), \
                 patch.object(cfg_mod, "_discover_inbox_key", return_value="K"), \
                 patch.object(cfg_mod, "_zotero_data_dir", return_value=str(data_dir)), \
                 patch.object(
                     cfg_mod, "_discover_vault",
                     return_value=(str(vault),
                                   str(vault / "02-Literature" / "ZoteroNotes"),
                                   {"literature_notes": str(vault / "02-Literature" / "ZoteroNotes")},
                                   ["/other/Vault"]),
                 ):
                with redirect_stderr(buffer):
                    cfg_mod.load_config(str(Path(tmp) / "absent.yaml"))
        self.assertIn("match equally", buffer.getvalue())
        self.assertIn("/other/Vault", buffer.getvalue())

    def test_yaml_override_suppresses_the_vault_warning(self) -> None:
        import io
        from contextlib import redirect_stderr

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Vault"
            (vault / "02-Literature" / "ZoteroNotes").mkdir(parents=True)
            data_dir = Path(tmp) / "Zotero"
            (data_dir / cfg_mod.MINERU_SUBDIR).mkdir(parents=True)
            yaml_path = Path(tmp) / "p.yaml"
            yaml_path.write_text(f"obsidian:\n  vault: {vault}\n")

            buffer = io.StringIO()
            with patch.object(cfg_mod, "_api_reachable", return_value=True), \
                 patch.object(cfg_mod, "_discover_inbox_key", return_value="K"), \
                 patch.object(cfg_mod, "_zotero_data_dir", return_value=str(data_dir)), \
                 patch.object(
                     cfg_mod, "_discover_vault",
                     return_value=(str(vault),
                                   str(vault / "02-Literature" / "ZoteroNotes"),
                                   {"literature_notes": str(vault / "02-Literature" / "ZoteroNotes")},
                                   ["/other/Vault"]),
                 ):
                with redirect_stderr(buffer):
                    cfg_mod.load_config(str(yaml_path))
        self.assertNotIn("match equally", buffer.getvalue())

    def test_unresolvable_vault_exits_with_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "Zotero"
            (data_dir / cfg_mod.MINERU_SUBDIR).mkdir(parents=True)
            import io
            from contextlib import redirect_stderr

            buffer = io.StringIO()
            with patch.object(cfg_mod, "_api_reachable", return_value=True), \
                 patch.object(cfg_mod, "_discover_inbox_key", return_value="K"), \
                 patch.object(cfg_mod, "_zotero_data_dir", return_value=str(data_dir)), \
                 patch.object(cfg_mod, "_discover_vault", return_value=(None, None, {}, [])):
                with redirect_stderr(buffer):
                    with self.assertRaises(SystemExit):
                        cfg_mod.load_config(str(Path(tmp) / "absent.yaml"))
        message = buffer.getvalue()
        self.assertIn("obsidian.vault", message)
        self.assertIn("paths.local.yaml", message)


class ExplainTest(unittest.TestCase):
    def test_reports_every_value_with_its_source(self) -> None:
        config = ZoteroConfig(
            api_url="http://x", user_id="0", inbox_collection_key="K",
            mineru_cache="/tmp/m", literature_notes="/tmp/n", vault="/tmp/v",
            sources={"zotero.user_id": "default"},
        )
        text = explain(config)
        self.assertIn("zotero.user_id", text)
        self.assertIn("from default", text)


if __name__ == "__main__":
    unittest.main()

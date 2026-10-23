#!/usr/bin/env python3
"""A missing library must degrade, not break, and must not read as "broken".

The README promises the repository works without Zotero, Obsidian or MinerU.
CI keeps that promise the strong way: its Linux and Windows jobs run the entire
suite on a machine that has none of them. This file covers the part that is
cheap to assert locally — that a diagnostic reports the difference correctly,
and that nothing in the literature path raises SystemExit out of a probe.

An earlier version of this file spawned a nested pytest run to check the same
thing. That recursed (the spawned suite collected this file), so the property is
left to CI where it is both cheaper and stronger.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

BARE_ENV = {
    "ZOTERO_LOCAL_API": "http://127.0.0.1:29999",
    "ZOTERO_DATA_DIR": "/nonexistent-zotero-data",
    "OBSIDIAN_VAULT": "/nonexistent-vault",
    "OBSIDIAN_LITERATURE_NOTES": "/nonexistent-notes",
    "PAPER_WORKFLOW_CONFIG": "/nonexistent/paths.local.yaml",
}


def bare_env() -> dict:
    env = dict(os.environ)
    env.update(BARE_ENV)
    env.pop("ZOTERO_INBOX_KEY", None)
    env.pop("ZOTERO_USER_ID", None)
    return env


class BareMachineTest(unittest.TestCase):
    def test_doctor_reports_literature_off_but_core_intact(self) -> None:
        """No library is a valid install: core stays ok, literature goes off."""
        result = subprocess.run(
            [sys.executable, "scripts/deploy/doctor.py", "--json"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
            env=bare_env(),
        )
        payload = json.loads(result.stdout)
        by_name = {c["name"]: c for c in payload["checks"]}

        self.assertIn(by_name["zotero"]["status"], ("off", "partial"))
        self.assertIn(by_name["obsidian"]["status"], ("off", "partial"))

        core_broken = any(
            c["status"] == "broken" for c in payload["checks"] if c["required"]
        )
        self.assertEqual(payload["core_ok"], not core_broken)

        # On a supported interpreter with PyYAML, core must be genuinely fine.
        if sys.version_info >= (3, 10) and by_name["pyyaml"]["status"] == "ready":
            self.assertTrue(
                payload["core_ok"],
                "core reported broken only because a library is absent",
            )

    def test_preflight_answers_instead_of_exiting(self) -> None:
        """paper-status must still classify, even with no library to read."""
        result = subprocess.run(
            [
                sys.executable, "scripts/literature/zotero_cli.py",
                "paper-status", "2501.99999",
            ],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
            env=bare_env(),
        )
        # Either it classifies, or it fails with guidance — never a traceback.
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(
            "STATE=" in result.stdout or "[ERROR]" in result.stderr,
            f"unexpected output:\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()

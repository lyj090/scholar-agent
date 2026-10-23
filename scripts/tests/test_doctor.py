#!/usr/bin/env python3
"""doctor must be honest about a healthy machine and about a bare one.

The failure mode worth guarding: doctor reporting "healthy" because it only
checked things that happened to be present, or reporting "broken" because the
literature flow is not configured — which is a perfectly valid install.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTOR = REPO_ROOT / "scripts" / "deploy" / "doctor.py"


def run_doctor(*args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PAPER_WORKFLOW_CONFIG", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(DOCTOR), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        env=env,
    )


class DoctorOutputTest(unittest.TestCase):
    def test_emits_every_check(self) -> None:
        result = run_doctor()
        self.assertIn(result.returncode, (0, 1), result.stderr)
        for name in (
            "python", "pyyaml", "skills", "tests",
            "zotero", "mineru", "obsidian", "agents", "network",
        ):
            self.assertIn(name, result.stdout, f"missing check: {name}")

    def test_json_is_parseable_and_complete(self) -> None:
        result = run_doctor("--json")
        payload = json.loads(result.stdout)
        self.assertIn("core_ok", payload)
        self.assertIn("checks", payload)
        names = {c["name"] for c in payload["checks"]}
        self.assertIn("python", names)
        for check in payload["checks"]:
            self.assertIn(check["status"], ("ready", "partial", "off", "broken"))
            self.assertIn("required", check)

    def test_writes_nothing(self) -> None:
        """doctor is diagnostic; it must not create config or deploy links."""
        before = sorted(p.name for p in (REPO_ROOT / "shared").iterdir())
        run_doctor()
        after = sorted(p.name for p in (REPO_ROOT / "shared").iterdir())
        self.assertEqual(before, after)

    def test_reports_python_version_requirement(self) -> None:
        result = run_doctor("--json")
        payload = json.loads(result.stdout)
        py = next(c for c in payload["checks"] if c["name"] == "python")
        major, minor = sys.version_info[:2]
        expected = "ready" if (major, minor) >= (3, 10) else "broken"
        self.assertEqual(py["status"], expected)


class UnconfiguredMachineTest(unittest.TestCase):
    """A machine with no Zotero and no vault is a valid install."""

    def test_literature_off_does_not_mean_core_broken(self) -> None:
        env = {
            "ZOTERO_LOCAL_API": "http://127.0.0.1:29999",
            "ZOTERO_DATA_DIR": "/nonexistent-zotero",
            "OBSIDIAN_VAULT": "/nonexistent-vault",
            "PAPER_WORKFLOW_CONFIG": "/nonexistent/paths.local.yaml",
        }
        result = run_doctor("--json", extra_env=env)
        payload = json.loads(result.stdout)
        by_name = {c["name"]: c for c in payload["checks"]}

        # Literature degrades, and says so without claiming the install is broken.
        self.assertIn(by_name["zotero"]["status"], ("off", "partial"))
        self.assertIn(by_name["obsidian"]["status"], ("off", "partial"))

        # Core health depends only on the required checks.
        core_broken = any(
            c["status"] == "broken" for c in payload["checks"] if c["required"]
        )
        self.assertEqual(payload["core_ok"], not core_broken)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts/agent-audit.py"


class AgentAuditTest(unittest.TestCase):
    def run_audit(self, root: Path, report: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
        args = [sys.executable, str(AUDIT), "--root", str(root), "--report", str(report)]
        if check:
            args.append("--check")
        return subprocess.run(args, text=True, capture_output=True, check=False)

    def test_reports_duplicates_and_clean_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "AGENTS.md").write_text("# Rules\n\nSee [guide](guide.md).\n", encoding="utf-8")
            (root / "CLAUDE.md").write_text("# Rules\n\nSee [guide](guide.md).\n", encoding="utf-8")
            (root / "guide.md").write_text("guide\n", encoding="utf-8")
            report = root / "report.md"

            result = self.run_audit(root, report, check=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DUPLICATES=1", result.stdout)
            self.assertIn("BROKEN_LINKS=0", result.stdout)
            self.assertIn("ABSOLUTE_REFS=0", result.stdout)
            self.assertTrue(report.exists())

    def test_check_fails_for_missing_or_absolute_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text(
                "# Rules\n\n[missing](nope.md)\n\nSee [local](file:///home/example/rules.md).\n",
                encoding="utf-8",
            )
            report = root / "report.md"

            result = self.run_audit(root, report, check=True)

            self.assertEqual(result.returncode, 1)
            self.assertIn("BROKEN_LINKS=1", result.stdout)
            self.assertIn("ABSOLUTE_REFS=2", result.stdout)

    def test_detects_exact_and_near_duplicate_headings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "AGENTS.md").write_text(
                "# Rules\n\n## Reading order\n\na\n\n## Reading order\n\nb\n",
                encoding="utf-8",
            )
            report = root / "report.md"

            result = self.run_audit(root, report, check=True)

            self.assertEqual(result.returncode, 1)
            self.assertIn("DUP_HEADINGS=1", result.stdout)
            self.assertIn("duplicated heading", report.read_text(encoding="utf-8"))

    def test_near_duplicate_heading_with_qualifier_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "AGENTS.md").write_text(
                "# Rules\n\n## 先读什么\n\na\n\n## 先读什么（省 token）\n\nb\n",
                encoding="utf-8",
            )
            report = root / "report.md"

            result = self.run_audit(root, report, check=True)

            self.assertEqual(result.returncode, 1)
            self.assertIn("DUP_HEADINGS=1", result.stdout)
            self.assertIn(
                "near-duplicated heading", report.read_text(encoding="utf-8")
            )

    def test_dead_instruction_name_fails_check(self) -> None:
        """A misnamed rule file must fail loudly.

        `AGENT.md` is read by no runtime, so its rules never load. Reporting it
        as a normal file was the original defect: the file looked maintained
        while every session ran without it.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "AGENT.md").write_text("# Rules\n\nAll clean.\n", encoding="utf-8")
            report = root / "report.md"

            result = self.run_audit(root, report, check=True)

            self.assertEqual(result.returncode, 1)
            self.assertIn("DEAD_NAMES=1", result.stdout)
            self.assertIn(
                "never auto-loaded", report.read_text(encoding="utf-8")
            )

    def test_live_instruction_names_are_not_dead(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            for name in ("AGENTS.md", "CLAUDE.md", "AGENTS.local.md"):
                (root / name).write_text(f"# {name}\n\nDistinct.\n", encoding="utf-8")
            report = root / "report.md"

            result = self.run_audit(root, report, check=True)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("DEAD_NAMES=0", result.stdout)

    def test_machine_local_absolute_paths_are_not_flagged(self) -> None:
        """The deployed global baseline intentionally hardcodes the machine path.

        `deploy.sh` rewrites `~/.zshrc` into the real shell rc for this host, so
        flagging it would report the generator working as designed. The portable
        template is a repo file and is still checked.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude = root / ".claude"
            claude.mkdir()
            (claude / "CLAUDE.md").write_text(
                "# Global\n\nConfig: `/home/example/.zshrc`\n", encoding="utf-8"
            )
            report = root / "report.md"

            result = self.run_audit(root, report, check=True)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("ABSOLUTE_REFS=0", result.stdout)

    def test_vendor_findings_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vendor = root / "node_modules" / "pkg"
            vendor.mkdir(parents=True)
            (vendor / "AGENTS.md").write_text(
                "# Vendor\n\n[gone](nope.md)\n", encoding="utf-8"
            )
            report = root / "report.md"

            result = self.run_audit(root, report, check=True)

            # Vendor breakage must not fail the build, but must still be counted.
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("BROKEN_LINKS=0", result.stdout)
            self.assertIn("Vendored copies", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

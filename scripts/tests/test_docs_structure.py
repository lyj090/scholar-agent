#!/usr/bin/env python3
"""Documentation links must resolve, and the public build must exclude the log.

After the docs were consolidated (three overlapping files became one reference
plus a development log), stale cross-references were the obvious failure mode:
an internal link that points at a renamed file renders as a dead end, and
`docs/devlog/` must never reach the public tree.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# GitHub resolves `../../` from the file's directory to the repository root,
# so these are valid on the web but have no local file to point at.
WEB_ONLY = ("../../security/advisories/new",)


def markdown_files() -> list[Path]:
    out = []
    for path in REPO_ROOT.rglob("*.md"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(part in rel for part in (
            ".git/", "node_modules/", "__pycache__/", ".ai/",
            "claude-skills/", "codex-skills/", "cursor-skills/",
            "grok-skills/", "dsh-skills/",
        )):
            continue
        out.append(path)
    return out


class InternalLinkTest(unittest.TestCase):
    def test_every_relative_link_resolves(self) -> None:
        broken: list[str] = []
        for path in markdown_files():
            text = path.read_text(errors="ignore")
            for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
                target = match.group(1)
                if target.startswith(("http://", "https://", "mailto:", "#", "zotero:")):
                    continue
                if target in WEB_ONLY:
                    continue
                rel = target.split("#")[0]
                if not rel:
                    continue
                if not (path.parent / rel).resolve().exists():
                    broken.append(f"{path.relative_to(REPO_ROOT)}: {target}")
        self.assertEqual(broken, [], f"broken internal links: {broken}")

    def test_no_link_to_merged_documents(self) -> None:
        """The three superseded files must not be *linked* from anywhere.

        Naming them is fine — the changelog has to say what was merged. A dead
        hyperlink is the failure; prose mention is not.
        """
        gone = (
            "pipeline-overview.md",
            "unified-agent-entry.md",
            "local-first-extraction.md",
        )
        hits: list[str] = []
        for path in markdown_files():
            text = path.read_text(errors="ignore")
            for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
                target = match.group(1)
                if any(name in target for name in gone):
                    hits.append(f"{path.relative_to(REPO_ROOT)}: {target}")
        self.assertEqual(hits, [], f"links to merged documents: {hits}")


class DevlogTest(unittest.TestCase):
    def test_devlog_is_documented_when_present(self) -> None:
        """In the public tree `docs/devlog/` is absent by design.

        These checks only apply where the log exists: skipping (rather than
        failing) is what makes the same suite meaningful on both branches.
        """
        devlog = REPO_ROOT / "docs" / "devlog"
        if not devlog.is_dir():
            self.skipTest("docs/devlog is excluded from the public tree")
        self.assertTrue((devlog / "README.md").is_file())
        logs = [p for p in devlog.glob("*.md") if p.name != "README.md"]
        self.assertGreaterEqual(len(logs), 2, "expected the extraction and CLI logs")
        index = (devlog / "README.md").read_text(encoding='utf-8')
        for log in logs:
            self.assertIn(log.name, index, f"{log.name} is not listed in the index")

    def test_build_script_excludes_the_devlog(self) -> None:
        script = (REPO_ROOT / "scripts" / "deploy" / "build-public.sh").read_text(encoding='utf-8')
        self.assertIn('"docs/devlog"', script,
                      "build-public.sh must exclude docs/devlog from the public tree")
        self.assertIn('".ai"', script)


class PublicTreeTest(unittest.TestCase):
    """The current public branch must not contain excluded paths."""

    def test_public_branch_has_no_devlog(self) -> None:
        import subprocess

        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "public"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if result.returncode != 0:
            self.skipTest("public branch not present")
        files = result.stdout.split()
        offenders = [f for f in files if f.startswith(("docs/devlog/", ".ai/"))]
        self.assertEqual(offenders, [], f"excluded paths in public: {offenders}")

    def test_public_branch_has_no_local_config(self) -> None:
        import subprocess

        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "public"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if result.returncode != 0:
            self.skipTest("public branch not present")
        offenders = [
            f for f in result.stdout.split()
            if f.endswith((".local.yaml", ".local.json", ".log")) or f == ".zotero_env"
        ]
        self.assertEqual(offenders, [], f"machine-local files in public: {offenders}")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""The two READMEs must stay in step, and their commands must run.

A bilingual README rots in a specific way: the English one is updated, the
Chinese one is not, and six months later they describe different projects. These
checks are deliberately structural — section parity, language switcher, and the
commands actually existing — not a diff of the prose, which is expected to
differ.
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EN = REPO_ROOT / "README.md"
ZH = REPO_ROOT / "README.zh-CN.md"


def headings(path: Path) -> list[str]:
    """Top-level `## ` headings, ignoring anything inside a fenced block."""
    lines = path.read_text(encoding='utf-8').splitlines()
    out: list[str] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## "):
            out.append(line[3:].strip())
    return out


def code_blocks(path: Path) -> list[str]:
    """Contents of fenced blocks tagged ```console or ```bash."""
    text = path.read_text(encoding='utf-8')
    return re.findall(r"```(?:console|bash)\n(.*?)```", text, re.DOTALL)


class BilingualParityTest(unittest.TestCase):
    def test_both_readmes_exist(self) -> None:
        self.assertTrue(EN.is_file())
        self.assertTrue(ZH.is_file())

    def test_same_number_of_sections(self) -> None:
        en, zh = headings(EN), headings(ZH)
        self.assertEqual(
            len(en),
            len(zh),
            f"section count drifted: EN={en}\nZH={zh}",
        )

    def test_language_switcher_links_both_ways(self) -> None:
        en_text, zh_text = EN.read_text(encoding='utf-8'), ZH.read_text(encoding='utf-8')
        self.assertIn("README.zh-CN.md", en_text)
        self.assertIn("README.md", zh_text)

    def test_chinese_readme_is_actually_chinese(self) -> None:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", ZH.read_text(encoding='utf-8')))
        self.assertGreater(cjk, 800, "zh-CN README looks mostly untranslated")

    def test_english_readme_has_little_chinese(self) -> None:
        """Some Chinese is fine (verbatim CLI output); prose must not be."""
        text = EN.read_text(encoding='utf-8')
        prose = "".join(code_blocks(EN))
        cjk_prose = len(re.findall(r"[\u4e00-\u9fff]", text.replace(prose, "")))
        self.assertLess(cjk_prose, 60, "English README has Chinese outside code blocks")


class ReadmeCommandsExistTest(unittest.TestCase):
    """Every repository path a README tells the reader to run must exist."""

    def _referenced_paths(self, text: str) -> set[str]:
        found: set[str] = set()
        for match in re.finditer(
            r"(?:^|\s)((?:bash|python3|\./)\s+)?((?:scripts|examples|shared|docs)/[\w./-]+)",
            text,
        ):
            found.add(match.group(2))
        return found

    def test_scripts_and_examples_referenced_exist(self) -> None:
        missing: list[str] = []
        for readme in (EN, ZH):
            for path in self._referenced_paths(readme.read_text(encoding='utf-8')):
                candidate = path.rstrip(".,)`")
                # Machine-local config is created by the reader, not shipped.
                if candidate.startswith("shared/paths.local"):
                    continue
                target = REPO_ROOT / candidate
                if not target.exists():
                    missing.append(f"{readme.name}: {candidate}")
        self.assertEqual(missing, [], f"README references missing paths: {missing}")

    def test_readme_does_not_use_a_bare_agent_sh(self) -> None:
        """`./agent.sh` does not exist at the root; it lives under scripts/."""
        for readme in (EN, ZH):
            text = readme.read_text(encoding='utf-8')
            self.assertNotIn(
                "./agent.sh",
                text,
                f"{readme.name} tells the reader to run ./agent.sh, which is absent",
            )


class DeploymentClaimTest(unittest.TestCase):
    def test_deploy_sh_exists_and_is_executable(self) -> None:
        deploy = REPO_ROOT / "deploy.sh"
        self.assertTrue(deploy.is_file())
        if os.name == "nt":
            # NTFS has no POSIX executable bit: the mode is meaningless there and
            # git reports 100644 on checkout. The Linux jobs assert it for real.
            self.skipTest("executable bit is not meaningful on Windows")
        self.assertTrue(deploy.stat().st_mode & 0o111)

    def test_agent_sh_list_runs(self) -> None:
        if os.name == "nt":
            # The deployment entry points are POSIX shell. Windows is supported
            # for the Python code and the CLI, not for this script.
            self.skipTest("bash-based deployment is not a Windows target")
        result = subprocess.run(
            ["bash", "scripts/agent.sh", "list"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("core", result.stdout)
        self.assertIn("literature", result.stdout)


if __name__ == "__main__":
    unittest.main()

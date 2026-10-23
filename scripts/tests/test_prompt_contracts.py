"""Regression tests for the prompt/skill audit.

Every case here corresponds to a defect that shipped: a gate that passed on a
broken config, a repair hint that produced a false pass, an unquoted placeholder
that truncated a file, or a hook that deleted the user's own.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK_PATHS = ROOT / "scripts" / "deploy" / "check_paths.py"
SKILL_PATHS = ROOT / "shared" / "config" / "skill-paths.yaml"
GATE = ROOT / "scripts" / "skills" / "skill-gate.sh"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=ROOT, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, **kw,
    )


class SkillRequirementMap(unittest.TestCase):
    """The per-skill gate must read a tracked file, not the gitignored override."""

    def test_map_is_tracked(self) -> None:
        self.assertTrue(SKILL_PATHS.is_file(), "skill-paths.yaml must be tracked")

    def test_every_skill_is_declared(self) -> None:
        text = SKILL_PATHS.read_text(encoding="utf-8")
        for meta in sorted((ROOT / "skills").glob("*/meta.yaml")):
            name = meta.parent.name
            self.assertRegex(
                text,
                rf"(?m)^{re.escape(name)}:",
                f"{name} is missing from skill-paths.yaml; an undeclared skill "
                f"cannot be distinguished from a typo",
            )

    def test_declared_skills_exist(self) -> None:
        sys.path.insert(0, str(CHECK_PATHS.parent))
        import check_paths  # noqa: PLC0415

        for name in check_paths._load_skill_requirements():
            self.assertTrue(
                (ROOT / "skills" / name).is_dir(),
                f"skill-paths.yaml declares {name}, which does not exist",
            )

    def test_broken_vault_is_rejected(self) -> None:
        """A vault that does not exist used to report OK for every skill."""
        if os.name == "nt":
            self.skipTest("the gate is a POSIX shell script")
        local = ROOT / "shared" / "paths.local.yaml"
        backup = local.read_bytes() if local.exists() else None
        try:
            local.write_text(
                "obsidian:\n  vault: /nonexistent/broken/vault\n", encoding="utf-8"
            )
            proc = run([sys.executable, str(CHECK_PATHS), "--skill",
                        "zotero-paper-to-obsidian", "--compact"])
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("paths_not_ready", proc.stdout)
        finally:
            if backup is None:
                local.unlink(missing_ok=True)
            else:
                local.write_bytes(backup)

    def test_skill_without_requirements_needs_no_config(self) -> None:
        """paper-lookup runs on a bare machine: a missing config is not an error."""
        if os.name == "nt":
            self.skipTest("the gate is a POSIX shell script")
        local = ROOT / "shared" / "paths.local.yaml"
        backup = local.read_bytes() if local.exists() else None
        try:
            local.unlink(missing_ok=True)
            proc = run([sys.executable, str(CHECK_PATHS), "--skill",
                        "paper-lookup", "--compact"])
            self.assertEqual(proc.returncode, 0, proc.stdout)
        finally:
            if backup is not None:
                local.write_bytes(backup)

    def test_absent_override_uses_discovery(self) -> None:
        """paths.local.yaml is optional: discovery alone must satisfy a skill.

        Requiring the file made a machine whose vault and Zotero were both
        auto-discovered fail every literature skill, while an empty file —
        which carries strictly less information — passed. The condition was
        presence, not readiness.
        """
        if os.name == "nt":
            self.skipTest("the gate is a POSIX shell script")
        local = ROOT / "shared" / "paths.local.yaml"
        backup = local.read_bytes() if local.exists() else None
        try:
            local.unlink(missing_ok=True)
            # Point HOME at a scratch dir with a discoverable vault, then confirm
            # the check consults the resolver rather than the file.
            home = Path(tempfile.mkdtemp())
            vault = home / "Documents" / "Obsidian Vault"
            (vault / "02-Literature" / "ZoteroNotes").mkdir(parents=True)
            # Discovery accepts a marker file *or* the 00-Inbox + 02-Literature
            # structure (paths_common.discover_vaults); a bare tree is not a vault.
            (vault / "00-Inbox").mkdir()
            (vault / "00-Vault-Entry.md").write_text("# vault\n", encoding="utf-8")
            # load_config() exits when it cannot locate a Zotero data dir, and
            # _merge_discovered treats that as "nothing to add". A fixture with
            # a vault but no Zotero would therefore prove nothing.
            zotero = home / "Zotero"
            (zotero / "llm-for-zotero-mineru").mkdir(parents=True)
            (zotero / "zotero.sqlite").write_bytes(b"")
            env = dict(os.environ, HOME=str(home))
            proc = run([sys.executable, str(CHECK_PATHS), "--check-key",
                        "obsidian.vault", "--quiet"], env=env)
            self.assertEqual(
                proc.returncode, 0,
                "a discoverable vault was reported missing only because the "
                "optional override file does not exist",
            )
        finally:
            shutil.rmtree(home, ignore_errors=True)
            if backup is not None:
                local.write_bytes(backup)

    def test_undeclared_skill_errors(self) -> None:
        proc = run([sys.executable, str(CHECK_PATHS), "--skill",
                    "not-a-real-skill", "--compact"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("skill_undeclared", proc.stdout)


class GateOutputIsCopyPasteable(unittest.TestCase):
    """Placeholders must be quoted: an unquoted `<REF>` is a shell redirect."""

    PLACEHOLDER = re.compile(r"(?<![\"'\w])<([A-Za-z][\w-]*)>")

    def test_no_bare_placeholders(self) -> None:
        if os.name == "nt":
            self.skipTest("the gate is a POSIX shell script")
        proc = run(["bash", str(GATE), "paper-lookup"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for line in proc.stdout.splitlines():
            if not line.startswith(("RUN=", "RUN_READ=", "RUN_FIX=")):
                continue
            bare = [m.group(0) for m in self.PLACEHOLDER.finditer(line)]
            self.assertEqual(
                bare, [],
                f"unquoted placeholder {bare} in: {line}\n"
                f"an unquoted <REF> is a redirect; `<topic` truncates a file",
            )

    def test_repair_hint_does_not_copy_the_template(self) -> None:
        """`cp paths.example.yaml paths.local.yaml` made every check pass."""
        src = (ROOT / "scripts" / "deploy" / "check_paths.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "cp {PATHS_EXAMPLE} {PATHS_LOCAL}", src,
            "the repair hint must not create a comments-only config: it parses "
            "to an empty mapping, which reads as 'everything configured'",
        )


class HookInstallation(unittest.TestCase):
    """sync-allowlist.sh must merge hooks and must not install one by default."""

    def setUp(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX shell")
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".claude").mkdir()
        self.settings = self.home / ".claude" / "settings.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def _sync(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, HOME=str(self.home))
        return run(["bash", "scripts/allowlist/sync-allowlist.sh", *args], env=env)

    def test_user_hook_survives(self) -> None:
        self.settings.write_text(json.dumps({
            "allowedTools": [],
            "hooks": {"PermissionRequest": [
                {"matcher": "Bash",
                 "hooks": [{"type": "command", "command": "/my/own/hook.sh"}]}
            ]},
        }), encoding="utf-8")
        self._sync()
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        commands = [h["command"]
                    for entry in data["hooks"]["PermissionRequest"]
                    for h in entry["hooks"]]
        self.assertIn("/my/own/hook.sh", commands,
                      "sync-allowlist.sh replaced the user's PermissionRequest hook")

    def test_hook_is_opt_in_and_idempotent(self) -> None:
        self.settings.write_text('{"allowedTools": []}\n', encoding="utf-8")
        self._sync()
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertNotIn("hooks", data, "the recording hook must not be a default")
        self.assertFalse((self.home / ".claude" / "hooks").exists())

        self._sync("--with-hook")
        self._sync("--with-hook")
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        ours = [h["command"]
                for entry in data["hooks"]["PermissionRequest"]
                for h in entry["hooks"]
                if "permission-counter" in h["command"]]
        self.assertEqual(len(ours), 1, "hook entry duplicated on re-run")

    def test_deny_list_is_synced_and_additive(self) -> None:
        self.settings.write_text(
            json.dumps({"allowedTools": [], "deniedTools": ["Bash(my-deny *)"]}),
            encoding="utf-8",
        )
        self._sync()
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertIn("Bash(my-deny *)", data["deniedTools"])
        self.assertTrue(
            any("rm -rf" in d for d in data["deniedTools"]),
            "the shared deny list was not applied",
        )


class DeployDoesNotWriteConfig(unittest.TestCase):
    """`deploy.sh` used to materialize paths.local.yaml into the working tree."""

    def test_no_config_side_effect(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX shell")
        local = ROOT / "shared" / "paths.local.yaml"
        if local.exists():
            self.skipTest("this machine already has a config; nothing to prove")
        home = Path(tempfile.mkdtemp())
        try:
            (home / ".claude").mkdir()
            (home / ".dsh").mkdir()
            env = dict(os.environ, HOME=str(home), DSH_HOME=str(home / ".dsh"),
                       CI="true")
            run(["bash", "deploy.sh", "--all", "--target", str(home / "proj")], env=env)
            self.assertFalse(
                local.exists(),
                "deploy.sh created shared/paths.local.yaml as a side effect",
            )
        finally:
            shutil.rmtree(home, ignore_errors=True)


class AllowlistHygiene(unittest.TestCase):
    def test_no_rule_grants_arbitrary_execution(self) -> None:
        data = json.loads(
            (ROOT / "shared/config/allowlist-common.json").read_text(encoding="utf-8")
        )
        banned = {
            "Bash(zsh -i -c *)", "Bash(source *)", "Bash(rm *)", "Bash(mv *)",
            "Bash(cp *)", "Bash(chmod *)", "Bash(sed *)", "Bash(echo *)",
            "Bash(for *)", "Bash(pip install *)",
        }
        present = banned & set(data["allowedTools"])
        self.assertEqual(present, set(), f"unbounded rules present: {present}")

    def test_deny_list_exists(self) -> None:
        data = json.loads(
            (ROOT / "shared/config/allowlist-common.json").read_text(encoding="utf-8")
        )
        self.assertTrue(data.get("deniedTools"), "a baseline with no deny layer is decoration")


if __name__ == "__main__":
    unittest.main()


class ProgressiveDisclosure(unittest.TestCase):
    """A gate must not emit everything up front, nor hide a deferral entirely."""

    def _gate(self, skill: str) -> list[str]:
        if os.name == "nt":
            self.skipTest("the gate is a POSIX shell script")
        local = ROOT / "shared" / "paths.local.yaml"
        backup = local.read_bytes() if local.exists() else None
        try:
            # Satisfy every declared requirement explicitly instead of relying on
            # what this machine happens to discover. A vault-only config passes
            # locally (Zotero is running and gets discovered) and fails in CI,
            # which is the environment-dependent test this repository keeps
            # re-introducing.
            vault = Path(tempfile.mkdtemp())
            notes = vault / "02-Literature" / "ZoteroNotes"
            notes.mkdir(parents=True)
            local.write_text(
                "obsidian:\n"
                f"  vault: {vault}\n"
                f"  literature_notes: {notes}\n"
                "zotero:\n"
                "  local_api: http://127.0.0.1:23119\n"
                "  inbox_collection_key: ABCD1234\n"
                "  user_id: \"0\"\n",
                encoding="utf-8",
            )
            proc = run(["bash", str(GATE), skill])
            self.assertEqual(proc.returncode, 0, f"{skill} gate failed: {proc.stdout}{proc.stderr}")
            return proc.stdout.splitlines()
        finally:
            if backup is None:
                local.unlink(missing_ok=True)
            else:
                local.write_bytes(backup)

    def test_compact_output_announces_the_spec(self) -> None:
        for skill in ("arxiv-to-library", "zotero-paper-to-obsidian",
                      "zotero-inbox-to-library", "obsidian-zotero-tag-governance"):
            lines = self._gate(skill)
            self.assertTrue(
                any(line.startswith("SPEC=") for line in lines),
                f"{skill}: compact output names no spec, so the deferral is "
                f"undiscoverable and the agent has nowhere to look next",
            )
            self.assertFalse(
                any(line.startswith("READ=") for line in lines),
                f"{skill}: compact output already emits READ=, which is the "
                f"eager load this gate exists to avoid",
            )

    def test_compact_output_is_small(self) -> None:
        for skill in ("arxiv-to-library", "zotero-paper-to-obsidian",
                      "zotero-inbox-to-library", "obsidian-zotero-tag-governance"):
            lines = self._gate(skill)
            self.assertLessEqual(
                len(lines), 5, f"{skill}: compact gate emitted {len(lines)} lines"
            )

    def test_later_step_files_are_deferred(self) -> None:
        lines = self._gate("zotero-paper-to-obsidian")
        self.assertFalse(
            any(line.startswith("READ_AT_") for line in lines),
            "a later-step file is emitted at gate time; step 3 is not what the "
            "caller is doing now",
        )


class GlobalInstructionsStayThin(unittest.TestCase):
    """The every-session payload must stay an index, not a manual."""

    BUDGET = 2600  # bytes; roughly 650 tokens

    def test_global_files_fit_the_budget(self) -> None:
        for name in ("claude-global.md", "dsh-global.md"):
            path = ROOT / "global-config" / name
            size = path.stat().st_size
            self.assertLessEqual(
                size, self.BUDGET,
                f"{name} is {size} bytes; it is loaded into every session, so "
                f"detail belongs in the protocols it links",
            )

    def test_global_files_link_the_protocols(self) -> None:
        for name in ("claude-global.md", "dsh-global.md"):
            text = (ROOT / "global-config" / name).read_text(encoding="utf-8")
            for proto in ("karpathy-guidelines-ai.md", "literature-preflight.md",
                          "context-budget-protocol.md"):
                self.assertIn(proto, text, f"{name} no longer points at {proto}")

    def test_repo_gateway_stays_thin(self) -> None:
        size = (ROOT / "AGENTS.md").stat().st_size
        self.assertLessEqual(size, 1400, f"AGENTS.md is {size} bytes")

    def test_no_duplicated_safety_rules_outside_the_globals(self) -> None:
        """A rule repeated in two files is a rule that can disagree with itself."""
        duplicates = []
        for path in (ROOT / "shared" / "protocols").glob("*.md"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"(?i)never\s+`?git push", text):
                duplicates.append(path.name)
        self.assertEqual(duplicates, [], f"safety rule duplicated in {duplicates}")


class EveryAgentCanResolveItsSkills(unittest.TestCase):
    """Each platform needs a different file shape to discover a skill.

    `deploy.sh` links the same six skill names into five trees, and each tree is
    read by a different loader: Claude wants a flat `<name>.md`, DSH silently
    ignores a file without `name` + `description` frontmatter, Codex keeps its
    invocation policy in a separate `agents/openai.yaml`, and Grok has no
    invocation field at all. Deploying the wrong shape is silent — the skill
    simply never appears — so the shapes are pinned here.
    """

    OWNED = ("arxiv-to-library", "obsidian-zotero-tag-governance", "paper-lookup",
             "research", "zotero-inbox-to-library", "zotero-paper-to-obsidian")
    FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)

    def _fm(self, path: Path, *, required: bool = True) -> dict[str, str]:
        match = self.FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if match is None:
            self.assertFalse(required, f"{path} has no frontmatter")
            return {}
        out = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                out[key.strip()] = value.strip().strip('"')
        return out

    def test_generated_trees_have_the_shape_each_loader_needs(self) -> None:
        for name in self.OWNED:
            claude = ROOT / "claude-skills" / name / f"{name}.md"
            self.assertTrue(claude.is_file(), f"claude-skills/{name} missing")
            # Claude command files are plain markdown; frontmatter is optional.
            self.assertTrue(claude.read_text(encoding="utf-8").strip())

            for platform in ("cursor-skills", "grok-skills", "dsh-skills", "codex-skills"):
                path = ROOT / platform / name / "SKILL.md"
                self.assertTrue(path.is_file(), f"{platform}/{name}/SKILL.md missing")
                fm = self._fm(path)
                self.assertEqual(fm.get("name"), name, f"{platform}/{name} name mismatch")
                self.assertTrue(fm.get("description"), f"{platform}/{name} has no description")

    def test_invocation_is_declared_where_each_platform_reads_it(self) -> None:
        # paper-lookup is the one skill that may fire on a natural request.
        natural = "paper-lookup"
        for name in self.OWNED:
            wants_explicit = name != natural

            claude = self._fm(
                ROOT / "claude-skills" / name / f"{name}.md", required=wants_explicit
            )
            self.assertEqual(
                claude.get("disable-model-invocation") == "true", wants_explicit,
                f"claude-skills/{name}: disable-model-invocation is wrong",
            )

            dsh = self._fm(ROOT / "dsh-skills" / name / "SKILL.md")
            self.assertEqual(
                dsh.get("disable-model-invocation") == "true", wants_explicit,
                f"dsh-skills/{name}: disable-model-invocation is wrong",
            )

            codex = (ROOT / "codex-skills" / name / "agents/openai.yaml").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                f"allow_implicit_invocation: {'false' if wants_explicit else 'true'}",
                codex,
                f"codex-skills/{name}: allow_implicit_invocation is wrong",
            )

    def test_no_platform_is_missing_a_skill(self) -> None:
        trees = ("claude-skills", "cursor-skills", "codex-skills",
                 "grok-skills", "dsh-skills")
        for tree in trees:
            root = ROOT / tree
            self.assertTrue(root.is_dir(), f"{tree} missing — run generate.sh")
            present = {p.name for p in root.iterdir() if p.is_dir()}
            self.assertEqual(
                present, set(self.OWNED),
                f"{tree} does not carry exactly the six skills: "
                f"missing={set(self.OWNED) - present} extra={present - set(self.OWNED)}",
            )

#!/usr/bin/env python3
"""Validate skill source, generated artifacts, gate routing, and agent metadata."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
NAME_RE = re.compile(r"^[a-z0-9-]+$")


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def parse_simple_yaml(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        data[key.strip()] = value
    return data


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        fail("unterminated generated frontmatter")
    return text[end + len("\n---\n") :].lstrip("\n")


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        fail("unterminated generated frontmatter")
    return parse_yaml_lines(text[4:end].splitlines())


def parse_yaml_lines(lines: list[str]) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in lines:
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        data[key.strip()] = value
    return data


def source_skills() -> list[Path]:
    dirs = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    if not dirs:
        fail("no source skills found")
    return dirs


def grouped_skills() -> list[str]:
    path = ROOT / "shared/config/skill-groups.yaml"
    names: list[str] = []
    in_groups = False
    in_skill_list = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("groups:"):
            in_groups = True
            in_skill_list = False
            continue
        if in_groups and re.match(r"^[a-z_]+:", raw):
            break
        if in_groups and re.match(r"^  [a-z0-9_-]+:", raw):
            in_skill_list = False
        if in_groups and raw.strip() == "skills:":
            in_skill_list = True
            continue
        if in_groups and in_skill_list:
            match = re.match(r"^      - ([a-z0-9-]+)$", raw)
            if match:
                names.append(match.group(1))
    return names


def check_source_and_groups(skill_dirs: list[Path]) -> list[str]:
    names = [p.name for p in skill_dirs]
    for name in names:
        if not NAME_RE.match(name):
            fail(f"invalid skill directory name: {name}")
        src = SKILLS / name / "SKILL.md"
        meta = parse_simple_yaml(SKILLS / name / "meta.yaml")
        body = src.read_text(encoding="utf-8")
        if not meta.get("description"):
            fail(f"skills/{name}/meta.yaml missing description")
        if meta.get("invocation") not in {"explicit", "natural"}:
            fail(f"skills/{name}/meta.yaml invocation must be explicit or natural")
        if meta.get("context_cost") not in {"low", "medium", "high"}:
            fail(f"skills/{name}/meta.yaml context_cost must be low, medium, or high")
        if meta.get("script_first") not in {"true", "false"}:
            fail(f"skills/{name}/meta.yaml script_first must be true or false")
        if meta.get("context_cost") == "high" and meta.get("invocation") != "explicit":
            fail(f"skills/{name}/high context_cost must use explicit invocation")
        if "allow_implicit_invocation" in meta or "claude_frontmatter" in meta:
            fail(f"skills/{name}/meta.yaml uses legacy invocation fields; use invocation")
        # Either entry point is correct: `agent.sh gate` is the documented one and
        # forwards to skill-gate.sh. Requiring the literal `skill-gate.sh` would
        # force every skill to advertise the implementation instead of the
        # interface.
        gate_invocation = re.search(
            rf"(?:skill-gate\.sh|agent\.sh\s+gate)\s+{re.escape(name)}\b", body
        )
        if not gate_invocation:
            fail(f"skills/{name}/SKILL.md does not invoke the gate for {name}")
        prompt = meta.get("codex_prompt", "")
        if f"${name}" not in prompt:
            fail(f"skills/{name}/meta.yaml codex_prompt must mention ${name}")
    grouped = grouped_skills()
    missing = sorted(set(names) - set(grouped))
    extra = sorted(set(grouped) - set(names))
    duplicates = sorted({name for name in grouped if grouped.count(name) > 1})
    if missing:
        fail(f"skills missing from skill-groups.yaml: {', '.join(missing)}")
    if extra:
        fail(f"skill-groups.yaml references unknown skills: {', '.join(extra)}")
    if duplicates:
        fail(f"skill-groups.yaml has duplicate skills: {', '.join(duplicates)}")
    return names


def check_generated(name: str) -> None:
    src_body = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    meta = parse_simple_yaml(SKILLS / name / "meta.yaml")
    description = meta["description"]

    for platform in ("cursor-skills", "grok-skills"):
        path = ROOT / platform / name / "SKILL.md"
        if not path.exists():
            fail(f"{platform}/{name}/SKILL.md missing")
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if fm.get("name") != name or fm.get("description") != description:
            fail(f"{platform}/{name}/SKILL.md frontmatter is stale")
        if strip_frontmatter(text) != src_body:
            fail(f"{platform}/{name}/SKILL.md body is stale")

    codex = ROOT / "codex-skills" / name / "SKILL.md"
    if not codex.exists():
        fail(f"codex-skills/{name}/SKILL.md missing")
    codex_text = codex.read_text(encoding="utf-8")
    codex_fm = parse_frontmatter(codex_text)
    if codex_fm.get("name") != name or codex_fm.get("description") != description:
        fail(f"codex-skills/{name}/SKILL.md frontmatter is stale")
    if strip_frontmatter(codex_text) != src_body:
        fail(f"codex-skills/{name}/SKILL.md body is stale")

    claude = ROOT / "claude-skills" / name / f"{name}.md"
    if not claude.exists():
        fail(f"claude-skills/{name}/{name}.md missing")
    claude_text = claude.read_text(encoding="utf-8")
    if strip_frontmatter(claude_text) != src_body:
        fail(f"claude-skills/{name}/{name}.md body is stale")
    claude_fm = parse_frontmatter(claude_text)
    invocation = meta["invocation"]
    if invocation == "explicit" and claude_fm.get("disable-model-invocation") != "true":
        fail(f"claude-skills/{name} explicit skill missing disable-model-invocation")
    if invocation == "natural" and claude_fm:
        fail(f"claude-skills/{name} natural skill has unexpected invocation frontmatter")

    # DSH discovery ignores a SKILL.md without YAML frontmatter (and ignores a
    # frontmatter lacking name/description), so both fields and the invocation
    # mapping must be verified rather than assumed.
    dsh = ROOT / "dsh-skills" / name / "SKILL.md"
    if not dsh.exists():
        fail(f"dsh-skills/{name}/SKILL.md missing")
    dsh_text = dsh.read_text(encoding="utf-8")
    dsh_fm = parse_frontmatter(dsh_text)
    if not dsh_fm:
        fail(f"dsh-skills/{name}/SKILL.md missing YAML frontmatter (dsh would ignore it)")
    if dsh_fm.get("name") != name or dsh_fm.get("description") != description:
        fail(f"dsh-skills/{name}/SKILL.md frontmatter is stale")
    if strip_frontmatter(dsh_text) != src_body:
        fail(f"dsh-skills/{name}/SKILL.md body is stale")
    if invocation == "explicit" and dsh_fm.get("disable-model-invocation") != "true":
        fail(f"dsh-skills/{name} explicit skill missing disable-model-invocation")
    if invocation == "natural" and "disable-model-invocation" in dsh_fm:
        fail(f"dsh-skills/{name} natural skill has unexpected disable-model-invocation")


def check_no_stale_generated(source_names: list[str]) -> None:
    expected = set(source_names)
    for platform in ("claude-skills", "cursor-skills", "codex-skills", "grok-skills", "dsh-skills"):
        actual = {p.name for p in (ROOT / platform).iterdir() if p.is_dir() and not p.name.startswith(".")}
        extra = sorted(actual - expected)
        if extra:
            fail(f"{platform} has stale generated skills: {', '.join(extra)}")


def check_openai_yaml(name: str) -> None:
    meta = parse_simple_yaml(SKILLS / name / "meta.yaml")
    path = ROOT / "codex-skills" / name / "agents/openai.yaml"
    if not path.exists():
        fail(f"codex-skills/{name}/agents/openai.yaml missing")
    text = path.read_text(encoding="utf-8")
    required = {
        "display_name": meta.get("display_name", ""),
        "short_description": meta.get("short_description", ""),
        "default_prompt": meta.get("codex_prompt", ""),
    }
    for key, expected in required.items():
        if not expected:
            fail(f"skills/{name}/meta.yaml missing {key}")
        match = re.search(rf"^\s+{key}: \"(.*)\"$", text, re.MULTILINE)
        if not match:
            fail(f"openai.yaml for {name} missing quoted interface.{key}")
        if match.group(1) != expected.replace("\\", "\\\\").replace('"', '\\"'):
            fail(f"openai.yaml for {name} has stale interface.{key}")
    short_len = len(meta["short_description"])
    if short_len < 25 or short_len > 64:
        fail(f"short_description for {name} must be 25-64 chars, got {short_len}")
    if f"${name}" not in meta["codex_prompt"]:
        fail(f"default prompt for {name} must mention ${name}")

    expected_policy = "false" if meta["invocation"] == "explicit" else "true"
    policy_match = re.search(r"^  allow_implicit_invocation: (true|false)$", text, re.MULTILINE)
    if not policy_match:
        fail(f"openai.yaml for {name} missing policy.allow_implicit_invocation")
    if policy_match.group(1) != expected_policy:
        fail(f"openai.yaml for {name} has stale allow_implicit_invocation")


def check_gate(name: str) -> None:
    meta = parse_simple_yaml(SKILLS / name / "meta.yaml")
    if os.name == "nt":
        # The gates are POSIX shell scripts. Windows is supported for the Python
        # CLI, not for the shell entry points, so there is nothing to run here.
        return
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts/skills/skill-gate.sh"), name, "--root", "."],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    out = proc.stdout.strip().splitlines()
    if not out:
        fail(f"skill-gate.sh {name} produced no output")
    if len(out) > 6:
        fail(f"skill-gate.sh {name} produced {len(out)} lines, expected <=6")
    joined = "\n".join(out)
    if "unknown_skill" in joined:
        fail(f"skill-gate.sh does not route {name}")
    for line in out:
        if line.startswith(("READ=", "READ_AT_", "READ_WITH=")):
            _, value = line.split("=", 1)
            if not Path(value).exists():
                fail(f"skill-gate.sh {name} references missing path: {value}")
    has_run = any(line.startswith("RUN=") for line in out)
    if proc.returncode == 0 and meta["script_first"] == "true" and not has_run:
        fail(f"skill-gate.sh {name} missing RUN for script_first skill")
    if proc.returncode == 0 and meta["script_first"] == "true":
        if any(line.startswith("READ=") for line in out):
            fail(f"skill-gate.sh {name} eagerly emits READ for script_first skill")
    if proc.returncode != 0 and not joined.startswith("ERR "):
        fail(f"skill-gate.sh {name} failed without ERR line: {proc.stderr.strip()}")


def main() -> int:
    names = check_source_and_groups(source_skills())
    check_no_stale_generated(names)
    for name in names:
        check_generated(name)
        check_openai_yaml(name)
        check_gate(name)
    print(f"ok: {len(names)} skills consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())

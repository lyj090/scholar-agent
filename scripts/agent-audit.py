#!/usr/bin/env python3
"""Audit agent instruction files without loading their full contents into chat."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit


# Instruction filenames an agent runtime may load. The audit exists to catch
# defects in the files agents read, so the list mirrors the live runtimes: DSH
# reads AGENTS.md + CLAUDE.md (plus the .local overlays), Codex reads AGENTS.md,
# Claude Code CLAUDE.md.
LIVE_NAMES = {
    "agents.md",
    "claude.md",
    "agents.local.md",
    "claude.local.md",
    # Codex also reads AGENTS.md only; nothing else is auto-loaded today.
}

# Spellings that look authoritative and are NOT loaded by any runtime. They are
# scanned so the audit can name them, and they fail `--check`.
#
# This is the defect that motivated the split: `AGENT.md` sat in dotfiles from
# 2026-09-22 carrying the repo's real rules, and every session silently ignored
# it, because DSH's candidate list is literally
# `["AGENTS.md", "CLAUDE.md"]`. A missing file is loud; a misnamed file is
# invisible, and the agent then re-derives the rules with shell commands. Never
# merge these back into LIVE_NAMES to make the report quieter — the whole point
# is that a dead copy in a repo is drift, not a candidate.
DEAD_NAMES = {
    "agent.md",
    "gemini.md",
    "codex.md",
    "cursor.md",
    "copilot-instructions.md",
}

NAMES = LIVE_NAMES | DEAD_NAMES
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)")
ABSOLUTE_PATH_RE = re.compile(
    r"(?:file://)?/(?:home|Users)/|(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# Installed third-party trees we never author. `.cache` holds downloaded
# artifacts (e.g. `uv` archive snapshots of upstream repos) and `.oh-my-zsh`
# is a git clone of upstream: their instruction-like files are copies, so a
# finding inside them is noise that buries our own defects.
EXTERNAL_PARTS = {
    ".cache",
    ".oh-my-zsh",
    ".nvm",
    ".cursor",
    ".vscode",
}

# Directories whose whole purpose is holding the per-agent global config. A
# misnamed file directly inside one of these is as damaging as one in a repo
# (it is the file every session loads), but no `.git` exists to mark it as
# ours, so `project_root()` cannot find it.
AGENT_CONFIG_DIRS = {
    ".dsh",
    ".claude",
    ".claude-internal",
    ".codex",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory local agent instruction files and write a compact audit report."
    )
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Directory to scan; repeatable (default: $HOME).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Report path (default: .ai/reports/agent-audit-<timestamp>.md).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Return non-zero when a relative link is missing, a local absolute path is "
            "found, a heading is duplicated, or an instruction file uses a dead name."
        ),
    )
    return parser.parse_args()


def instruction_files(roots: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        root = root.expanduser().resolve()
        if root.is_file():
            if root.name.lower() in NAMES:
                found.add(root)
            continue
        if not root.is_dir():
            continue
        for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name != ".git")
            for name in names:
                if name.lower() in NAMES:
                    found.add((Path(current) / name).resolve())
    return sorted(found)


def project_root(path: Path) -> Optional[Path]:
    for parent in (path.parent, *path.parents):
        if (parent / ".git").exists():
            return parent
    return None


def scope(path: Path) -> str:
    parts = set(path.parts)
    if "node_modules" in parts or ".cargo" in parts or ".vscode-server" in parts:
        return "vendor"
    if ".codex" in parts and ".tmp" in parts:
        return "vendor"
    if ".quartz" in parts and "plugins" in parts:
        return "vendor"
    # CodeBuddy's plugin/skill marketplaces are downloaded third-party bundles,
    # not files we author; auditing them buries our own findings in noise.
    if ".codebuddy" in parts and parts & {"plugins", "skills-marketplace"}:
        return "vendor"
    if parts & EXTERNAL_PARTS:
        return "external"
    if ".backups" in parts:
        # Snapshot trees are frozen copies of what was already audited in place;
        # re-reporting them doubles every finding without adding information.
        return "external"
    if project_root(path) is not None:
        return "project"
    if parts & AGENT_CONFIG_DIRS:
        return "project"
    return "other"


def is_machine_local(path: Path) -> bool:
    """True for a deployed global baseline, whose absolute paths are intended.

    The per-agent global config (`~/.claude/CLAUDE.md`, `~/.dsh/AGENTS.md`, ...)
    is generated for one machine: the deploy step deliberately rewrites
    `~/.zshrc` into that machine's real shell rc. Flagging those paths would
    report the generator working as designed. The portable source lives in the
    scholar-agent repo, is a `project` scope, and is still checked there.
    """
    return path.parent.name in AGENT_CONFIG_DIRS


def markdown_issues(path: Path) -> tuple[list[str], list[str]]:
    """Return (missing relative links, local absolute references)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    missing: list[str] = []
    absolute: list[str] = []
    report_absolute = not is_machine_local(path)
    for match in LINK_RE.finditer(text):
        ref = match.group(1).strip()
        if ref.startswith("<") and ref.endswith(">"):
            ref = ref[1:-1]
        if ref.startswith("#"):
            continue
        parsed = urlsplit(ref)
        if parsed.scheme or ref.startswith("//"):
            if parsed.scheme == "file":
                absolute.append(ref)
            continue
        if ref.startswith("/"):
            absolute.append(ref)
            continue
        target = (path.parent / ref.split("#", 1)[0]).resolve()
        if not target.exists():
            line = text[: match.start()].count("\n") + 1
            missing.append(f"{path}:{line}: {ref}")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if ABSOLUTE_PATH_RE.search(line):
            absolute.append(f"{path}:{line_number}: {line.strip()}")
    return missing, absolute if report_absolute else []


def normalize_heading(title: str) -> str:
    """Fold a heading title for near-duplicate comparison.

    Strips a trailing parenthetical so that ``先读什么`` and
    ``先读什么（省 token）`` compare equal: two sections meant to answer the
    same question are one structural defect regardless of a qualifier.
    """
    folded = re.sub(r"[\s（(【\[].*$", "", title.strip().lower()).strip()
    return folded or title.strip().lower()


def duplicate_headings(path: Path) -> list[str]:
    """Return repeated or near-repeated heading titles inside one file.

    A repeated heading is a structural smell: an agent reading the file cannot
    tell the two sections apart, and neither can an anchor link. Callers skip
    vendor and external scopes because we do not own those files.
    """
    seen: dict[str, tuple[int, str]] = {}
    duplicates: list[str] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        match = HEADING_RE.match(line)
        if not match:
            continue
        raw_title = match.group(2).strip()
        key = normalize_heading(raw_title)
        if key in seen:
            first_line, first_title = seen[key]
            kind = "duplicated" if first_title == raw_title else "near-duplicated"
            duplicates.append(
                f"{path}:{number}: {kind} heading {raw_title!r} "
                f"(first at line {first_line}: {first_title!r})"
            )
        else:
            seen[key] = (number, raw_title)
    return duplicates


def dead_instruction_files(paths: list[Path]) -> list[str]:
    """Return instruction files whose name no runtime auto-loads.

    Only project/other scopes are reported: a vendored copy under
    `node_modules/` is not ours to rename. A dead file is worse than no file,
    because it looks like the rules are in force while sessions run without
    them — and path-scoped rules added to it never fire at all.
    """
    findings: list[str] = []
    for path in paths:
        if path.name.lower() not in DEAD_NAMES:
            continue
        if scope(path) in {"vendor", "external"}:
            continue
        findings.append(f"{path}: {path.name} is never auto-loaded by any agent runtime")
    return findings


def build_report(paths: list[Path], roots: list[Path]) -> tuple[str, dict[str, int]]:
    by_scope: dict[str, list[Path]] = defaultdict(list)
    by_hash: dict[str, list[Path]] = defaultdict(list)
    missing: list[str] = []
    absolute: list[str] = []
    headings: list[str] = []
    dead: list[str] = []
    vendored_missing = 0
    vendored_absolute = 0

    for path in paths:
        file_scope = scope(path)
        by_scope[file_scope].append(path)
        by_hash[hashlib.sha256(path.read_bytes()).hexdigest()].append(path)
        missing_links, absolute_refs = markdown_issues(path)
        if file_scope not in {"vendor", "external"}:
            # Findings we can act on. Vendor trees (editor extensions, cargo
            # registry) are read-only third-party copies whose defects are not
            # ours to fix, so keeping them out makes the report actionable.
            missing.extend(missing_links)
            absolute.extend(absolute_refs)
            headings.extend(duplicate_headings(path))
        else:
            vendored_missing += len(missing_links)
            vendored_absolute += len(absolute_refs)

    duplicate_groups = [items for items in by_hash.values() if len(items) > 1]
    dead = sorted(set(dead_instruction_files(paths)))
    missing = sorted(set(missing))
    absolute = sorted(set(absolute))
    headings = sorted(set(headings))
    counts = {
        "files": len(paths),
        "project": len(by_scope["project"]),
        "vendor": len(by_scope["vendor"]),
        "external": len(by_scope["external"]),
        "other": len(by_scope["other"]),
        "duplicates": len(duplicate_groups),
        "missing_links": len(missing),
        "absolute_refs": len(absolute),
        "dup_headings": len(headings),
        "dead_names": len(dead),
        "vendored_missing": vendored_missing,
        "vendored_absolute": vendored_absolute,
    }

    lines = [
        "# Agent instruction audit",
        "",
        f"- Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- Roots: {', '.join(str(root.expanduser().resolve()) for root in roots)}",
        f"- Files: {counts['files']} (project={counts['project']}, vendor={counts['vendor']}, "
        f"external={counts['external']}, other={counts['other']})",
        "",
        "## Project and user-maintained candidates",
        "",
    ]
    candidates = by_scope["project"] + by_scope["other"]
    if candidates:
        for path in candidates:
            lines.append(
                f"- `{path}` ({len(path.read_text(encoding='utf-8', errors='replace').splitlines())} lines)"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Vendor or external instruction files", ""])
    external = by_scope["vendor"] + by_scope["external"]
    if external:
        for path in external:
            lines.append(f"- `{path}` ({scope(path)})")
    else:
        lines.append("- None")

    lines.extend(["", "## Exact duplicate groups", ""])
    if duplicate_groups:
        for group in sorted(duplicate_groups, key=lambda item: str(item[0])):
            digest = hashlib.sha256(group[0].read_bytes()).hexdigest()[:12]
            lines.append(f"- {len(group)} files, sha256 `{digest}`")
            lines.extend(f"  - `{path}`" for path in group)
    else:
        lines.append("- None")

    lines.extend(["", "## Findings", ""])
    lines.append(f"- Dead instruction names (never auto-loaded): {len(dead)}")
    lines.extend(f"  - `{item}`" for item in dead)
    lines.append(f"- Missing relative links: {len(missing)}")
    lines.extend(f"  - `{item}`" for item in missing)
    lines.append(f"- Local absolute references: {len(absolute)}")
    lines.extend(f"  - `{item}`" for item in absolute)
    lines.append(f"- Duplicated headings within one file: {len(headings)}")
    lines.extend(f"  - `{item}`" for item in headings)
    if vendored_missing or vendored_absolute:
        lines.extend(
            [
                "",
                "## Vendored copies (not actionable)",
                "",
                f"- Skipped {vendored_missing} missing link(s) and "
                f"{vendored_absolute} absolute reference(s) inside vendor/external trees.",
            ]
        )

    return "\n".join(lines) + "\n", counts


def main() -> int:
    args = parse_args()
    roots = [Path(value) for value in args.roots] if args.roots else [Path.home()]
    paths = instruction_files(roots)
    report = args.report or (
        Path.cwd() / ".ai" / "reports" / f"agent-audit-{datetime.now():%Y%m%d-%H%M%S}.md"
    )
    report = report.expanduser().resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    content, counts = build_report(paths, roots)
    report.write_text(content, encoding="utf-8")

    print(f"REPORT={report}")
    print(
        "FILES={files} PROJECT={project} VENDOR={vendor} EXTERNAL={external} OTHER={other}".format(
            **counts
        )
    )
    print(
        "DUPLICATES={duplicates} BROKEN_LINKS={missing_links} "
        "ABSOLUTE_REFS={absolute_refs} DUP_HEADINGS={dup_headings} "
        "DEAD_NAMES={dead_names}".format(**counts)
    )

    if args.check and (
        counts["missing_links"]
        or counts["absolute_refs"]
        or counts["dup_headings"]
        or counts["dead_names"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_audit_notes.py — Batch audit all literature notes against quality gates.

Scans obsidian.literature_notes directory and checks each .md file for:
  1. Frontmatter exists + parseable
  2. Required fields (title, tags, authors, year, date)
  3. Tags: block list format + type/* + area/* present
  4. ## 关联 section present
  5. Filename rules (title case, no date suffix)
  6. Referenced images exist on disk

Replaces manual per-file scanning for the obsidian-zotero-tag-governance skill's Phase 1 Obsidian scan.
One command → full report. Agent calls: 0 (pure script).
NOT responsible for: fixing issues (that's other skills' job).
"""

from __future__ import annotations

import json
import os
import re
import sys

from ..config import ZoteroConfig
from ..frontmatter_tags import find_status_tag
from ..note_structure import (
    find_private_wikilinks,
    find_public_region_violations,
    has_personal_section,
    has_related_section,
)


# ─── Constants ────────────────────────────────────────────────────────────────────────────────────────────────────────

kRequiredFields = {"title", "tags", "authors", "year", "date"}


# ─── Public ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'audit-notes' subcommand."""
    p = subparsers.add_parser("audit-notes", help="Batch audit literature notes quality")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--errors-only", action="store_true", help="Only show files with issues")
    p.add_argument("--limit", "-n", type=int, default=0, help="Max files to check (0=all)")
    p.add_argument("--quick", action="store_true", help="Skip image existence checks")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute audit-notes. Returns 0 if all pass, 1 if any fail."""
    notes_dir = config.literature_notes
    if not os.path.isdir(notes_dir):
        print(f"[ERROR] Directory not found: {notes_dir}", file=sys.stderr)
        return 1

    # Collect all .md files
    md_files = sorted(f for f in os.listdir(notes_dir) if f.endswith(".md"))
    if args.limit > 0:
        md_files = md_files[: args.limit]

    results: list[dict] = []

    for filename in md_files:
        filepath = os.path.join(notes_dir, filename)
        issues = _AuditFile(
            filepath,
            filename,
            notes_dir,
            check_images=not getattr(args, "quick", False),
        )
        results.append({
            "file": filename,
            "path": filepath,
            "pass": len(issues) == 0,
            "issues": issues,
        })

    # Output
    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed

    if args.json:
        print(json.dumps({
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "results": results if not args.errors_only else [r for r in results if not r["pass"]],
        }, ensure_ascii=False, separators=(",", ":")))
    else:
        _PrintReport(results, args.errors_only)

    return 0 if failed == 0 else 1


# ─── Core Audit Logic ─────────────────────────────────────────────────────────────────────────────────────────────────


def _AuditFile(
    filepath: str,
    filename: str,
    notes_dir: str,
    *,
    check_images: bool = True,
) -> list[str]:
    """Audit a single note file. Returns list of issue descriptions."""
    issues: list[str] = []

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Frontmatter exists
    frontmatter = _ParseFrontmatter(content)
    if frontmatter is None:
        issues.append("No YAML frontmatter found")
        return issues  # Can't check further

    # 2. Required fields
    missing = kRequiredFields - set(frontmatter.keys())
    if missing:
        issues.append(f"Missing fields: {', '.join(sorted(missing))}")

    # 3. Tags format + required prefixes
    tags = frontmatter.get("tags")
    if not tags:
        issues.append("No tags field")
    elif not isinstance(tags, list):
        issues.append("tags is not a list")
    else:
        if not any(t.startswith("type/") for t in tags):
            issues.append("Missing type/* tag")
        if not any(t.startswith("area/") for t in tags):
            issues.append("Missing area/* tag")
        if not find_status_tag(tags):
            issues.append("Missing status/* tag (run: backfill-status-tags)")

    # Check inline array in raw YAML
    yaml_block = content.split("---")[1] if content.count("---") >= 2 else ""
    if re.search(r"tags:\s*\[", yaml_block):
        issues.append("Tags use inline array (must be block list)")

    from ..note_structure import validate_note
    issues.extend(validate_note(content))

    violations = find_public_region_violations(content)
    if violations:
        sample = "; ".join(f"L{n}(private-context)" for n, _t, _ in violations[:3])
        issues.append(
            f"{len(violations)} project/pipeline reference(s) in published "
            f"sections: {sample}"
        )

    private_links = find_private_wikilinks(content)
    if private_links:
        sample = "; ".join(f"L{n}(private-link)" for n, _t in private_links[:3])
        issues.append(
            f"{len(private_links)} link(s) into private partitions: {sample}"
        )

    # 5. Filename rules
    if re.search(r"\d{4}-\d{2}-\d{2}\.md$", filename):
        issues.append("Filename has date suffix")

    # 6. Referenced images exist
    if check_images:
        missing_images = []
        for img_match in re.finditer(r"!\[.*?\]\((assets/[^)]+)\)", content):
            img_rel = img_match.group(1)
            img_abs = os.path.join(notes_dir, img_rel)
            if not os.path.isfile(img_abs):
                missing_images.append(img_rel)
        if missing_images:
            issues.append(f"{len(missing_images)} missing image(s): {missing_images[0]}")

    # 7. Content length check
    body_start = content.find("---", content.find("---") + 3) + 3
    body = content[body_start:].strip() if body_start > 3 else ""
    if len(body) < 200:
        issues.append(f"Body too short ({len(body)} chars)")

    return issues


# ─── Helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _ParseFrontmatter(content: str) -> dict | None:
    """Minimal YAML frontmatter parser."""
    if not content.startswith("---"):
        return None
    end = content.find("---", 3)
    if end < 0:
        return None

    result = {}
    current_key = ""
    current_list: list | None = None

    for line in content[3:end].split("\n"):
        list_match = re.match(r"^\s*-\s+(.+)$", line)
        if list_match and current_key:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(list_match.group(1).strip().strip("'\""))
            continue

        kv_match = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if kv_match:
            current_key = kv_match.group(1)
            value = kv_match.group(2).strip().strip("'\"")
            current_list = None
            if value:
                if value.startswith("["):
                    try:
                        result[current_key] = json.loads(value)
                    except json.JSONDecodeError:
                        result[current_key] = value
                else:
                    result[current_key] = value

    return result if result else None


def _PrintReport(results: list[dict], errors_only: bool) -> None:
    """Print human-readable audit report."""
    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed

    print(f"=== Literature Notes Audit: {passed} pass / {failed} fail ===\n")

    if failed > 0:
        print("ERRORS:")
        for r in results:
            if not r["pass"]:
                print(f"  ❌ {r['file']}")
                for issue in r["issues"]:
                    print(f"       → {issue}")
        print()

    if not errors_only:
        pass_list = [r["file"] for r in results if r["pass"]]
        if pass_list:
            print(f"PASS ({len(pass_list)}):")
            for f in pass_list[:10]:
                print(f"  ✅ {f}")
            if len(pass_list) > 10:
                print(f"  ... and {len(pass_list) - 10} more")

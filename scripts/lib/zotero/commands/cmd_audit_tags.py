#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_audit_tags.py — Scan Obsidian notes and report tag violations.

Reads all .md files in a target directory, extracts frontmatter tags,
compares against tag_taxonomy_rules.yaml, and reports violations with
suggested fixes.

NOT responsible for: applying fixes (that's tag-governance + MCP write).
"""

from __future__ import annotations

import json
import os
import sys

import yaml

from ..config import REPO_ROOT, ZoteroConfig
from ..tag_governance import load_taxonomy

# ─── Constants ────────────────────────────────────────────────────────────────────────────────────────────────────────

kTaxonomyPath = os.path.join(REPO_ROOT, "shared", "config", "tag-taxonomy-rules.yaml")

# Legacy and derived tags are reported as non-canonical. This validator never
# proposes an automatic alias replacement; the governance plan must be updated
# explicitly before a write.
kForbiddenTags = {
    "/unread",
    "Computer Science - Robotics",
    "Computer Science - Computer Vision and Pattern Recognition",
    "Computer Science - Artificial Intelligence",
    "Computer Science - Machine Learning",
    "Statistics - Machine Learning",
    "status/has-obsidian-note",
    "source/zotero",
    "robotics",
    "rl",
    "paper",
    "survey",
}


# ─── Public ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'audit-tags' subcommand."""
    p = subparsers.add_parser("audit-tags", help="Scan Obsidian notes for tag violations")
    p.add_argument("--dir", default="", help="Subdirectory to scan (default: literature_notes)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--include-clean", action="store_true", help="Also list compliant tags")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute audit-tags."""
    # Determine scan directory
    if args.dir:
        scan_dir = os.path.join(config.vault, args.dir)
    else:
        scan_dir = config.literature_notes

    if not os.path.isdir(scan_dir):
        print(f"[ERROR] Directory not found: {scan_dir}", file=sys.stderr)
        return 1

    # Load taxonomy
    allowed_prefixes, allowed_values = _LoadTaxonomy()

    # Scan files
    all_tags: dict[str, list[str]] = {}  # tag → [files using it]
    violations: list[dict] = []
    clean_tags: set[str] = set()

    for filename in sorted(os.listdir(scan_dir)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(scan_dir, filename)
        tags = _ExtractTags(filepath)

        for tag in tags:
            all_tags.setdefault(tag, []).append(filename)

    # Classify each unique tag
    for tag, files in sorted(all_tags.items()):
        violation = _ClassifyTag(tag, allowed_prefixes, allowed_values)
        if violation:
            violation["files"] = files
            violation["count"] = len(files)
            violations.append(violation)
        else:
            clean_tags.add(tag)

    # Output
    if args.json:
        output = {
            "scan_dir": scan_dir,
            "total_files": len([f for f in os.listdir(scan_dir) if f.endswith(".md")]),
            "unique_tags": len(all_tags),
            "violations": violations,
            "violation_count": len(violations),
            "clean_count": len(clean_tags),
        }
        if args.include_clean:
            output["clean_tags"] = sorted(clean_tags)
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    else:
        _PrintReport(violations, clean_tags, scan_dir, args.include_clean)

    return 0 if not violations else 1


# ─── Private ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _LoadTaxonomy() -> tuple[set[str], set[str]]:
    """Load allowed tag prefixes and values from taxonomy YAML."""
    fallback_prefixes = {
        "type/", "area/", "topic/", "method/", "task/",
        "software/", "hardware/", "source/", "rel/", "status/",
    }

    if not os.path.isfile(kTaxonomyPath):
        return fallback_prefixes, set()

    try:
        taxonomy = load_taxonomy(kTaxonomyPath)
    except (OSError, yaml.YAMLError):
        return fallback_prefixes, set()

    prefixes = {
        f"{str(namespace).rstrip('/')}/"
        for namespace in (taxonomy.get("tag_prefixes") or {})
        if str(namespace).strip()
    }
    return prefixes or fallback_prefixes, set(taxonomy.get("allowed_tags") or [])


def _ExtractTags(filepath: str) -> list[str]:
    """Extract tags from a markdown file's frontmatter."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read(65536)  # Bound malformed/oversized files.
    except OSError:
        return []

    if not content.startswith("---"):
        return []

    end = content.find("\n---", 3)
    if end < 0:
        return []

    yaml_block = content[3:end]
    try:
        frontmatter = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        return []
    tags = frontmatter.get("tags") if isinstance(frontmatter, dict) else []
    if isinstance(tags, str):
        return [tags.strip()] if tags.strip() else []
    if not isinstance(tags, list):
        return []
    return [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]


def _ClassifyTag(tag: str, allowed_prefixes: set[str], allowed_values: set[str]) -> dict | None:
    """Classify a tag as violation or clean. Returns violation dict or None."""
    # Legacy/derived input: report only, never auto-map.
    if tag in kForbiddenTags:
        return {
            "tag": tag,
            "type": "legacy",
            "action": "REVIEW",
            "replacement": None,
        }

    # Check if it has a valid namespace prefix
    has_prefix = any(tag.startswith(p) for p in allowed_prefixes)
    if not has_prefix:
        # Bare tag without namespace
        return {
            "tag": tag,
            "type": "bare",
            "action": "REPLACE_OR_DELETE",
            "replacement": None,
        }

    # Has valid prefix — check if value is in taxonomy (if taxonomy loaded)
    if allowed_values and tag not in allowed_values:
        # Unknown value but valid namespace — warn but don't hard-fail
        return {
            "tag": tag,
            "type": "unknown_value",
            "action": "VERIFY",
            "replacement": None,
        }

    return None  # Clean


def _PrintReport(violations: list[dict], clean_tags: set[str], scan_dir: str, show_clean: bool) -> None:
    """Print human-readable tag audit report."""
    print(f"=== Tag Audit: {os.path.basename(scan_dir)} ===\n")

    if violations:
        print(f"VIOLATIONS ({len(violations)} tags):\n")
        print(f"  {'Action':<10} {'Tag':<45} {'→ Fix':<25} {'Files'}")
        print("  " + "-" * 90)
        for v in violations:
            fix = v["replacement"] or "—"
            print(f"  {v['action']:<10} {v['tag']:<45} {fix:<25} {v['count']} file(s)")
        print()

    print(f"CLEAN: {len(clean_tags)} compliant tags")
    if show_clean and clean_tags:
        for tag in sorted(clean_tags):
            print(f"  ✅ {tag}")

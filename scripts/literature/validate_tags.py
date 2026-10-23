#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_tags.py - Validate Obsidian note frontmatter tags against taxonomy

Reads YAML frontmatter from a .md file and checks tags against
shared/config/tag-taxonomy-rules.yaml. Reports violations and unknown values.

Usage:
    python scripts/literature/validate_tags.py note.md
    python scripts/literature/validate_tags.py note.md --strict    # exit 1 on any unknown value
    python scripts/literature/validate_tags.py note.md --fix       # interactive: suggest fixes

Exit codes:
    0 - all tags valid (or only warnings in non-strict mode)
    1 - invalid tags found (namespace violation or strict mode + unknown value)
    2 - file/parse error

Designed to be called by zotero-paper-to-obsidian skill
as a final quality gate before writing.
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import yaml
except ImportError:
    # Minimal YAML parsing fallback for frontmatter
    yaml = None


# === YAML frontmatter extraction (no dependency needed) ===

def extract_frontmatter(text: str) -> dict | None:
    """Extract YAML frontmatter from markdown text."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm_text = text[3:end].strip()

    if yaml:
        try:
            return yaml.safe_load(fm_text)
        except yaml.YAMLError:
            return None

    # Minimal fallback: parse tags array only
    result = {}
    tags = []
    in_tags = False
    for line in fm_text.splitlines():
        if line.strip().startswith("tags:"):
            in_tags = True
            continue
        if in_tags:
            if line.strip().startswith("- "):
                tags.append(line.strip()[2:].strip())
            else:
                in_tags = False
        # Extract title
        if line.strip().startswith("title:"):
            result["title"] = line.split(":", 1)[1].strip().strip('"').strip("'")
    if tags:
        result["tags"] = tags
    return result


# === Taxonomy loading ===

def find_taxonomy_file() -> str:
    """Locate tag_taxonomy_rules.yaml relative to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # scripts/ -> shared/
    candidate = os.path.join(script_dir, "..", "..", "shared", "config", "tag-taxonomy-rules.yaml")
    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    # Also try from cwd
    candidate2 = os.path.join(os.getcwd(), "shared", "config", "tag-taxonomy-rules.yaml")
    if os.path.exists(candidate2):
        return os.path.abspath(candidate2)
    return ""


def load_taxonomy(path: str) -> dict:
    """Load taxonomy rules from YAML file."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        if yaml:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
            from workflow_config import merge_taxonomy
            return merge_taxonomy(yaml.safe_load(f) or {})
        else:
            # Minimal parse: extract known values from mapping section
            content = f.read()
            return _minimal_parse_taxonomy(content)


def _minimal_parse_taxonomy(content: str) -> dict:
    """Minimal taxonomy parser without PyYAML."""
    result = {"tag_prefixes": {}, "mapping": {}, "limits": {}, "folder_defaults": {}}
    # Extract mapping values (lines like "  key: namespace/value")
    mapping = {}
    in_mapping = False
    for line in content.splitlines():
        if line.strip() == "mapping:":
            in_mapping = True
            continue
        if in_mapping:
            if line and not line[0].isspace():
                in_mapping = False
                continue
            if ":" in line and not line.strip().startswith("#"):
                parts = line.strip().split(":", 1)
                if len(parts) == 2:
                    val = parts[1].strip()
                    if "/" in val:
                        mapping[parts[0].strip()] = val
    result["mapping"] = mapping
    return result


def get_known_values(taxonomy: dict) -> dict[str, set]:
    """Build namespace -> set of known values from taxonomy mapping."""
    known: dict[str, set] = {}
    mapping = taxonomy.get("mapping", {})
    for _key, canonical in mapping.items():
        if "/" in canonical:
            ns, val = canonical.split("/", 1)
            if ns not in known:
                known[ns] = set()
            known[ns].add(val)
    return known


def get_valid_namespaces(taxonomy: dict) -> set:
    """Get set of valid tag namespace prefixes."""
    prefixes = taxonomy.get("tag_prefixes", {})
    if prefixes:
        return set(prefixes.keys())
    # Fallback: infer from mapping
    ns_set = set()
    for _key, canonical in taxonomy.get("mapping", {}).items():
        if "/" in canonical:
            ns_set.add(canonical.split("/", 1)[0])
    return ns_set


def get_folder_rules(taxonomy: dict, file_path: str) -> dict | None:
    """Find matching folder_defaults rule for a file path."""
    folder_defaults = taxonomy.get("folder_defaults", {})
    if not folder_defaults:
        return None
    for pattern, rules in folder_defaults.items():
        # Simple glob: "02-Literature/WebNotes/**" matches any file under that path
        base = pattern.rstrip("*").rstrip("/")
        if base in file_path:
            return rules
    return None


# === Validation ===

class ValidationResult:
    def __init__(self):
        self.errors: list[str] = []     # namespace violations, missing required
        self.warnings: list[str] = []   # unknown values (allowed but flagged)
        self.info: list[str] = []       # suggestions

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


def validate_tags(
    tags: list[str],
    taxonomy: dict,
    file_path: str = "",
) -> ValidationResult:
    """
    Validate a list of tags against the taxonomy.

    Checks:
    1. Each tag has a valid namespace prefix (namespace/value format)
    2. Namespace is in the allowed list
    3. Value is known in taxonomy mapping (warning if not)
    4. Folder-specific required tags are present
    5. Tag count per namespace within limits
    """
    result = ValidationResult()
    valid_ns = get_valid_namespaces(taxonomy)
    known_values = get_known_values(taxonomy)
    limits = taxonomy.get("limits", {})

    # Parse tags into namespace -> values
    ns_counts: dict[str, list[str]] = {}

    for tag in tags:
        # Check format
        if "/" not in tag:
            result.errors.append(f"Bare tag without namespace: '{tag}' → must be namespace/value")
            continue

        ns, val = tag.split("/", 1)

        # Check namespace validity
        if valid_ns and ns not in valid_ns:
            result.errors.append(f"Unknown namespace: '{ns}/' in tag '{tag}'. Valid: {sorted(valid_ns)}")
            continue

        # Track count
        if ns not in ns_counts:
            ns_counts[ns] = []
        ns_counts[ns].append(val)

        # Check value against known values
        if ns in known_values and val not in known_values[ns]:
            result.warnings.append(
                f"Unknown value '{val}' in namespace '{ns}/'. "
                f"Known: {sorted(known_values[ns])}"
            )

    # Check limits
    for ns, vals in ns_counts.items():
        if ns in limits and len(vals) > limits[ns]:
            result.warnings.append(
                f"Namespace '{ns}/' has {len(vals)} tags (limit: {limits[ns]}): {vals}"
            )

    # Check folder-specific rules
    folder_rules = get_folder_rules(taxonomy, file_path)
    if folder_rules:
        required = folder_rules.get("required", [])
        for req in required:
            if "/" in req and "*" not in req:
                # Exact tag required
                if req not in tags:
                    result.errors.append(f"Required tag missing: '{req}' (folder rule)")
            elif req.endswith("/*"):
                # Namespace required (any value)
                req_ns = req[:-2]
                if req_ns not in ns_counts:
                    result.errors.append(f"Required namespace missing: '{req_ns}/' (folder rule)")

    return result


# === Main ===

def main():
    parser = argparse.ArgumentParser(
        description="Validate Obsidian note frontmatter tags against taxonomy"
    )
    parser.add_argument("file", help="Markdown file to validate")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on unknown tag values (not just warnings)",
    )
    parser.add_argument(
        "--taxonomy",
        help="Path to tag_taxonomy_rules.yaml (auto-detected if not given)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print errors, not warnings or info",
    )
    args = parser.parse_args()

    # Read file
    if not os.path.exists(args.file):
        print(f"[ERROR] File not found: {args.file}", file=sys.stderr)
        sys.exit(2)

    with open(args.file, "r", encoding="utf-8") as f:
        text = f.read()

    # Parse frontmatter
    fm = extract_frontmatter(text)
    if fm is None:
        print(f"[ERROR] No YAML frontmatter found in: {args.file}", file=sys.stderr)
        sys.exit(2)

    tags = fm.get("tags", [])
    if not tags:
        print(f"[ERROR] No 'tags' field in frontmatter: {args.file}", file=sys.stderr)
        sys.exit(1)

    # Load taxonomy
    taxonomy_path = args.taxonomy or find_taxonomy_file()
    if not taxonomy_path:
        print("[WARN] tag_taxonomy_rules.yaml not found, skipping value checks", file=sys.stderr)
        taxonomy = {}
    else:
        taxonomy = load_taxonomy(taxonomy_path)

    # Validate
    result = validate_tags(tags, taxonomy, file_path=args.file)

    # Report
    exit_code = 0

    if result.errors:
        for err in result.errors:
            print(f"[ERROR] {err}", file=sys.stderr)
        exit_code = 1

    if result.warnings:
        if args.strict:
            for warn in result.warnings:
                print(f"[ERROR] (strict) {warn}", file=sys.stderr)
            exit_code = 1
        elif not args.quiet:
            for warn in result.warnings:
                print(f"[WARN] {warn}", file=sys.stderr)

    if not args.quiet and result.info:
        for info in result.info:
            print(f"[INFO] {info}", file=sys.stderr)

    if exit_code == 0:
        if not args.quiet:
            print(f"[OK] Tags valid: {tags}", file=sys.stderr)
    else:
        print(f"[FAIL] Tag validation failed for: {args.file}", file=sys.stderr)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

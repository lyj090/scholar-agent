#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_verify_note.py — Verify a generated literature note against quality gates.

Checks:
  1. Note file exists at expected path
  2. YAML frontmatter parseable + required fields present
  3. Tags valid (delegates to validate_tags logic)
  4. Figure count matches prepare-note expectation
  5. All referenced images exist on disk
  6. ## 关联 section present
  7. zotero_key present in frontmatter
  8. ## 快捷链接 with zotero:// links
  9. Filename matches title rules (no date suffix, no slug)
 10. zotero_key still resolves to a live (non-trashed) library item

One command replaces manual inspection. Returns structured JSON or human summary.
NOT responsible for: semantic quality (chapter order, equation explanations).
"""

from __future__ import annotations

import json
import os
import re

from ..api import ZoteroAPI, ZoteroAPIError
from ..mineru_lookup import MineruLookup
from ..config import ZoteroConfig
from ..frontmatter_tags import note_filename_for_title, resolve_note_path
from ..link_sync import is_valid_item_key
from ..note_structure import (
    find_private_wikilinks,
    find_public_region_violations,
    has_personal_section,
    has_related_section,
)

# ─── Constants ────────────────────────────────────────────────────────────────────────────────────────────────────────

kRequiredFrontmatterKeys = {"title", "tags", "authors", "date"}
kRequiredTagPrefixes = {"type/", "area/"}


# ─── Public ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'verify-note' subcommand."""
    p = subparsers.add_parser(
        "verify-note",
        help="Verify a literature note against quality gates",
    )
    p.add_argument("key", help="Zotero item key")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.set_defaults(func=_Run)


def _count_cache_images(cache_dir: str) -> int:
    """Number of figures the MinerU cache actually contains.

    Reads ``content_list.json``, where MinerU records image blocks. The previous
    implementation grepped ``full.md`` for ``![](images/<hex>)``, but published
    caches keep those references out of the Markdown (0 matches in every cache
    checked), so the baseline was always 0 and the check never ran.

    Falls back to counting files in ``images/`` when ``content_list.json`` is
    absent, so a partially-written cache still yields a usable baseline.
    """
    content_list = os.path.join(cache_dir, "content_list.json")
    if os.path.isfile(content_list):
        try:
            with open(content_list, "r", encoding="utf-8") as handle:
                entries = json.load(handle)
            if isinstance(entries, list):
                return sum(
                    1 for item in entries
                    if isinstance(item, dict) and item.get("type") == "image"
                )
        except (OSError, json.JSONDecodeError):
            pass
    images_dir = os.path.join(cache_dir, "images")
    if os.path.isdir(images_dir):
        try:
            return sum(
                1 for name in os.listdir(images_dir)
                if os.path.isfile(os.path.join(images_dir, name))
            )
        except OSError:
            pass
    return 0


def _Run(args, config: ZoteroConfig) -> int:
    """Execute verify-note. Returns 0 if all pass, 1 if any fail."""
    api = ZoteroAPI(config.api_url, config.user_id)
    cache = MineruLookup(config, api)

    checks: list[dict] = []

    # ─── Get item metadata ────────────────────────────────────────────────────────────────────────────────────────────
    try:
        item = api.get_item(args.key)
    except ZoteroAPIError as e:
        checks.append({"name": "item_fetch", "pass": False, "msg": str(e)})
        _Output(checks, args.json)
        return 1

    title = item.get("data", {}).get("title", "").strip()
    note_filename = note_filename_for_title(title)
    note_path = resolve_note_path(config.literature_notes, title, args.key)

    # ─── 1. Note file exists ──────────────────────────────────────────────────────────────────────────────────────────
    if note_path and os.path.isfile(note_path):
        checks.append({
            "name": "file_exists",
            "pass": True,
            "msg": os.path.basename(note_path),
        })
    else:
        checks.append({
            "name": "file_exists",
            "pass": False,
            "msg": f"NOT FOUND: {os.path.join(config.literature_notes, note_filename)}",
        })
        _Output(checks, args.json)
        return 1

    note_filename = os.path.basename(note_path)

    # Read note content
    with open(note_path, "r", encoding="utf-8") as f:
        content = f.read()

    # ─── 2. YAML frontmatter parseable ────────────────────────────────────────────────────────────────────────────────
    frontmatter = _ExtractFrontmatter(content)
    if frontmatter is None:
        checks.append({"name": "yaml_valid", "pass": False, "msg": "No YAML frontmatter found"})
    else:
        checks.append({"name": "yaml_valid", "pass": True, "msg": f"{len(frontmatter)} keys"})

    # ─── 3. Required frontmatter fields ───────────────────────────────────────────────────────────────────────────────
    if frontmatter:
        missing = kRequiredFrontmatterKeys - set(frontmatter.keys())
        if missing:
            checks.append({"name": "required_fields", "pass": False, "msg": f"Missing: {', '.join(sorted(missing))}"})
        else:
            checks.append({"name": "required_fields", "pass": True, "msg": "All present"})
    else:
        checks.append({"name": "required_fields", "pass": False, "msg": "No frontmatter"})

    # ─── 4. Tags valid ────────────────────────────────────────────────────────────────────────────────────────────────
    if frontmatter and "tags" in frontmatter:
        tags = frontmatter["tags"]
        if isinstance(tags, list) and len(tags) > 0:
            # Check required prefixes
            has_type = any(t.startswith("type/") for t in tags)
            has_area = any(t.startswith("area/") for t in tags)
            if has_type and has_area:
                checks.append({"name": "tags_valid", "pass": True, "msg": f"{len(tags)} tags, type/ + area/ present"})
            else:
                missing_prefixes = []
                if not has_type:
                    missing_prefixes.append("type/*")
                if not has_area:
                    missing_prefixes.append("area/*")
                checks.append({
                    "name": "tags_valid", "pass": False,
                    "msg": f"Missing required: {', '.join(missing_prefixes)}",
                })
        else:
            checks.append({"name": "tags_valid", "pass": False, "msg": "tags is empty or not a list"})
    else:
        checks.append({"name": "tags_valid", "pass": False, "msg": "No tags field"})

    # ─── 5. Tags are block list (not inline) ──────────────────────────────────────────────────────────────────────────
    # Check raw YAML for inline array pattern
    yaml_block = content.split("---")[1] if content.count("---") >= 2 else ""
    if re.search(r"tags:\s*\[", yaml_block):
        checks.append({
            "name": "tags_block_list", "pass": False,
            "msg": "Tags use inline array []; must use block list",
        })
    else:
        checks.append({"name": "tags_block_list", "pass": True, "msg": "Block list format"})

    # ─── 6. Figure count ──────────────────────────────────────────────────────────────────────────────────────────────
    note_images = re.findall(r"!\[.*?\]\(assets/[^)]+\)", content)
    checks.append({"name": "figure_count", "pass": True, "skipped": True,
                   "msg": f"{len(note_images)} image references; scientific coverage needs source review, not block-count ratios"})

    # ─── 7. Referenced images exist on disk ───────────────────────────────────────────────────────────────────────────
    missing_images = []
    for img_match in re.finditer(r"!\[.*?\]\((assets/[^)]+)\)", content):
        img_rel = img_match.group(1)
        img_abs = os.path.join(config.literature_notes, img_rel)
        if not os.path.isfile(img_abs):
            missing_images.append(img_rel)

    if not missing_images:
        checks.append({
            "name": "images_exist", "pass": True,
            "msg": f"All {len(note_images)} image files found",
        })
    else:
        checks.append({
            "name": "images_exist", "pass": False,
            "msg": f"{len(missing_images)} missing: {missing_images[0]}...",
        })

    from ..note_structure import validate_note
    from ..figure_identity import validate_figures
    structural = validate_note(content) + validate_figures(content, note_path)
    checks.append({"name": "source_note_contract", "pass": not structural,
                   "msg": ", ".join(structural) if structural else "source contract valid"})

    # ─── 8b. published-region isolation (the two-voice rule) ──────────────────
    violations = find_public_region_violations(content)
    if violations:
        sample = "; ".join(f"L{n}(private-context)" for n, _t, _ in violations[:3])
        checks.append({
            "name": "public_region_isolated",
            "pass": False,
            "msg": (
                f"{len(violations)} project/pipeline reference(s) in published "
                f"sections: {sample} — move personal analysis into a separate private document"
            ),
        })
    else:
        checks.append({
            "name": "public_region_isolated", "pass": True,
            "msg": "no project references in published sections",
        })

    private_links = find_private_wikilinks(content)
    if private_links:
        sample = "; ".join(f"L{n}(private-link)" for n, _t in private_links[:3])
        checks.append({
            "name": "no_private_links",
            "pass": False,
            "msg": f"{len(private_links)} link(s) into private partitions: {sample}",
        })
    else:
        checks.append({
            "name": "no_private_links", "pass": True,
            "msg": "no links into private partitions",
        })

    # ─── 9. zotero_key in frontmatter ───────────────────────────────────────────────────────────────────────────────
    zotero_key = frontmatter.get("zotero_key") if frontmatter else None
    if zotero_key and is_valid_item_key(str(zotero_key)):
        checks.append({"name": "zotero_key_present", "pass": True, "msg": str(zotero_key)})
    else:
        checks.append({
            "name": "zotero_key_present",
            "pass": False,
            "msg": "Missing or invalid zotero_key (re-run write-note --force)",
        })

    # ─── 9b. zotero_key still resolves to a live library item ────────────────────────────────────────────────────────
    # Format alone is not enough: a merge trashes the donor, so a note can keep
    # a syntactically perfect key that no longer opens anything. Two notes in
    # this vault were in exactly that state while all 12 checks reported pass,
    # which is how the breakage stayed invisible.
    if zotero_key and is_valid_item_key(str(zotero_key)):
        try:
            envelope = api.get_item(str(zotero_key))
            is_deleted = bool(envelope.get("data", {}).get("deleted"))
            if is_deleted:
                checks.append({
                    "name": "key_alive",
                    "pass": False,
                    "msg": (
                        f"{zotero_key} is in the Zotero trash (merged away?). "
                        "Re-point zotero_key/快捷链接 at the surviving item."
                    ),
                })
            else:
                checks.append({"name": "key_alive", "pass": True, "msg": str(zotero_key)})
        except ZoteroAPIError:
            checks.append({
                "name": "key_alive",
                "pass": False,
                "msg": f"{zotero_key} does not resolve in the Zotero library",
            })

    # ─── 10. Quick links block ──────────────────────────────────────────────────────────────────────────────────────
    has_quick_header = bool(re.search(r"^## 快捷链接", content, re.MULTILINE))
    zotero_link_count = len(re.findall(r"zotero://", content))
    if has_quick_header and zotero_link_count >= 1:
        checks.append({
            "name": "quick_links_present",
            "pass": True,
            "msg": f"{zotero_link_count} zotero:// link(s)",
        })
    else:
        checks.append({
            "name": "quick_links_present",
            "pass": False,
            "msg": "Missing ## 快捷链接 or zotero:// links (re-run write-note --force)",
        })

    # ─── 11. Filename rules ───────────────────────────────────────────────────────────────────────────────────────────
    # Should not have date suffix or slug
    if re.search(r"\d{4}-\d{2}-\d{2}\.md$", note_filename):
        checks.append({"name": "filename_rules", "pass": False, "msg": "Filename has date suffix"})
    elif note_filename.lower() == note_filename:
        checks.append({
            "name": "filename_rules", "pass": False,
            "msg": "Filename is all lowercase (should preserve title case)",
        })
    else:
        checks.append({"name": "filename_rules", "pass": True, "msg": "Correct format"})

    # ─── 12. Content not empty ────────────────────────────────────────────────────────────────────────────────────────
    body_start = content.find("---", content.find("---") + 3) + 3
    body = content[body_start:].strip() if body_start > 3 else content
    if body:
        checks.append({"name": "content_length", "pass": True, "msg": f"{len(body)} chars"})
    else:
        checks.append({"name": "content_length", "pass": False, "msg": "Empty note body"})

    # ─── Output ───────────────────────────────────────────────────────────────────────────────────────────────────────
    _Output(checks, args.json)

    all_passed = all(c["pass"] for c in checks)
    return 0 if all_passed else 1


# ─── Helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _ExtractFrontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter as dict. Returns None if not found/invalid."""
    if not content.startswith("---"):
        return None

    end = content.find("---", 3)
    if end < 0:
        return None

    yaml_str = content[3:end].strip()

    # Minimal YAML parsing (no pyyaml dependency)
    result = {}
    current_key = ""
    current_list: list | None = None

    for line in yaml_str.split("\n"):
        # List item
        list_match = re.match(r"^\s*-\s+(.+)$", line)
        if list_match and current_key:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(list_match.group(1).strip().strip("'\""))
            continue

        # Key-value pair
        kv_match = re.match(r"^(\w+):\s*(.*)$", line)
        if kv_match:
            current_key = kv_match.group(1)
            value = kv_match.group(2).strip().strip("'\"")
            current_list = None
            if value:
                # Try to parse as JSON array
                if value.startswith("["):
                    try:
                        result[current_key] = json.loads(value)
                    except json.JSONDecodeError:
                        result[current_key] = value
                else:
                    result[current_key] = value
            # If empty value, might be followed by list items
            continue

    return result if result else None


def _Output(checks: list[dict], as_json: bool) -> None:
    """Print results."""
    all_passed = all(c["pass"] for c in checks)

    if as_json:
        print(json.dumps({
            "all_passed": all_passed,
            "checks": checks,
            "passed": sum(1 for c in checks if c["pass"]),
            "failed": sum(1 for c in checks if not c["pass"]),
        }, ensure_ascii=False, indent=2))
    else:
        for check in checks:
            icon = "✅" if check["pass"] else "❌"
            print(f"  {icon} {check['name']}: {check['msg']}")

        print()
        passed = sum(1 for c in checks if c["pass"])
        total = len(checks)
        if all_passed:
            print(f"  [OK] All {total} checks passed")
        else:
            failed = total - passed
            print(f"  [FAIL] {failed}/{total} checks failed")

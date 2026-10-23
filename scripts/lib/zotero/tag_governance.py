#!/usr/bin/env python3
"""Reusable two-sided Zotero/Obsidian paper tag governance.

The module deliberately separates planning from mutation:

    plan -> inspect JSON -> apply -> verify

Zotero is read through the Local API and written through
:mod:`zotero.local_write` (the Zotero 10+ native write API), so applying a plan
no longer needs the llm-for-zotero plugin. Obsidian edits are limited to the
YAML ``tags`` block.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .api import ZoteroAPI
from .action_ledger import ActionLedger
from .config import ZoteroConfig
from .frontmatter_tags import split_frontmatter
from .local_write import build_write_client


RESEARCH_ITEM_TYPES = {
    "preprint",
    "journalArticle",
    "conferencePaper",
    "report",
    "bookSection",
}

NAMESPACE_ORDER = {
    "type": 0,
    "area": 1,
    "topic": 2,
    "method": 3,
    "task": 4,
    "software": 5,
    "hardware": 6,
    "source": 7,
    "rel": 8,
    "status": 9,
}

#: arXiv category names, exactly as Zotero's arXiv connector writes them.
#: ``Computer Science - Robotics`` et al. are arXiv's own taxonomy, not the
#: curated one, so they are machine noise rather than an unmapped intent.
_ARXIV_CATEGORY = re.compile(
    r"^(?:"
    r"Computer Science|Electrical Engineering and Systems Science|Mathematics|"
    r"Statistics|Physics|Quantitative Biology|Quantitative Finance|"
    r"Nonlinear Sciences|Economics|Astrophysics|Condensed Matter|"
    r"General Relativity and Quantum Cosmology|High Energy Physics|"
    r"Mathematical Physics|Nuclear Experiment|Nuclear Theory"
    r")\s+-\s+.+$"
)

#: arXiv tag shorthand such as ``Machine Learning (cs.LG)`` / ``Robotics (cs.RO)``.
_ARXIV_SHORTHAND = re.compile(r"^[A-Za-z][A-Za-z0-9 &'\-]*\([a-z]+\.[A-Za-z]{2}\)$")

#: Fields of Science classification; the arXiv connector emits a single lump.
_FOS = re.compile(r"^FOS:\s+.+$")

#: Zotero's own read/unread marker, written by the web connector.
_CONNECTOR_INTERNAL = frozenset({"/unread"})


def is_connector_noise(tag: str) -> bool:
    """True for machine-generated connector tags, not user intent.

    These are dropped outright by the governance plan instead of blocking it.
    The distinction matters: ``/unread`` and the arXiv taxonomy carry no
    semantic decision, whereas an unmapped bare tag such as ``robotics`` might
    be a real (if sloppy) intent that a human should resolve. Only the provably
    machine-generated forms are auto-dropped; everything else stays in
    ``unknown`` and keeps blocking ``apply``.

    Kept deliberately narrow — a false positive silently discards a tag.
    """
    value = (tag or "").strip()
    if not value:
        return False
    if value in _CONNECTOR_INTERNAL:
        return True
    if value.startswith("/"):
        return True
    return bool(
        _ARXIV_CATEGORY.match(value)
        or _ARXIV_SHORTHAND.match(value)
        or _FOS.match(value)
    )

TITLE_TOPIC_RULES = (
    (("event-based", "event camera", "event vision"), "topic/event-vision"),
    (("slam", "lidar", "odometry"), "topic/slam"),
    (("world model", "foundation model"), "topic/world-model"),
    (("retarget", "teleoperation", "hand-arm"), "topic/hand-retargeting"),
    (("manipulation", "grasp", "object"), "topic/manipulation"),
    (("locomotion", "legged", "humanoid", "walking"), "topic/locomotion"),
    (("control",), "topic/control"),
)

TITLE_METHOD_RULES = (
    (("ppo", "reinforcement learning", "reinforcement-learning"), "method/reinforcement-learning"),
    (("gail", "adversarial imitation"), "method/adversarial-imitation-learning"),
    (("amp", "motion prior"), "method/amp"),
    (("imitation", "demonstration", "motion capture"), "method/imitation-learning"),
    (("diffusion",), "method/diffusion-policy"),
    (("transformer",), "method/transformer"),
    (("sim-to-real", "sim2real"), "method/sim-to-real"),
    (("retarget", "inverse kinematics", "kinematic"), "method/kinematic-optimization"),
    (("impedance", "admittance", "force control"), "method/impedance-control"),
)

TITLE_TASK_RULES = (
    (("loco-manipulation", "mobile manipulator"), "task/loco-manipulation"),
    (("grasp", "grasping", "pick up"), "task/grasping"),
    (("bimanual", "dual-arm", "cooperative"), "task/bimanual-manipulation"),
    (("tracking", "track"), "task/feature-tracking"),
    (("motion retarget", "retargeting"), "task/motion-retargeting"),
    (("motion planning", "trajectory optimization"), "task/motion-planning"),
    (("inverse kinematics", "ik solver"), "task/inverse-kinematics"),
)


def load_taxonomy(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    from workflow_config import merge_taxonomy
    data = merge_taxonomy(data)
    data["allowed_tags"] = set((data.get("mapping") or {}).values()) | set(
        data.get("default_tags") or []
    )
    return data


def _frontmatter(content: str) -> tuple[str, str] | None:
    """Return ``(yaml_text, tail)`` using the historical split convention.

    ``yaml_text`` excludes the newline preceding the closing fence and ``tail``
    begins with that newline, so ``"---" + yaml_text + tail`` round-trips. The
    delimiter is located line-anchored (via :func:`split_frontmatter`) rather
    than by a bare ``"---"`` search, which used to cut a quoted title such as
    ``title: "A --- B"`` in half.
    """
    parsed = split_frontmatter(content)
    if parsed is None:
        return None
    inner, tail = parsed
    if inner.endswith("\n"):
        return inner[:-1], "\n" + tail
    return inner, tail


def _note_index(notes_dir: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    root = Path(notes_dir)
    if not root.is_dir():
        return result

    for path in sorted(root.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        parsed = _frontmatter(content)
        if parsed is None:
            continue
        yaml_text, _body = parsed
        try:
            data = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError:
            continue
        key = data.get("zotero_key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Z0-9]{8}", key):
            continue
        tags = data.get("tags")
        tags = {
            tag.strip()
            for tag in tags
            if isinstance(tag, str) and tag.strip()
        } if isinstance(tags, list) else set()
        result[key] = {
            "path": str(path),
            "tags": tags,
        }
    return result


def _title_has(title: str, *terms: str) -> bool:
    value = title.lower()
    return any(term in value for term in terms)


def _add(tags: set[str], tag: str, allowed: set[str]) -> None:
    if tag in allowed:
        tags.add(tag)


def _cap_namespace(tags: set[str], namespace: str, limit: int) -> None:
    values = sorted(t for t in tags if t.startswith(namespace + "/"))
    if len(values) <= limit:
        return
    priority = {
        "type": ["type/survey", "type/benchmark", "type/dataset", "type/paper"],
        "area": ["area/robotics", "area/computer-vision", "area/control", "area/machine-learning", "area/tools"],
        "topic": ["topic/event-vision", "topic/perception", "topic/manipulation", "topic/hand-retargeting", "topic/locomotion", "topic/slam", "topic/world-model"],
        "method": ["method/reinforcement-learning", "method/imitation-learning", "method/amp", "method/diffusion-policy", "method/transformer", "method/sim-to-real"],
        "task": ["task/loco-manipulation", "task/grasping", "task/bimanual-manipulation", "task/motion-retargeting", "task/motion-planning"],
        "rel": ["rel/thesis-core", "rel/thesis-related", "rel/amp", "rel/background", "rel/related-work", "rel/toolchain"],
        "source": ["source/cvpr", "source/rss", "source/corl", "source/icra", "source/iros", "source/arxiv"],
    }.get(namespace, [])
    ordered = [tag for tag in priority if tag in values]
    ordered.extend(tag for tag in values if tag not in ordered)
    keep = set(ordered[:limit])
    tags.difference_update(values)
    tags.update(keep)


def canonicalize_tags(
    item: dict[str, Any],
    raw_tags: set[str],
    note_tags: set[str],
    taxonomy: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(canonical_tags, unknown, dropped_noise)``.

    No aliases are resolved here. A non-canonical input that is *not*
    recognisable connector noise is reported as ``unknown`` and blocks apply
    until it is explicitly removed or the taxonomy is updated.

    Machine-generated connector tags (arXiv categories, ``/unread``) are
    returned separately as ``dropped_noise``. They are never written to either
    side and never block apply — see :func:`is_connector_noise` for why the
    split is drawn where it is.
    """
    allowed = taxonomy["allowed_tags"]
    tags: set[str] = set()
    unknown: list[str] = []
    dropped_noise: list[str] = []

    raw_values = {
        value.strip()
        for value in raw_tags | note_tags
        if isinstance(value, str) and value.strip()
    }
    for raw in sorted(raw_values):
        if is_connector_noise(raw):
            dropped_noise.append(raw)
        elif raw not in allowed:
            unknown.append(raw)
        else:
            tags.add(raw)

    title = item.get("title") or ""

    # Exactly one type. Existing note/Zotero semantics win over inference.
    existing_types = [tag for tag in tags if tag.startswith("type/")]
    tags.difference_update(existing_types)
    if "type/survey" in existing_types:
        tags.add("type/survey")
    elif "type/benchmark" in existing_types:
        tags.add("type/benchmark")
    elif "type/dataset" in existing_types:
        tags.add("type/dataset")
    elif _title_has(title, "survey", "review", "tutorial survey"):
        _add(tags, "type/survey", allowed)
    elif _title_has(title, "benchmark"):
        _add(tags, "type/benchmark", allowed)
    elif _title_has(title, "dataset"):
        _add(tags, "type/dataset", allowed)
    else:
        _add(tags, "type/paper", allowed)

    # A paper needs one stable area; only infer a second area from its title.
    areas = {tag for tag in tags if tag.startswith("area/")}
    tags.difference_update(areas)
    if not areas:
        areas.add("area/robotics")
    if not any(tag in areas for tag in ("area/computer-vision", "area/vision")) and _title_has(
        title, "event", "vision", "camera", "pose estimation", "slam", "lidar", "odometry", "detection", "image", "video", "depth"
    ):
        areas.add("area/computer-vision")
    if not any(tag in areas for tag in ("area/machine-learning", "area/control")) and _title_has(
        title, "reinforcement", "imitation", "ppo", "diffusion", "transformer", "policy", "control", "locomotion", "retarget"
    ):
        areas.add("area/control" if _title_has(title, "control", "locomotion", "retarget") else "area/machine-learning")
    if "area/vision" in areas:
        areas.remove("area/vision")
        areas.add("area/computer-vision")
    tags.update(sorted(areas, key=lambda value: ("area/robotics", "area/computer-vision", "area/control", "area/machine-learning", "area/tools").index(value) if value in ("area/robotics", "area/computer-vision", "area/control", "area/machine-learning", "area/tools") else 99)[:2])

    # Add one topic/method/task only when the paper has none in that namespace.
    if not any(tag.startswith("topic/") for tag in tags):
        for terms, candidate in TITLE_TOPIC_RULES:
            if _title_has(title, *terms):
                _add(tags, candidate, allowed)
                break
    if not any(tag.startswith("method/") for tag in tags):
        for terms, candidate in TITLE_METHOD_RULES:
            if _title_has(title, *terms):
                _add(tags, candidate, allowed)
                break
    if not any(tag.startswith("task/") for tag in tags):
        for terms, candidate in TITLE_TASK_RULES:
            if _title_has(title, *terms):
                _add(tags, candidate, allowed)
                break

    # Infer arXiv source only when metadata makes it unambiguous.
    if not any(tag.startswith("source/") for tag in tags):
        doi = item.get("DOI") or ""
        catalog = (item.get("libraryCatalog") or "").lower()
        url = item.get("url") or ""
        if "arxiv" in doi.lower() or "arxiv" in catalog or "arxiv.org" in url:
            _add(tags, "source/arxiv", allowed)

    # Obsidian reading status is authoritative for linked notes; otherwise use
    # Zotero status or the default intake state.
    statuses = [tag for tag in note_tags if tag.startswith("status/")]
    if not statuses:
        statuses = [tag for tag in raw_tags if tag.startswith("status/") and tag in allowed]
    tags.difference_update({tag for tag in tags if tag.startswith("status/")})
    tags.add(statuses[0] if statuses else "status/to-read")

    for namespace, limit in (taxonomy.get("limits") or {}).items():
        _cap_namespace(tags, namespace, int(limit))

    return (
        sorted(tags, key=lambda tag: (NAMESPACE_ORDER.get(tag.split("/", 1)[0], 99), tag)),
        sorted(set(unknown)),
        sorted(set(dropped_noise)),
    )


def build_plan(config: ZoteroConfig, taxonomy_path: str) -> dict[str, Any]:
    taxonomy = load_taxonomy(taxonomy_path)
    api = ZoteroAPI(config.api_url, config.user_id)
    notes = _note_index(config.literature_notes)
    all_items = api.get_all("/items/top")
    selected = []
    for row in all_items:
        item = row.get("data", {})
        key = item.get("key")
        if item.get("itemType") in RESEARCH_ITEM_TYPES or key in notes:
            selected.append(row)

    rows: list[dict[str, Any]] = []
    for row in selected:
        item = row.get("data", {})
        key = item.get("key")
        zotero_tags = {tag.get("tag") for tag in item.get("tags", []) if isinstance(tag, dict) and tag.get("tag")}
        note = notes.get(key, {})
        note_tag_set = set(note.get("tags", set()))
        after, unknown, dropped_noise = canonicalize_tags(
            item, zotero_tags, note_tag_set, taxonomy
        )
        rows.append({
            "key": key,
            "title": item.get("title", ""),
            "item_type": item.get("itemType", ""),
            "note_path": note.get("path"),
            "before_zotero": sorted(zotero_tags),
            "before_obsidian": sorted(note_tag_set),
            "after": after,
            "unknown": unknown,
            "dropped_noise": dropped_noise,
        })

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy": os.path.abspath(taxonomy_path),
        "scope": "top-level research items plus linked literature notes",
        "items": rows,
    }


def replace_note_tags(content: str, tags: list[str]) -> str:
    parsed = _frontmatter(content)
    if parsed is None:
        return content
    yaml_text, body = parsed
    lines = yaml_text.splitlines()
    output: list[str] = []
    index = 0
    found = False
    while index < len(lines):
        if lines[index].strip() == "tags:":
            found = True
            output.append("tags:")
            output.extend(f"  - {tag}" for tag in tags)
            index += 1
            while index < len(lines) and (re.match(r"^\s+-\s+", lines[index]) or not lines[index].strip()):
                index += 1
            continue
        output.append(lines[index])
        index += 1
    if not found:
        output.insert(0, "tags:")
        output[1:1] = [f"  - {tag}" for tag in tags]
    return "---" + "\n".join(output) + body


def apply_obsidian_plan(plan: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    changed = 0
    skipped = 0
    conflicts: list[str] = []
    for row in plan.get("items", []):
        path = row.get("note_path")
        if not path or not os.path.isfile(path):
            continue
        content = Path(path).read_text(encoding="utf-8")
        parsed = _frontmatter(content)
        if parsed is None:
            continue
        yaml_text, _body = parsed
        current = yaml.safe_load(yaml_text) or {}
        current_tags = sorted(current.get("tags", [])) if isinstance(current.get("tags"), list) else []
        before = sorted(row.get("before_obsidian", []))
        if not force and current_tags != before:
            conflicts.append(path)
            continue
        updated = replace_note_tags(content, row.get("after", []))
        if updated == content:
            skipped += 1
            continue
        Path(path).write_text(updated, encoding="utf-8")
        changed += 1
    return {"changed": changed, "skipped": skipped, "conflicts": conflicts}


def apply_zotero_plan(
    plan: dict[str, Any], config: ZoteroConfig, *, force: bool = False
) -> dict[str, Any]:
    """Apply the Zotero half of a tag plan via the native write API.

    Tags are written as a **complete replacement** (``set_tags``), which is what
    a governance plan means: ``after`` is the authoritative taxonomy for the
    item, not an addition to whatever is already there.

    Every changed item is recorded in the action ledger with its prior tag list,
    so a governance sweep across dozens of items is revertible with
    ``write undo``. It previously bypassed the ledger entirely: the plan is
    applied to 70+ items in one call, and the only safety net was a
    ``<plan>.before.json`` file that no command could read back.
    """
    changes = [row for row in plan.get("items", []) if sorted(row.get("before_zotero", [])) != sorted(row.get("after", []))]
    if not changes:
        return {"changed": 0, "missing": []}

    client = build_write_client(config)
    ledger = ActionLedger()
    changed = 0
    missing: list[str] = []
    before: dict[str, dict[str, Any]] = {}
    applied: list[str] = []
    for row in changes:
        key = row.get("key")
        if not key:
            continue
        try:
            client.set_tags(key, list(row.get("after", [])))
            before[key] = {"tags": row.get("before_zotero", [])}
            applied.append(key)
            changed += 1
        except Exception as error:
            # A key that no longer resolves is reported, not fatal: the rest of
            # the plan still applies and the caller sees exactly what was missed.
            if "404" in str(error) or "not found" in str(error).lower():
                missing.append(key)
            else:
                raise

    if applied:
        ledger.record_write(
            operation="tag_governance_apply",
            keys=applied,
            before=before,
            description=f"tag-governance: normalise tags on {len(applied)} item(s)",
        )
    return {"changed": changed, "missing": missing}


def check_zotero_drift(config: ZoteroConfig, plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return plan rows whose Zotero tags changed after the plan was generated."""
    api = ZoteroAPI(config.api_url, config.user_id)
    current = {
        row.get("data", {}).get("key"): {
            tag.get("tag")
            for tag in row.get("data", {}).get("tags", [])
            if isinstance(tag, dict) and tag.get("tag")
        }
        for row in api.get_all("/items/top")
    }
    conflicts = []
    for row in plan.get("items", []):
        key = row.get("key")
        if key in current and sorted(current[key]) != sorted(row.get("before_zotero", [])):
            conflicts.append({"key": key, "problem": "zotero_tags", "current": sorted(current[key]), "planned_before": row.get("before_zotero", [])})
    return conflicts


def check_obsidian_drift(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return note rows whose tags changed after the plan was generated."""
    conflicts = []
    for row in plan.get("items", []):
        path = row.get("note_path")
        if not path or not os.path.isfile(path):
            continue
        content = Path(path).read_text(encoding="utf-8")
        parsed = _frontmatter(content)
        if parsed is None:
            continue
        yaml_text, _body = parsed
        current = yaml.safe_load(yaml_text) or {}
        current_tags = current.get("tags") if isinstance(current.get("tags"), list) else []
        if sorted(current_tags) != sorted(row.get("before_obsidian", [])):
            conflicts.append({"path": path, "problem": "obsidian_tags", "current": sorted(current_tags), "planned_before": row.get("before_obsidian", [])})
    return conflicts

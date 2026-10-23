#!/usr/bin/env python3
"""Plan, apply, and verify reusable paper tag governance."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from ..config import REPO_ROOT, ZoteroConfig
from ..tag_governance import (
    apply_obsidian_plan,
    apply_zotero_plan,
    build_plan,
    check_obsidian_drift,
    check_zotero_drift,
)


def Register(subparsers) -> None:
    parser = subparsers.add_parser(
        "tag-governance",
        help="Plan, apply, and verify unified Zotero/Obsidian paper tags",
    )
    actions = parser.add_subparsers(dest="tag_action", required=True)

    plan = actions.add_parser("plan", help="Generate a JSON tag plan without writing")
    plan.add_argument("--output", "-o", required=True, help="Plan JSON output path")
    plan.add_argument("--json", action="store_true", help="Print summary JSON")
    plan.set_defaults(func=_RunPlan)

    apply = actions.add_parser("apply", help="Apply a previously reviewed plan")
    apply.add_argument("--plan", required=True, help="Plan JSON path")
    apply.add_argument("--zotero", action="store_true", help="Apply Zotero side")
    apply.add_argument("--obsidian", action="store_true", help="Apply Obsidian side")
    apply.add_argument("--force", action="store_true", help="Ignore before-tag drift")
    apply.add_argument("--json", action="store_true", help="Print result JSON")
    apply.set_defaults(func=_RunApply)

    verify = actions.add_parser("verify", help="Verify current state against a plan")
    verify.add_argument("--plan", required=True, help="Plan JSON path")
    verify.add_argument("--json", action="store_true", help="Print result JSON")
    verify.set_defaults(func=_RunVerify)


def _taxonomy_path() -> str:
    return os.path.join(REPO_ROOT, "shared", "config", "tag-taxonomy-rules.yaml")


def _load_plan(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _summary(plan: dict) -> dict:
    rows = plan.get("items", [])
    return {
        "schema_version": plan.get("schema_version"),
        "generated_at": plan.get("generated_at"),
        "items": len(rows),
        "notes": sum(bool(row.get("note_path")) for row in rows),
        "zotero_changes": sum(
            sorted(row.get("before_zotero", [])) != sorted(row.get("after", []))
            for row in rows
        ),
        "obsidian_changes": sum(
            bool(row.get("note_path"))
            and sorted(row.get("before_obsidian", [])) != sorted(row.get("after", []))
            for row in rows
        ),
        "unknown_tags": sorted({tag for row in rows for tag in row.get("unknown", [])}),
        "dropped_noise": sorted(
            {tag for row in rows for tag in row.get("dropped_noise", [])}
        ),
    }


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _RunPlan(args, config: ZoteroConfig) -> int:
    plan = build_plan(config, _taxonomy_path())
    output = os.path.abspath(os.path.expanduser(args.output))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    payload = _summary(plan)
    payload["plan"] = output
    _emit(payload, args.json)
    return 0


def _RunApply(args, config: ZoteroConfig) -> int:
    plan = _load_plan(args.plan)
    rows = plan.get("items", [])
    unknown = sorted({tag for row in rows for tag in row.get("unknown", [])})
    if unknown:
        print(
            "[ERROR] Plan contains non-canonical tags. Update the taxonomy or edit the plan explicitly:\n"
            + "\n".join(f"  - {tag}" for tag in unknown),
            file=sys.stderr,
        )
        return 1

    dropped = sorted({tag for row in rows for tag in row.get("dropped_noise", [])})
    if dropped:
        print(
            "[OK] Connector noise will be dropped (not blocking): "
            + ", ".join(dropped),
            file=sys.stderr,
        )

    apply_zotero = args.zotero
    apply_obsidian = args.obsidian
    if not apply_zotero and not apply_obsidian:
        apply_zotero = apply_obsidian = True

    drift = []
    if not args.force:
        if apply_zotero:
            drift.extend(check_zotero_drift(config, plan))
        if apply_obsidian:
            drift.extend(check_obsidian_drift(plan))
    if drift:
        _emit({"pass": False, "problem": "plan_drift", "conflicts": drift}, args.json)
        return 1

    backup_path = os.path.abspath(args.plan) + ".before.json"
    with open(backup_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "plan": os.path.abspath(args.plan),
                "items": [
                    {
                        "key": row.get("key"),
                        "note_path": row.get("note_path"),
                        "before_zotero": row.get("before_zotero", []),
                        "before_obsidian": row.get("before_obsidian", []),
                    }
                    for row in rows
                ],
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")

    result: dict = {
        "plan": os.path.abspath(args.plan),
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "backup": backup_path,
        "zotero": None,
        "obsidian": None,
    }
    if apply_obsidian:
        result["obsidian"] = apply_obsidian_plan(plan, force=args.force)
    if apply_zotero:
        result["zotero"] = apply_zotero_plan(plan, config, force=args.force)

    _emit(result, args.json)
    return 0


def _RunVerify(args, config: ZoteroConfig) -> int:
    expected = _load_plan(args.plan)
    current = build_plan(config, _taxonomy_path())
    expected_by_key = {row["key"]: row for row in expected.get("items", [])}
    current_by_key = {row["key"]: row for row in current.get("items", [])}
    diffs = []
    for key, expected_row in expected_by_key.items():
        current_row = current_by_key.get(key)
        if current_row is None:
            diffs.append({"key": key, "problem": "missing"})
            continue
        if sorted(current_row.get("before_zotero", [])) != sorted(expected_row.get("after", [])):
            diffs.append({"key": key, "problem": "zotero_tags", "current": current_row.get("before_zotero", []), "expected": expected_row.get("after", [])})
        if expected_row.get("note_path") and sorted(current_row.get("before_obsidian", [])) != sorted(expected_row.get("after", [])):
            diffs.append({"key": key, "problem": "obsidian_tags", "current": current_row.get("before_obsidian", []), "expected": expected_row.get("after", [])})
    result = {"plan": os.path.abspath(args.plan), "checked": len(expected_by_key), "diffs": diffs, "pass": not diffs}
    _emit(result, args.json)
    return 0 if not diffs else 1

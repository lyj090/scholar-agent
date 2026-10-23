#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_prepare_triage.py — Batch fetch Inbox items with auto-tag suggestions.

One command replaces N per-item metadata calls. Outputs structured JSON
with suggested tags and folders based on keyword matching.

NOT responsible for: final tag decisions (LLM does that), Zotero writes.
"""

from __future__ import annotations

import json
import re
import sys

from ..api import ZoteroAPI, ZoteroAPIError, normalize_key
from ..config import ZoteroConfig

# ─── Constants ────────────────────────────────────────────────────────────────────────────────────────────────────────

kDefaultInboxFolder = "90_Inbox"
kTagNamespaceOrder = {
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

# Inline keyword rules (subset, for when YAML not available)
kKeywordRules = (
    (re.compile(r"manipulation|grasp|dexterous|hand", re.IGNORECASE), ("topic/manipulation",), "Manipulation"),
    (re.compile(r"locomotion|walking|legged|bipedal|quadruped|humanoid", re.IGNORECASE), ("topic/locomotion",), "Locomotion_RL"),
    (re.compile(r"reinforcement learning|RL|policy gradient|PPO|SAC", re.IGNORECASE), ("method/reinforcement-learning",), ""),
    (re.compile(r"diffusion|score matching|denoising", re.IGNORECASE), ("method/diffusion",), ""),
    (re.compile(r"transformer|attention|ViT", re.IGNORECASE), ("method/transformer",), ""),
    (re.compile(r"SLAM|localization|visual odometry", re.IGNORECASE), ("topic/slam",), "SLAM"),
    (re.compile(r"event.?camera|event.?based|DVS", re.IGNORECASE), ("topic/event-vision",), "Event_Vision"),
    (re.compile(r"tactile|haptic|touch", re.IGNORECASE), ("topic/tactile-sensing",), "Hand_Tactile"),
    (re.compile(r"planning|task.?plan|motion.?plan", re.IGNORECASE), ("topic/planning",), ""),
    (re.compile(r"perception|detection|segmentation", re.IGNORECASE), ("topic/perception",), "Perception"),
    (re.compile(r"sim.?to.?real|domain.?adapt", re.IGNORECASE), ("method/sim-to-real",), ""),
    (re.compile(r"imitation|behavior.?cloning|learning from demo", re.IGNORECASE), ("method/imitation-learning",), ""),
    (re.compile(r"aerial|UAV|drone|quadrotor", re.IGNORECASE), ("topic/aerial-robotics",), "Aerial_Robotics"),
    (re.compile(r"MPC|model predictive control", re.IGNORECASE), ("method/model-predictive-control",), "Control"),
)


# ─── Public ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'prepare-triage' subcommand."""
    p = subparsers.add_parser("prepare-triage", help="Batch fetch Inbox items with tag suggestions")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
    p.add_argument("--collection", "-c", help="Inbox collection key (default: configured Inbox)")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute prepare-triage."""
    api = ZoteroAPI(config.api_url, config.user_id)
    try:
        collection_key = normalize_key(
            args.collection or config.inbox_collection_key,
            "Inbox collection key",
        )
    except ZoteroAPIError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1

    try:
        items = api.get_items(limit=args.limit, collection=collection_key, sort="dateAdded", direction="desc")
    except ZoteroAPIError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if not items:
        print("[OK] Inbox is empty.", file=sys.stderr)
        if args.json:
            print("[]")
        return 0

    collection_keys, collection_names = _LoadCollectionIndex(api)
    inbox_name = collection_names.get(collection_key.upper(), kDefaultInboxFolder)
    results = []
    for item in items:
        data = item.get("data", {})
        title = data.get("title", "").strip()
        abstract = data.get("abstractNote", "").strip()
        key = data.get("key", "")
        current_tags = [
            tag["tag"]
            for tag in data.get("tags", [])
            if isinstance(tag, dict) and tag.get("tag")
        ]

        # Generate suggestions
        suggested_tags, suggested_folder = _SuggestTags(title, abstract, data)
        if suggested_folder == kDefaultInboxFolder:
            suggested_folder = inbox_name

        results.append({
            "key": key,
            "title": title,
            "abstract": abstract[:300] if abstract else "",
            "current_tags": current_tags,
            "suggested_tags": suggested_tags,
            "suggested_folder": suggested_folder,
            "suggested_folder_key": collection_keys.get(suggested_folder.casefold(), ""),
        })

    if args.json:
        print(json.dumps(results, ensure_ascii=False, separators=(",", ":")))
    else:
        _PrintTable(results)

    return 0


# ─── Private ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _LoadCollectionIndex(api: ZoteroAPI) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve folder names to keys without relying on synced key constants."""
    try:
        rows = api.get_collections()
    except ZoteroAPIError as error:
        print(f"[WARN] Cannot resolve collection keys: {error}", file=sys.stderr)
        return {}, {}

    by_name: dict[str, str] = {}
    names_by_key: dict[str, str] = {}
    ambiguous: set[str] = set()
    for row in rows:
        data = row.get("data", {}) if isinstance(row, dict) else {}
        key = data.get("key") or (row.get("key") if isinstance(row, dict) else None)
        name = str(data.get("name") or "").strip()
        if not key or not name:
            continue
        normalized = name.casefold()
        names_by_key[str(key)] = name
        if normalized in ambiguous:
            continue
        if normalized in by_name and by_name[normalized] != key:
            ambiguous.add(normalized)
            by_name.pop(normalized, None)
            continue
        by_name[normalized] = str(key)

    return by_name, names_by_key


def _IsArxiv(item: dict) -> bool:
    """Return true only when item metadata identifies arXiv as its source."""
    values = (
        item.get("DOI", ""),
        item.get("libraryCatalog", ""),
        item.get("url", ""),
    )
    return any("arxiv" in str(value).casefold() for value in values)


def _SuggestTags(title: str, abstract: str, item: dict | None = None) -> tuple[list[str], str]:
    """Generate tag and folder suggestions from title + abstract keywords."""
    text = f"{title} {abstract}".lower()
    tags: set[str] = {"status/to-read"}
    if re.search(r"\bsurvey\b|\breview\b|\btutorial\b", text):
        tags.add("type/survey")
    elif re.search(r"\bbenchmark\b", text):
        tags.add("type/benchmark")
    elif re.search(r"\bdataset\b", text):
        tags.add("type/dataset")
    else:
        tags.add("type/paper")
    if item and _IsArxiv(item):
        tags.add("source/arxiv")
    folder = ""

    for pattern, tag_list, subfolder in kKeywordRules:
        if pattern.search(text):
            tags.update(tag_list)
            if subfolder and not folder:
                folder = subfolder

    if not folder:
        folder = kDefaultInboxFolder  # Keep in inbox if unclear

    return sorted(
        tags,
        key=lambda tag: (
            kTagNamespaceOrder.get(tag.split("/", 1)[0], 99),
            tag,
        ),
    ), folder


def _PrintTable(results: list[dict]) -> None:
    """Print human-readable triage table."""
    print(f"=== Inbox Triage Preview ({len(results)} items) ===\n")
    print(f"{'#':>2} {'Key':<10} {'Title':<45} {'Suggested Folder':<18} {'Tags'}")
    print("-" * 110)

    for i, r in enumerate(results, 1):
        title = r["title"][:43] + "…" if len(r["title"]) > 43 else r["title"]
        tags_str = ", ".join(r["suggested_tags"][:3])
        if len(r["suggested_tags"]) > 3:
            tags_str += f" +{len(r['suggested_tags'])-3}"
        print(f"{i:>2} {r['key']:<10} {title:<45} {r['suggested_folder']:<18} {tags_str}")

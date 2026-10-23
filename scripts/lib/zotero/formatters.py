#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
formatters.py — Output formatters for zotero_cli.

Provides human-readable table output and JSON serialization.
NOT responsible for: data fetching, business logic.
"""

from __future__ import annotations

import json


def format_items_table(items: list[dict]) -> str:
    """
    Format a list of Zotero items as a human-readable table.

    Columns: Key | Type | Year | Title (truncated)
    """
    if not items:
        return "(no items found)"

    lines = []
    lines.append(f"{'Key':<10} {'Type':<16} {'Year':<6} Title")
    lines.append("-" * 72)

    for item in items:
        data = item.get("data", {})
        meta = item.get("meta", {})

        key = data.get("key", "?")
        item_type = data.get("itemType", "?")
        title = data.get("title", "(untitled)")

        # Extract year from parsedDate or date field
        year = ""
        if meta.get("parsedDate"):
            year = meta["parsedDate"][:4]
        elif data.get("date"):
            year = data["date"][:4]

        # Truncate title
        if len(title) > 50:
            title = title[:47] + "..."

        lines.append(f"{key:<10} {item_type:<16} {year:<6} {title}")

    lines.append(f"\n({len(items)} items)")
    return "\n".join(lines)


def format_metadata(item: dict) -> str:
    """Format a single item's metadata for human reading."""
    data = item.get("data", {})

    lines = []
    lines.append(f"Title:    {data.get('title', '(untitled)')}")
    lines.append(f"Key:      {data.get('key', '?')}")
    lines.append(f"Type:     {data.get('itemType', '?')}")

    # Authors
    creators = data.get("creators", [])
    if creators:
        authors = [
            f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            for c in creators
            if c.get("creatorType") == "author"
        ]
        lines.append(f"Authors:  {', '.join(authors)}")

    # Publication info
    if data.get("date"):
        lines.append(f"Date:     {data['date']}")
    if data.get("publicationTitle"):
        lines.append(f"Venue:    {data['publicationTitle']}")
    if data.get("DOI"):
        lines.append(f"DOI:      {data['DOI']}")
    if data.get("url"):
        lines.append(f"URL:      {data['url']}")

    # Abstract (truncated)
    abstract = data.get("abstractNote", "")
    if abstract:
        if len(abstract) > 200:
            abstract = abstract[:197] + "..."
        lines.append(f"Abstract: {abstract}")

    # Tags
    tags = data.get("tags", [])
    if tags:
        tag_strs = [t.get("tag", "") for t in tags]
        lines.append(f"Tags:     {', '.join(tag_strs)}")

    # Collections
    collections = data.get("collections", [])
    if collections:
        lines.append(f"Collections: {', '.join(collections)}")

    return "\n".join(lines)


def format_figures(manifest: dict) -> str:
    """Format manifest.json figure/table listing."""
    if not manifest:
        return "(no manifest data)"

    sections = manifest.get("sections", [])
    if not sections:
        return "(no sections in manifest)"

    lines = []
    lines.append(f"{'#':<4} {'Section':<40} {'Figs':<6} {'Tables':<6} {'Eqs':<4}")
    lines.append("-" * 64)

    total_figs = 0
    total_tables = 0
    total_eqs = 0

    for i, sec in enumerate(sections):
        heading = sec.get("heading", "(unnamed)")
        if len(heading) > 38:
            heading = heading[:35] + "..."
        figs = len(sec.get("figures", []))
        tables = len(sec.get("tables", []))
        eqs = sec.get("equationCount", 0)
        total_figs += figs
        total_tables += tables
        total_eqs += eqs
        lines.append(f"{i:<4} {heading:<40} {figs:<6} {tables:<6} {eqs:<4}")

    lines.append("-" * 64)
    lines.append(f"{'Σ':<4} {'TOTAL':<40} {total_figs:<6} {total_tables:<6} {total_eqs:<4}")

    # List individual figures
    all_figs = []
    for sec in sections:
        for fig in sec.get("figures", []):
            all_figs.append(fig)

    if all_figs:
        lines.append(f"\nFigures ({len(all_figs)}):")
        for fig in all_figs:
            label = fig.get("label", "?")
            path = fig.get("path", "")
            caption = fig.get("caption", "")
            if len(caption) > 60:
                caption = caption[:57] + "..."
            lines.append(f"  {label}: {path}")
            if caption:
                lines.append(f"         {caption}")

    return "\n".join(lines)


def to_json(data, compact: bool = False) -> str:
    """Serialize data to JSON string."""
    if compact:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(data, ensure_ascii=False, indent=2)

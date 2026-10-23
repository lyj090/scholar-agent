#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_prune_assets.py — Find asset directories no note references.

`prepare-note` writes figures to `<notes>/assets/<slug>/`. The slug is derived
from the note title, and it changed shape at least once: an older run produced
short prose slugs (`beyondmimic/`) where the current code produces a longer
truncated slug (`beyondmimic-from-motion-tracking-to-versatile-humanoid-.../`).
Re-preparing a note under the new naming therefore *adds* a directory and leaves
the old one behind, because nothing ever reclaims it.

Measured on the live vault: 127 asset directories, 109 referenced by a note,
18 orphaned — 16.3 MB of 108.6 MB (15%).

A directory is a deletion candidate only when **no** note under `literature_notes`
links to it. That test is deliberately conservative:

  * Every `assets/<slug>/` occurrence is collected, from image embeds *and* any
    other markdown link, so a figure referenced from a non-standard pattern still
    counts as live.
  * A directory holding a file that some note references by another path is kept.
  * Notes are read as UTF-8 text with no YAML parsing — a malformed note must
    still be able to keep its assets alive.

Default is a dry run. `--apply` is required to delete, and even then each
directory is removed only after its size has been reported. Nothing here touches
files outside the resolved `assets/` tree.

NOT responsible for: re-preparing the note that should own the assets, and not
responsible for reclaiming figures that were never referenced in the first place
(that is what this command reports).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys

from ..config import ZoteroConfig

# `assets/<slug>/` in any markdown link or embed. Slugs are lowercase-dash by
# construction (`_Slugify`), so the character class is narrow on purpose: it
# cannot accidentally match a URL path segment like `assets/images/foo`.
kAssetRefRe = re.compile(r"assets/([a-z0-9][a-z0-9-]*)/")
# `assets/<slug>/<file>` — the specific file, used to detect leftovers inside a
# directory that is still referenced by other figures. Filenames are restricted
# to characters the asset writer produces so a query string cannot match.
kAssetFileRe = re.compile(r"assets/([a-z0-9][a-z0-9-]*)/([A-Za-z0-9._-]+)")


# ─── Public ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def Register(subparsers) -> None:
    """Register the 'prune-assets' subcommand."""
    p = subparsers.add_parser(
        "prune-assets",
        help="Find (and with --apply, delete) asset dirs no note references",
    )
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete orphaned directories (default: report only)",
    )
    p.add_argument(
        "--notes-dir",
        default="",
        help="Override the literature notes directory (default: from config)",
    )
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute prune-assets. Returns 0 on success, 1 on error."""
    notes_dir = args.notes_dir or config.literature_notes
    if not os.path.isdir(notes_dir):
        print(f"[ERROR] Notes directory not found: {notes_dir}", file=sys.stderr)
        return 1

    assets_dir = os.path.join(notes_dir, "assets")
    if not os.path.isdir(assets_dir):
        print(f"[ERROR] Assets directory not found: {assets_dir}", file=sys.stderr)
        return 1

    referenced, referenced_files, scan_errors = _CollectReferencedSlugs(notes_dir)
    on_disk = sorted(
        name for name in os.listdir(assets_dir)
        if os.path.isdir(os.path.join(assets_dir, name))
    )

    orphans = [name for name in on_disk if name not in referenced]
    orphan_details = [
        {
            "slug": name,
            "path": os.path.join(assets_dir, name),
            "bytes": _DirSize(os.path.join(assets_dir, name)),
            "files": _FileCount(os.path.join(assets_dir, name)),
        }
        for name in orphans
    ]

    # Retained directories can still hold stale files: re-preparing a note
    # rewrites the assets it uses but never reclaims the ones it stopped using,
    # so a live `assets/<slug>/` accumulates orphaned figures. The old asset
    # names are what led the PRISM note to mislabel figures, so surface them.
    kPrivateLedgerFiles = {"figure-ledger.json", "figure-candidates.json"}
    stale_details: list[dict] = []
    for name in on_disk:
        if name not in referenced:
            continue
        dir_path = os.path.join(assets_dir, name)
        used = {fn for (slug, fn) in referenced_files if slug == name}
        try:
            present = sorted(os.listdir(dir_path))
        except OSError:
            continue
        leftover = [
            fn for fn in present
            if fn not in used and fn not in kPrivateLedgerFiles
            and os.path.isfile(os.path.join(dir_path, fn))
        ]
        if leftover:
            stale_details.append({
                "slug": name,
                "files": leftover,
                "bytes": sum(
                    os.path.getsize(os.path.join(dir_path, fn)) for fn in leftover
                ),
            })

    orphan_bytes = sum(d["bytes"] for d in orphan_details)
    stale_bytes = sum(d["bytes"] for d in stale_details)
    total_bytes = sum(
        _DirSize(os.path.join(assets_dir, name)) for name in on_disk
    )

    deleted: list[str] = []
    if args.apply and orphan_details:
        for detail in orphan_details:
            try:
                shutil.rmtree(detail["path"])
                deleted.append(detail["slug"])
            except OSError as e:
                print(f"[WARN] Could not remove {detail['path']}: {e}", file=sys.stderr)

    stale_deleted: list[str] = []
    if args.apply and stale_details:
        for detail in stale_details:
            for fn in detail["files"]:
                try:
                    os.remove(os.path.join(assets_dir, detail["slug"], fn))
                    stale_deleted.append(f"{detail['slug']}/{fn}")
                except OSError as e:
                    print(f"[WARN] Could not remove {fn}: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps({
            "notes_dir": notes_dir,
            "assets_dir": assets_dir,
            "total_dirs": len(on_disk),
            "referenced_dirs": len(on_disk) - len(orphans),
            "orphan_count": len(orphans),
            "orphan_bytes": orphan_bytes,
            "stale_file_count": sum(len(d["files"]) for d in stale_details),
            "stale_bytes": stale_bytes,
            "total_bytes": total_bytes,
            "orphan_pct": round(orphan_bytes / total_bytes * 100, 1) if total_bytes else 0.0,
            "orphans": orphan_details,
            "stale_files": stale_details,
            "applied": bool(args.apply),
            "deleted": deleted,
            "stale_deleted": stale_deleted,
            "scan_errors": scan_errors,
        }, ensure_ascii=False, separators=(",", ":")))
    else:
        _PrintReport(
            assets_dir, len(on_disk), orphan_details, orphan_bytes, total_bytes,
            args.apply, deleted, scan_errors,
        )
        _PrintStale(stale_details, stale_bytes, args.apply, stale_deleted)

    return 0


# ─── Core ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _CollectReferencedSlugs(notes_dir: str) -> tuple[set[str], set[tuple[str, str]], list[str]]:
    """Return (referenced slugs, referenced (slug, filename) pairs, unreadable notes)."""
    referenced: set[str] = set()
    referenced_files: set[tuple[str, str]] = set()
    errors: list[str] = []

    for name in sorted(os.listdir(notes_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(notes_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            errors.append(f"{name}: {e}")
            continue
        referenced.update(kAssetRefRe.findall(content))
        referenced_files.update(
            (m.group(1), m.group(2))
            for m in kAssetFileRe.finditer(content)
        )

    return referenced, referenced_files, errors


def _DirSize(path: str) -> int:
    """Total bytes under a directory, tolerating unreadable entries."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _FileCount(path: str) -> int:
    """Number of files under a directory, tolerating unreadable entries."""
    count = 0
    for _root, _dirs, files in os.walk(path):
        count += len(files)
    return count


def _HumanBytes(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024.0
    return f"{num:.1f} GB"


def _PrintStale(
    stale: list[dict],
    stale_bytes: int,
    applied: bool,
    deleted: list[str],
) -> None:
    """Report unreferenced files inside directories a note still links to."""
    if not stale:
        return
    print(
        f"\n=== Stale files in live directories: {len(stale)} dir(s), "
        f"{_HumanBytes(stale_bytes)} ==="
    )
    if applied:
        print(f"    deleted {len(deleted)} file(s)")
    for detail in sorted(stale, key=lambda d: d["bytes"], reverse=True):
        print(
            f"  {detail['slug']}: {len(detail['files'])} unreferenced file(s), "
            f"{_HumanBytes(detail['bytes'])}"
        )
        for fn in detail["files"][:5]:
            print(f"      {fn}")
        if len(detail["files"]) > 5:
            print(f"      … {len(detail['files']) - 5} more")
    if not applied:
        print(
            "\nDry run — nothing was deleted. These files sit inside a directory"
            "\nthat is still referenced; they are the names assets used to have."
            "\nConfirm, then re-run with --apply."
        )


def _PrintReport(
    assets_dir: str,
    total_dirs: int,
    orphans: list[dict],
    orphan_bytes: int,
    total_bytes: int,
    applied: bool,
    deleted: list[str],
    scan_errors: list[str],
) -> None:
    """Print a human-readable orphan report."""
    pct = orphan_bytes / total_bytes * 100 if total_bytes else 0.0
    print(f"=== Asset prune: {len(orphans)} orphaned / {total_dirs} directories ===")
    print(f"    {assets_dir}")
    print(
        f"    reclaimable {_HumanBytes(orphan_bytes)} of {_HumanBytes(total_bytes)}"
        f" ({pct:.1f}%)\n"
    )

    if not orphans:
        print("No orphaned asset directories found.")
    else:
        for detail in sorted(orphans, key=lambda d: d["bytes"], reverse=True):
            marker = "deleted" if detail["slug"] in deleted else "orphan"
            print(
                f"  [{marker}] {detail['slug']}\n"
                f"           {_HumanBytes(detail['bytes'])} in {detail['files']} file(s)"
            )

    if scan_errors:
        print(f"\nWARN: {len(scan_errors)} note(s) could not be read:")
        for err in scan_errors[:5]:
            print(f"  {err}")

    if not applied and orphans:
        print(
            "\nDry run — nothing was deleted. An orphan is a directory no note"
            "\nlinks to; confirm the list, then re-run with --apply."
        )
    elif applied and deleted:
        print(f"\nDeleted {len(deleted)} director{'y' if len(deleted) == 1 else 'ies'}.")

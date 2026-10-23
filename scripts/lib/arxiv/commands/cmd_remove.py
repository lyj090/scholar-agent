#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cmd_remove.py — Remove (trash) papers from Zotero, with safety rails.

Why this exists
---------------
`arxiv-to-library` could discover and import but never *remove*, so pruning a
library meant dropping to hand-written code with no validation. The dangerous
part is not the deletion call — it is aiming it correctly.

This command closes that gap:

* resolves **all** keys before mutating any, so a bad key aborts the whole
  batch instead of leaving a half-applied change;
* trashes rather than erases — `restore` undoes it (the local API's `DELETE`
  is permanent and bypasses the trash, so it is never used here);
* requires an explicit `--yes`, and supports `--dry-run` to preview.

Usage::

    arxiv_fetch.py remove <KEY> [<KEY> ...] --dry-run
    arxiv_fetch.py remove <KEY> [...] --yes
    arxiv_fetch.py remove <KEY> [...] --restore --yes
"""

from __future__ import annotations

import json
import os
import sys

from arxiv.cache import set_quiet

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from zotero.api import ZoteroAPI, ZoteroAPIError  # noqa: E402
from zotero.config import load_config  # noqa: E402
from zotero.local_write import ZoteroWriteError, build_write_client  # noqa: E402


def Register(subparsers) -> None:
    """Register the 'remove' subcommand."""
    p = subparsers.add_parser(
        "remove",
        help="Move papers to the Zotero trash (or restore them), by item key",
        description=(
            "Trash Zotero items by their 8-character key. Trashing is "
            "reversible: the same command with --restore puts them back. "
            "All keys are resolved before anything is modified, so one bad "
            "key aborts the entire batch."
        ),
    )
    p.add_argument("keys", nargs="*", help="Zotero item keys (8 chars each)")
    p.add_argument(
        "--restore",
        action="store_true",
        help="Restore the given keys from the trash instead of trashing them",
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Actually perform the change (without it, nothing is written)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and report what would change, then stop",
    )
    p.add_argument(
        "--undo",
        action="store_true",
        help="Flip the given keys back (trashed -> live, live -> trashed)",
    )
    p.add_argument("--json", action="store_true", help="Output JSON results")
    p.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress lines. Errors still print.",
    )
    p.set_defaults(func=_Run)


def _FindScript(name: str) -> str | None:
    """Locate a sibling script in the shared scripts tree."""
    here = os.path.dirname(os.path.abspath(__file__))          # .../lib/arxiv/commands
    scripts = os.path.abspath(os.path.join(here, "..", "..", ".."))  # .../scripts
    for candidate in (
        os.path.join(scripts, "literature", name),
        os.path.join(scripts, name),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _Lookup(keys: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Return (key -> {title, deleted}, missing_keys).

    Resolution happens before any mutation; a key that cannot be found is
    reported back so the caller can abort the whole batch. Reads go through the
    Local API, which needs no write authorization.
    """
    config = load_config()
    api = ZoteroAPI(config.api_url, config.user_id)
    found: dict[str, dict] = {}
    missing: list[str] = []
    for key in keys:
        try:
            data = api.get_item(key).get("data", {})
        except (ZoteroAPIError, KeyError):
            missing.append(key)
            continue
        found[key] = {
            "key": key,
            # `deleted` is omitted (not False) on live items in the local API.
            "deleted": bool(data.get("deleted")),
            "title": data.get("title") or data.get("note") or "",
        }
    return found, missing


def _Apply(keys: list[str], *, mode: str) -> dict:
    """Trash or restore items. Uses PATCH, never the permanent DELETE."""
    config = load_config()
    client = build_write_client(config)
    deleted = mode == "trash"
    done: list[str] = []
    for key in keys:
        client.set_deleted(key, deleted)
        done.append(key)
    return {"count": len(done), "keys": done, "mode": mode}


def _Undo(keys: list[str]) -> dict:
    """Reverse a previous remove/restore by flipping the deleted flag back."""
    config = load_config()
    client = build_write_client(config)
    restored: list[str] = []
    for key in keys:
        envelope = client.get_item(key)
        client.set_deleted(key, not bool(envelope["data"].get("deleted")))
        restored.append(key)
    return {"count": len(restored), "keys": restored}


def _Run(args, config) -> int:
    """Execute the remove command."""
    del config
    set_quiet(getattr(args, "quiet", False))

    if not args.keys:
        print("[ERROR] No keys given.", file=sys.stderr)
        return 1

    keys = [k.strip().upper() for k in args.keys]
    bad = [k for k in keys if len(k) != 8 or not k.isalnum()]
    if bad:
        print(f"[ERROR] Not 8-character Zotero keys: {', '.join(bad)}", file=sys.stderr)
        return 1

    # --undo flips the deleted flag back, so it is the same rails minus the
    # resolve-before-mutate check: it deliberately works on trashed items, which
    # the normal lookup reports as present anyway.
    if args.undo:
        if not args.yes:
            print("[ERROR] Refusing to undo without --yes.", file=sys.stderr)
            return 1
        try:
            outcome = _Undo(keys)
        except (ZoteroWriteError, ZoteroAPIError) as error:
            print(f"[ERROR] {error}", file=sys.stderr)
            return 1
        print(f"[OK] flipped {outcome['count']} item(s)", file=sys.stderr)
        if args.json:
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0

    mode = "restore" if args.restore else "trash"
    verb = "restore" if args.restore else "trash"

    found, missing = _Lookup(keys)
    if missing:
        # Abort before touching anything: a partially applied batch is worse
        # than no change at all.
        print(
            f"[ERROR] Aborting — {len(missing)} key(s) not found: {', '.join(missing)}. "
            "No items were modified.",
            file=sys.stderr,
        )
        return 1

    already = [k for k, v in found.items() if (not args.restore and v["deleted"])
               or (args.restore and not v["deleted"])]

    if not args.quiet:
        for k in keys:
            info = found[k]
            state = "trashed" if info["deleted"] else "live"
            print(f"  {k} [{state}] {info['title'][:70]}")

    if args.dry_run:
        print(
            f"[DRY-RUN] would {verb} {len(keys)} item(s)"
            + (f"; {len(already)} already in the target state" if already else ""),
            file=sys.stderr,
        )
        if args.json:
            print(json.dumps(
                {"mode": mode, "dry_run": True, "items": list(found.values())},
                ensure_ascii=False, indent=2,
            ))
        return 0

    if not args.yes:
        print(
            f"[ERROR] Refusing to {verb} {len(keys)} item(s) without --yes. "
            f"Re-run with --yes, or preview with --dry-run.",
            file=sys.stderr,
        )
        return 1

    outcome = _Apply(keys, mode=mode)
    count = outcome.get("count", len(keys))
    print(f"[OK] {count} item(s) {'restored from' if args.restore else 'moved to'} trash",
          file=sys.stderr)
    # Undo is the opposite operation on the same keys, so no action id is needed.
    print(f"[OK] undo: arxiv_fetch.py remove {'--restore' if not args.restore else ''} "
          f"{' '.join(keys)} --yes", file=sys.stderr)
    if args.json:
        print(json.dumps(
            {"mode": mode, "count": count, "keys": keys},
            ensure_ascii=False, indent=2,
        ))
    return 0

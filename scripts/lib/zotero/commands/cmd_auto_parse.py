#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cmd_auto_parse.py — keep MinerU caches in step with the library, without a plugin.

Why this exists
---------------
The llm-for-zotero plugin parsed PDFs automatically by registering a Zotero
``Notifier`` observer: an in-process hook that fires on every item add/modify.
That is the one genuinely useful thing this repo could not reproduce over HTTP,
and it is the reason "just delete the plugin" was not obviously safe.

This command reproduces the *effect* by polling instead of hooking: scan the
library for PDFs with no cache, newest first, and parse up to a budget. Run it
from cron. Polling is the right trade here because

  * the work (a MinerU API call) takes seconds to minutes, so sub-second latency
    from a notifier is worthless;
  * it needs no code inside Zotero's process, which is the whole point of having
    removed the plugin;
  * it is observable and bounded — you can see exactly what it would do.

It shares ``mineru-parse``'s writer, so caches are byte-compatible with the
plugin's: both are keyed ``<attachment_id>/full.md`` + ``manifest.json``, which
is also the only signal the plugin uses to decide "already parsed". The two
cannot fight: whichever runs first produces a cache the other skips.

By default only **research-paper item types** are parsed. That default is
deliberate — unattended parsing spends real credit, and this library's uncached
PDFs were 54 ``book`` to 3 ``journalArticle``. Use ``--all`` to widen it.

NOT responsible for: MinerU API details, cache format (see mineru_client /
mineru_cache_writer), or scheduling (see scripts/deploy/schedule.sh).
"""

from __future__ import annotations

import json
import os
import re
import sys

from ..config import ZoteroConfig
from ..zotero_db import ZoteroDatabase

#: Item types that are research papers — the only things parsed by default.
#:
#: Item *type* is a far better signal than the filename: it is curated metadata
#: rather than a guess. Measured on this library, the uncached PDFs were 54
#: ``book`` and only 3 ``journalArticle``, so filtering by type removes the
#: entire textbook pile without a brittle list of title substrings (an earlier
#: filename-based attempt still queued 23 textbooks).
PAPER_ITEM_TYPES = (
    "journalArticle",
    "conferencePaper",
    "preprint",
    "report",
    "thesis",
    "manuscript",
)

#: Filename/title substrings skipped even for paper-typed items — course notes
#: and exercise sheets that were typed as a paper. Kept short on purpose: a long
#: list would be overfitting to one library.
DEFAULT_EXCLUDE = (
    "讲义", "作业", "习题", "试卷", "课程",
    "homework", "assignment", "problem set", "pset", "syllabus", "slides",
)

#: Where permanently-failing PDFs are remembered, so an unattended run does not
#: retry them every hour. Kept next to the action ledger (machine-local, ignored
#: by git) rather than in the cache dir, which is Zotero's to own.
FAILURE_STATE_FILE = "mineru-sync-failures.json"

#: A PDF that fails this many times is skipped until the file changes. Three
#: attempts tolerates transient network/API errors while stopping the endless
#: retry of something the service genuinely rejects (a scanned image with no
#: text layer, for instance) — which would otherwise cost an API call per hour
#: forever.
MAX_ATTEMPTS = 3


def _failure_state_path() -> str:
    """Repo ``data/`` dir, matching where the action ledger lives.

    __file__ is ``scripts/lib/zotero/commands/cmd_auto_parse.py``, so the repo
    root is five levels up, not four.
    """
    here = os.path.abspath(__file__)
    repo_root = os.path.dirname(  # scripts/lib/zotero/commands -> lib/zotero -> lib -> scripts -> repo
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
    )
    return os.path.join(repo_root, "data", FAILURE_STATE_FILE)


def _load_failures() -> dict:
    try:
        with open(_failure_state_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_failures(state: dict) -> None:
    path = _failure_state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1)
        # Atomic replace: a half-written state file would silently reset the
        # retry counters, which is exactly the bug this guards against.
        os.replace(tmp, path)
    except OSError:
        pass


def _signature(path: str) -> str:
    """Identify a PDF by size+mtime, so replacing the file resets its counters."""
    try:
        stat = os.stat(path)
        return f"{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        return ""


def Register(subparsers) -> None:
    p = subparsers.add_parser(
        "mineru-sync",
        help="Parse MinerU caches for library PDFs that lack one (cron-friendly)",
    )
    p.add_argument(
        "--limit", type=int, default=5,
        help="Max PDFs to parse this run (default: 5, keeps a cron tick bounded)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="List what would be parsed, parse nothing",
    )
    p.add_argument(
        "--all", action="store_true",
        help="Also parse non-paper item types (books/textbooks), not just papers",
    )
    p.add_argument(
        "--exclude", action="append", default=None,
        help="Extra filename substring to skip (repeatable)",
    )
    p.add_argument("--mode", choices=["cloud", "local"], default="cloud")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", "-q", action="store_true")
    p.set_defaults(func=_Run)


def _excluded(title: str, pdf_path: str, patterns: tuple[str, ...]) -> str | None:
    """Return the pattern that excludes this PDF, or None.

    Short patterns (<=3 chars) match on word boundaries only: a bare substring
    test for ``hw`` would also fire inside a legitimate title such as
    "Shweta et al.", silently skipping a real paper.
    """
    haystack = f"{title} {os.path.basename(pdf_path or '')}".casefold()
    words = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", haystack))
    for pattern in patterns:
        needle = pattern.casefold().strip()
        if not needle:
            continue
        if len(needle) <= 3 and needle.isascii():
            if needle in words:
                return pattern
        elif needle in haystack:
            return pattern
    return None


def _pending(config: ZoteroConfig, *, include_all: bool, extra: list[str]):
    """Return ``(pending, skipped)`` — PDFs without a cache, newest first."""
    from ..mineru_catalog import MineruCatalog
    from ..api import ZoteroAPI

    catalog = MineruCatalog(
        config.mineru_cache, ZoteroAPI(config.api_url, config.user_id), config
    )
    cached_ids = {record.cache_id for record in catalog.records()}
    patterns = DEFAULT_EXCLUDE + tuple(extra or ())
    failures = _load_failures()

    pending: list[dict] = []
    skipped: list[dict] = []
    with ZoteroDatabase(config.data_dir) as database:
        for row in database.all_pdf_attachments():
            path = row["pdf_path"]
            # A missing file cannot be parsed (linked attachment, or moved).
            if not path or not os.path.isfile(path):
                continue
            if row["item_id"] in cached_ids:
                continue

            # Report the more fundamental reason first. Item type is curated
            # metadata, so it beats guessing from the filename.
            if not include_all and row.get("item_type") not in PAPER_ITEM_TYPES:
                skipped.append({
                    **row,
                    "reason": f"item type {row.get('item_type') or '?'} is not a paper",
                })
                continue

            hit = _excluded(row["title"], path, patterns)
            if hit:
                skipped.append({**row, "reason": f"excluded by {hit!r}"})
                continue

            # Give up on a PDF that keeps failing, but only while the file is
            # unchanged: replacing it is a clear signal to try again.
            record = failures.get(row["attachment_key"]) or {}
            if (
                record.get("signature") == _signature(path)
                and int(record.get("attempts") or 0) >= MAX_ATTEMPTS
            ):
                skipped.append({
                    **row,
                    "reason": f"failed {record['attempts']}x, skipping "
                              f"({str(record.get('error'))[:60]})",
                })
                continue

            pending.append(row)
    return pending, skipped


def _Run(args, config: ZoteroConfig) -> int:
    try:
        pending, skipped = _pending(
            config, include_all=args.all, extra=args.exclude or []
        )
    except Exception as error:
        print(f"[ERROR] cannot scan library: {error}", file=sys.stderr)
        return 1

    limit = max(0, args.limit)
    batch = pending[:limit]

    if args.dry_run:
        payload = {
            "pending": len(pending),
            "wouldParse": [p["attachment_key"] for p in batch],
            "excluded": len(skipped),
            "dryRun": True,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"pending={len(pending)} excluded={len(skipped)} would_parse={len(batch)}")
            for row in batch:
                print(f"  {row['attachment_key']}  {row['title'][:60]}")
            if skipped and not args.quiet:
                for row in skipped[:5]:
                    print(f"  [skip] {row['title'][:50]} — {row['reason']}", file=sys.stderr)
        return 0

    if not batch:
        if args.json:
            print(json.dumps({"parsed": 0, "pending": len(pending), "excluded": len(skipped)}))
        elif not args.quiet:
            print(f"[OK] nothing to do (pending={len(pending)} excluded={len(skipped)})")
        return 0

    from .cmd_mineru_parse import ParseOne  # local import: avoids a cycle

    parsed: list[dict] = []
    failed: list[dict] = []
    failures = _load_failures()
    for row in batch:
        key = row["attachment_key"]
        if not args.quiet:
            print(f"[mineru-sync] parsing {key} — {row['title'][:60]}", file=sys.stderr)
        try:
            result = ParseOne(
                config,
                reference=key,
                pdf_path=row["pdf_path"],
                attachment_id=row["item_id"],
                attachment_key=key,
                parent_key=row["parent_item_key"],
                mode=args.mode,
                overwrite=False,
            )
            parsed.append({"key": key, "cacheId": result.get("cache_id")})
            failures.pop(key, None)
        except FileExistsError:
            # Another run (or the plugin) won the race — not an error.
            parsed.append({"key": key, "skipped": "already cached"})
            failures.pop(key, None)
        except Exception as error:
            message = str(error)[:200]
            failed.append({"key": key, "error": message})
            record = failures.get(key) or {"attempts": 0}
            failures[key] = {
                "attempts": int(record.get("attempts") or 0) + 1,
                "signature": _signature(row["pdf_path"]),
                "error": message,
                "lastAttempt": row.get("date_added") or "",
            }

    _save_failures(failures)

    payload = {
        "parsed": len(parsed),
        "failed": failed,
        "remaining": max(0, len(pending) - len(parsed)),
        "excluded": len(skipped),
        "results": parsed,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    elif not args.quiet:
        print(
            f"[OK] parsed {len(parsed)}/{len(batch)} "
            f"(remaining={payload['remaining']} excluded={len(skipped)})"
        )
        for item in failed:
            print(f"  [WARN] {item['key']}: {item['error']}", file=sys.stderr)

    # A partial failure is not a crash: cron should retry next tick, not alarm.
    return 0

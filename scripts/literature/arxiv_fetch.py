#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arxiv_fetch.py — Paper discovery + Zotero import + Publish.

Three core commands:
  find    — Discover papers (time × scope, all combinations)
  import  — Import specific papers to Zotero (by ID, URL, or title)
  publish — Publish papers to output formats (README, GitHub Pages, WeChat)

Usage:
    # Today's new papers, ranked by your library (auto profile)
    python3 scripts/literature/arxiv_fetch.py find --profile

    # Today + specific field
    python3 scripts/literature/arxiv_fetch.py find --field robotics

    # Classic high-citation papers
    python3 scripts/literature/arxiv_fetch.py find --period classic --keywords "manipulation" --field robotics

    # Recent papers by keyword
    python3 scripts/literature/arxiv_fetch.py find --period recent --keywords "event camera"

    # Direct import
    python3 scripts/literature/arxiv_fetch.py import 2505.12345
    python3 scripts/literature/arxiv_fetch.py import https://arxiv.org/abs/2505.12345

    # Publish papers
    python3 scripts/literature/arxiv_fetch.py publish --cat cs.RO,cs.CV
    python3 scripts/literature/arxiv_fetch.py publish --file papers.json --output-dir ./output

Exit codes: 0=OK, 1=error, 2=not found.

Dependencies: numpy (pre-installed).
"""

import argparse
import contextlib
import io
import os
import sys

# Ensure lib/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from arxiv.cache import set_quiet
from arxiv.commands import cmd_find, cmd_import, cmd_publish, cmd_remove
from zotero.config import load_config


def Main() -> int:
    # Paper titles are UTF-8 and often contain non-ASCII (ellipsis, accents,
    # CJK). On a Windows console or a C locale, printing them raises from inside
    # the table formatter after the search has already succeeded.
    from paths_common import ensure_utf8_stdio

    ensure_utf8_stdio()

    parser = argparse.ArgumentParser(
        prog="arxiv_fetch",
        description="Paper discovery (find) + Zotero import + Publish.",
    )
    parser.add_argument("--config", help="Path to paths.local.yaml (auto-detected if omitted)")
    # Distinct dest: a subcommand --quiet has default False and would otherwise
    # overwrite this, dropping `--quiet` placed before the subcommand.
    parser.add_argument(
        "--quiet", "-q",
        dest="quiet_global",
        action="store_true",
        help="Suppress progress lines for any subcommand. Errors still print.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Core commands
    cmd_find.Register(subparsers)
    cmd_import.Register(subparsers)
    cmd_publish.Register(subparsers)
    cmd_remove.Register(subparsers)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    set_quiet(getattr(args, "quiet_global", False) or getattr(args, "quiet", False))

    # Load config (needed for Zotero integration; graceful fallback if missing).
    # load_config() prints an ASCII error block and raises SystemExit when a
    # machine has no library. That block describes Zotero setup, which `find`
    # does not need and cannot act on — so it is captured here and replaced by
    # one line naming the only consequence.
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            config = load_config(args.config)
    except SystemExit:
        config = None
        if args.command == "find":
            print(
                "[WARN] no library configured; profile/dedup unavailable "
                "(python3 scripts/deploy/setup_paths.py --apply)",
                file=sys.stderr,
            )

    return args.func(args, config)


if __name__ == "__main__":
    sys.exit(Main())

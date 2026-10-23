#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_publish.py — 'publish' subcommand: publish papers to output formats.

Fetches papers and generates README, GitHub Pages, WeChat formats.
Supports JSON persistence and optional push to separate repo.
"""

from __future__ import annotations

import sys

from ..config import LoadConfig
from ..publisher import Publisher
from arxiv.cache import note, set_quiet


def Register(subparsers) -> None:
    """Register the 'publish' subcommand."""
    p = subparsers.add_parser(
        "publish",
        help="Publish papers to output formats (README, GitHub Pages, WeChat)",
    )

    # Paper source
    source = p.add_argument_group("paper source (choose one)")
    source.add_argument(
        "--cat",
        help="arXiv categories, comma-separated (e.g., cs.RO,cs.CV)",
    )
    source.add_argument(
        "--file",
        help="Load papers from JSON file (instead of fetching)",
    )
    source.add_argument(
        "--use-find",
        action="store_true",
        help="Use find command results (requires --period and scope args)",
    )

    # Output options
    output = p.add_argument_group("output options")
    output.add_argument(
        "--output-dir", "-o",
        default=".",
        help="Output directory (default: current directory)",
    )
    output.add_argument(
        "--formats",
        nargs="+",
        choices=["readme", "pages", "wechat"],
        help="Output formats to generate (default: all enabled in config)",
    )
    output.add_argument(
        "--no-json",
        action="store_true",
        help="Skip JSON persistence",
    )

    # Behavior options
    behavior = p.add_argument_group("behavior options")
    behavior.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually save files, just show what would be done",
    )
    behavior.add_argument(
        "--config",
        help="Path to config.yaml (auto-detected if omitted)",
    )
    behavior.add_argument(
        "--push",
        action="store_true",
        help="Push to separate publishing repo (requires repo config)",
    )
    behavior.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress lines. Errors still print.",
    )

    p.set_defaults(func=_Run)


def _Run(args, config) -> int:
    """Execute the publish command."""
    set_quiet(getattr(args, "quiet", False))
    # Load config (always load arXiv config, ignore Zotero config)
    if args.config:
        from ..config import ConfigLoader
        cfg = ConfigLoader(args.config)
        cfg.Load()
    else:
        # Always load arXiv config (scripts/lib/arxiv/config.yaml)
        cfg = LoadConfig()

    # Determine paper source
    papers = None

    if args.file:
        # Load from JSON file
        import json
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            from ..protocol import Paper
            papers = [Paper(**d) for d in data]
            note(f"[publish] Loaded {len(papers)} papers from {args.file}")
        except Exception as e:
            print(f"[ERROR] Failed to load {args.file}: {e}", file=sys.stderr)
            return 1

    elif args.use_find:
        # TODO: Implement using find command results
        print("[ERROR] --use-find not yet implemented.", file=sys.stderr)
        return 1

    else:
        # Fetch from arXiv
        categories = ["cs.RO", "cs.CV", "cs.LG"]
        if args.cat:
            categories = [c.strip() for c in args.cat.split(",")]

    # Publish
    publisher = Publisher(cfg)
    results = publisher.Publish(
        papers=papers,
        categories=None if papers else categories,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )

    # Report results
    note("[publish] Results:")
    for fmt, success in results.items():
        status = "OK" if success else "FAILED"
        note(f"  - {fmt}: {status}")

    # Push to repo if requested
    if args.push and not args.dry_run:
        _PushToRepo(cfg)

    return 0 if all(results.values()) else 1


def _PushToRepo(config) -> bool:
    """Push published files to separate GitHub repo.

    Args:
        config: ConfigLoader instance.

    Returns:
        True if pushed successfully.
    """
    publish_settings = config.GetPublishSettings()
    repo_url = publish_settings.get("repo_url", "")
    branch = publish_settings.get("branch", "main")

    if not repo_url:
        note("[publish] No repo_url configured, skipping push.")
        return False

    import subprocess

    try:
        # Git add, commit, push
        subprocess.run(["git", "add", "."], check=True)
        date_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        commit_msg = publish_settings.get("commit_template", "Update papers: {date}").format(date=date_str)
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "push", repo_url, branch], check=True)
        note("[publish] Pushed to repo.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[publish] Failed to push: {e}", file=sys.stderr)
        return False

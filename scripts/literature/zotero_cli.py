#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zotero_cli.py — Terminal bridge for Zotero + MinerU.

Standalone CLI tool. No AI dependencies. Pipe-friendly output.
Designed to be called by humans or AI skills equally.

Usage:
    python3 scripts/literature/zotero_cli.py list --limit 5
    python3 scripts/literature/zotero_cli.py search "humanoid" --json
    python3 scripts/literature/zotero_cli.py metadata CMBCBW9Q --children
    python3 scripts/literature/zotero_cli.py mineru read CMBCBW9Q --section 0
    python3 scripts/literature/zotero_cli.py unprocessed --limit 10
    python3 scripts/literature/zotero_cli.py mineru list --query "stochastic" --json
    python3 scripts/literature/zotero_cli.py mineru outline PY5D7UP9 --json
    python3 scripts/literature/zotero_cli.py mineru read PY5D7UP9 --section "6.2"

Exit codes: 0=OK, 1=error, 2=not found.

Entry point: argparse setup, config load, command dispatch.
NOT responsible for: any business logic (delegated to lib/zotero/commands/).
"""

import argparse
import sys
import os

# Ensure lib/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from zotero.config import load_config
from zotero.api import ZoteroAPIError
from zotero.commands import cmd_list, cmd_search, cmd_metadata, cmd_unprocessed
from zotero.commands import cmd_mineru, cmd_auto_parse
from zotero.commands import cmd_mineru_parse
from zotero.commands import cmd_paper_status
from zotero.commands import cmd_prepare_note, cmd_write_note, cmd_verify_note
from zotero.commands import cmd_assemble_note, cmd_update_section
from zotero.commands import cmd_audit_notes, cmd_prepare_triage, cmd_audit_tags, cmd_audit_all
from zotero.commands import cmd_sync_links, cmd_backfill_status_tags
from zotero.commands import cmd_tag_governance
from zotero.commands import cmd_prune_assets
from zotero.commands import cmd_mark_private
from zotero.commands import cmd_write


def Main() -> int:
    # Note content and paper titles are UTF-8 and frequently non-ASCII; on a
    # Windows console or a C locale, printing them raises from inside a report.
    from paths_common import ensure_utf8_stdio

    ensure_utf8_stdio()

    parser = argparse.ArgumentParser(
        prog="zotero_cli",
        description="Terminal bridge for Zotero Local API + MinerU cache.",
    )
    parser.add_argument("--config", help="Path to paths.local.yaml (auto-detected if omitted)")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Register all subcommands
    cmd_list.Register(subparsers)
    cmd_search.Register(subparsers)
    cmd_metadata.Register(subparsers)
    cmd_unprocessed.Register(subparsers)
    cmd_mineru.Register(subparsers)
    cmd_auto_parse.Register(subparsers)
    cmd_mineru_parse.Register(subparsers)
    cmd_paper_status.Register(subparsers)
    cmd_prepare_note.Register(subparsers)
    cmd_write_note.Register(subparsers)
    cmd_verify_note.Register(subparsers)
    cmd_assemble_note.Register(subparsers)
    cmd_update_section.Register(subparsers)
    cmd_audit_notes.Register(subparsers)
    cmd_prepare_triage.Register(subparsers)
    cmd_audit_tags.Register(subparsers)
    cmd_audit_all.Register(subparsers)
    cmd_sync_links.Register(subparsers)
    cmd_backfill_status_tags.Register(subparsers)
    cmd_tag_governance.Register(subparsers)
    cmd_prune_assets.Register(subparsers)
    cmd_mark_private.Register(subparsers)
    cmd_write.Register(subparsers)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Load config
    config = load_config(args.config)

    # Dispatch to command with one consistent user-facing API error boundary.
    try:
        return args.func(args, config)
    except ZoteroAPIError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(Main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_audit_all.py — Combined audit: runs audit-notes + audit-tags in one call.

Usage:
    python3 zotero_cli.py audit-all
    python3 zotero_cli.py audit-all --json
    python3 zotero_cli.py audit-all --errors-only
    python3 zotero_cli.py audit-all --quick   # skip image existence check (for pre-commit)

Exit 0 if both pass, 1 if either fails.
"""

from __future__ import annotations

import contextlib
import io
import json

from ..config import ZoteroConfig
from . import cmd_audit_notes, cmd_audit_tags


# ─── Stub args for internal delegation ──────────────────────────────────────────────────────────────────────────────────

class _StubArgs:
    """Minimal argparse.Namespace stub to delegate to audit-notes / audit-tags."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ─── Public ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def Register(subparsers) -> None:
    """Register the 'audit-all' subcommand."""
    p = subparsers.add_parser("audit-all", help="Run audit-notes + audit-tags combined")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--errors-only", action="store_true", help="Only show failures")
    p.add_argument("--quick", action="store_true", help="Skip image existence check (faster)")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    """Execute both audits. Returns 0 if both pass, 1 otherwise."""

    # ── audit-notes ──
    notes_args = _StubArgs(
        json=args.json,
        errors_only=args.errors_only,
        limit=0,
        quick=args.quick,
    )
    if args.json:
        notes_buf = io.StringIO()
        with contextlib.redirect_stdout(notes_buf):
            notes_rc = cmd_audit_notes._Run(notes_args, config)
        notes_data = json.loads(notes_buf.getvalue())
    else:
        notes_rc = cmd_audit_notes._Run(notes_args, config)

    # ── audit-tags ──
    tags_args = _StubArgs(json=args.json, include_clean=False, dir="")
    if args.json:
        tags_buf = io.StringIO()
        with contextlib.redirect_stdout(tags_buf):
            tags_rc = cmd_audit_tags._Run(tags_args, config)
        tags_data = json.loads(tags_buf.getvalue())
    else:
        tags_rc = cmd_audit_tags._Run(tags_args, config)

    # ── Output ──
    if args.json:
        combined = {
            "audit_notes": notes_data,
            "audit_tags": tags_data,
            "overall": {
                "notes_pass": notes_rc == 0,
                "tags_pass": tags_rc == 0,
                "all_pass": notes_rc == 0 and tags_rc == 0,
            },
        }
        print(json.dumps(combined, ensure_ascii=False, separators=(",", ":")))
    else:
        notes_pass = "PASS" if notes_rc == 0 else "FAIL"
        tags_pass = "PASS" if tags_rc == 0 else "FAIL"
        status = "✅ ALL PASS" if (notes_rc == 0 and tags_rc == 0) else "❌ SOME FAILED"
        print(f"\nOVERALL: {status}  (audit-notes: {notes_pass}, audit-tags: {tags_pass})")

    return 0 if (notes_rc == 0 and tags_rc == 0) else 1

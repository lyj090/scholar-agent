#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cmd_paper_status.py — local-first preflight before any web fetch.

Answers "do I already have this paper?" for arXiv IDs, DOIs, URLs, titles, or
Zotero keys, so a design/review agent reads the local MinerU cache instead of
re-fetching from arXiv.

NOT responsible for: cache parsing, library indexing internals, HTTP.
"""

from __future__ import annotations

import json

from ..config import ZoteroConfig
from ..paper_status import preflight


def Register(subparsers) -> None:
    p = subparsers.add_parser(
        "paper-status",
        help="Local-first preflight: is this paper already cached locally?",
    )
    p.add_argument(
        "references",
        nargs="+",
        help="arXiv ID, DOI, paper URL, Zotero key, or title",
    )
    p.add_argument(
        "--json", action="store_true", help="Output compact JSON"
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the state word per reference",
    )
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    result = preflight(config, list(args.references))

    if args.json:
        print(
            json.dumps(
                result.to_dict(), ensure_ascii=False, separators=(",", ":")
            )
        )
        return 0

    if args.quiet:
        for status in result.statuses:
            print(status.state)
        return 0

    # Compact KEY=value-style output: one block per reference, <=6 lines total
    # for the common single-reference case.
    for status in result.statuses:
        print(f"STATE={status.state}")
        print(f"ACTION={status.action}")
        if status.title:
            print(f"TITLE={status.title}")
        if status.cache_id is not None:
            print(f"CACHE={status.cache_id}")
        if status.attachment_key:
            print(f"ATTACHMENT={status.attachment_key}")
            print(f"READ=python3 ~/scholar-agent/scripts/literature/zotero_cli.py mineru read {status.attachment_key} --section 0")
        elif status.item_key:
            print(f"ITEM={status.item_key}")
        if status.candidates:
            print(
                "CANDIDATES="
                + ",".join(str(cid) for cid in status.candidates)
            )
    return 0

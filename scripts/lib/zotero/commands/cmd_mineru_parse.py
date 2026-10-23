#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cmd_mineru_parse.py — parse a PDF into a plugin-compatible cache, externally.

This removes the last hard dependency on the Zotero GUI: previously a cache miss
was a dead end because parsing could only be triggered from the plugin UI.

Flow: resolve reference → find PDF (Zotero storage or explicit path) → resolve
numeric attachment id (sqlite) → call MinerU → normalize → write cache.

NOT responsible for: MinerU HTTP details, cache format internals, Zotero IDs.
"""

from __future__ import annotations

import json
import os
import sys

from ..config import ZoteroConfig
from ..mineru_cache_writer import write_mineru_cache
from ..mineru_client import (
    MineruAuthError,
    MineruClient,
    MineruConfigError,
    MineruParseError,
    MineruRateLimitError,
    read_mineru_api_key,
)
from ..zotero_db import ZoteroDatabase, ZoteroDbError


def Register(subparsers) -> None:
    p = subparsers.add_parser(
        "mineru-parse",
        help="Parse a PDF via MinerU and write a plugin-compatible cache (no GUI)",
    )
    p.add_argument(
        "reference",
        nargs="?",
        help="Zotero attachment/parent key, arXiv ID, DOI, or title",
    )
    p.add_argument(
        "--pdf", help="Explicit PDF path (skips Zotero lookup)"
    )
    p.add_argument(
        "--attachment-id",
        type=int,
        help="Numeric Zotero attachment id (the cache dir name)",
    )
    p.add_argument(
        "--mode",
        choices=("cloud", "local"),
        default="cloud",
        help="MinerU backend (default: cloud)",
    )
    p.add_argument("--api-key", help="MinerU API key (default: read Zotero prefs)")
    p.add_argument("--cloud-model", default="vlm", choices=("vlm", "pipeline"))
    p.add_argument(
        "--local-base", default="http://127.0.0.1:8000", help="Local mineru-api base"
    )
    p.add_argument(
        "--local-backend", default="pipeline", choices=("pipeline", "vlm", "hybrid")
    )
    p.add_argument("--force-ocr", action="store_true")
    p.add_argument(
        "--overwrite", action="store_true", help="Replace an existing cache"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Resolve everything, parse nothing"
    )
    p.add_argument("--test-connection", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=_Run)


def _fail(message: str, code: int = 1) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return code


def _resolve_target(config: ZoteroConfig, args):
    """Return ``(pdf_path, attachment_key, parent_key, attachment_id)``."""
    from ..paper_status import LOCAL_READY, preflight

    if args.pdf:
        pdf_path = os.path.abspath(os.path.expanduser(args.pdf))
        if not os.path.isfile(pdf_path):
            raise ZoteroDbError(f"PDF not found: {pdf_path}")
        attachment_id = args.attachment_id
        if attachment_id is None:
            raise ZoteroDbError(
                "--attachment-id is required with --pdf (it is the cache dir name)"
            )
        return pdf_path, None, None, int(attachment_id)

    if not args.reference:
        raise ZoteroDbError("provide a reference or --pdf")

    status = preflight(config, [args.reference]).statuses[0]

    if status.state == LOCAL_READY:
        raise FileExistsError(
            f"already parsed locally (cache {status.cache_id}); "
            "pass --overwrite to re-parse"
        )

    item_key = status.item_key
    attachment_key = status.attachment_key
    if not item_key:
        raise ZoteroDbError(
            f"reference {args.reference!r} is not in the Zotero library; import it first"
        )

    with ZoteroDatabase(config.data_dir) as database:
        location = database.locate(attachment_key or item_key)
        if location is None:
            raise ZoteroDbError(f"cannot resolve {item_key} in the Zotero database")

        # A parent key has no file; use its first PDF attachment.
        if attachment_key is None:
            candidates = status.pdf_attachment_keys
            if not candidates:
                raise ZoteroDbError(
                    f"{item_key} has no PDF attachment to parse"
                )
            attachment_key = candidates[0]
            location = database.locate(attachment_key)
            if location is None:
                raise ZoteroDbError(
                    f"cannot resolve attachment {attachment_key}"
                )

        pdf_path = database.storage_path(attachment_key)
        parent_key = location.parent_item_key or (
            item_key if location.parent_item_key is None else None
        )
        if not pdf_path or not os.path.isfile(pdf_path):
            raise ZoteroDbError(
                f"PDF file not found for {attachment_key} (linked attachment?)"
            )
        return pdf_path, attachment_key, parent_key, location.item_id


def ParseOne(
    config: ZoteroConfig,
    *,
    reference: str,
    pdf_path: str,
    attachment_id: int,
    attachment_key: str | None = None,
    parent_key: str | None = None,
    mode: str = "cloud",
    api_key: str | None = None,
    cloud_model: str = "vlm",
    local_base: str | None = None,
    local_backend: str = "pipeline",
    force_ocr: bool = False,
    overwrite: bool = False,
    verbose: bool = False,
    client=None,
) -> dict:
    """Parse one PDF and write its cache. Returns the writer's report dict.

    Extracted so ``mineru-sync`` reuses the exact parse-and-write path of
    ``mineru-parse`` rather than duplicating it: a second implementation would
    eventually produce subtly different caches, which is the "two lines that
    drift" problem this work exists to remove.

    Raises the client's own errors and ``FileExistsError`` when a cache exists
    and ``overwrite`` is false — callers decide if that is a failure or a skip.
    """
    if client is None:
        resolved_key = api_key or (None if mode == "local" else read_mineru_api_key())
        if mode == "cloud" and not resolved_key:
            raise MineruConfigError(
                "no MinerU API key. Set extensions.zotero.llmforzotero.mineruApiKey "
                "in Zotero, pass --api-key, or use --mode local."
            )
        # Only forward optional overrides when set: MineruClient's defaults are
        # the real values, so passing None would clobber them (local_base=None
        # raised "'NoneType' object has no attribute 'rstrip'" in cloud mode).
        client_kwargs = {
            "api_key": resolved_key,
            "mode": mode,
            "cloud_model": cloud_model,
            "local_backend": local_backend,
            "force_ocr": force_ocr,
            "verbose": verbose,
        }
        if local_base:
            client_kwargs["local_base"] = local_base
        client = MineruClient(**client_kwargs)

    result = client.parse_pdf(pdf_path)

    from ..mineru_client import _pick_markdown  # noqa: PLC2701 - same package

    markdown_name = _pick_markdown(result.files)
    if markdown_name is None:
        raise MineruParseError("MinerU result has no Markdown")

    written = write_mineru_cache(
        cache_root=config.mineru_cache,
        attachment_id=attachment_id,
        result=result,
        markdown_name=markdown_name,
        attachment_key=attachment_key,
        parent_item_key=parent_key,
        source_filename=os.path.basename(pdf_path),
        overwrite=overwrite,
    )
    return written.to_dict()


def _Run(args, config: ZoteroConfig) -> int:
    api_key = args.api_key or (
        None if args.mode == "local" else read_mineru_api_key()
    )
    client = MineruClient(
        api_key=api_key,
        mode=args.mode,
        local_base=args.local_base,
        cloud_model=args.cloud_model,
        local_backend=args.local_backend,
        force_ocr=args.force_ocr,
        verbose=args.verbose,
    )

    if args.test_connection:
        try:
            ok, message = client.test_connection()
        except MineruAuthError as exc:
            return _fail(str(exc))
        except MineruParseError as exc:
            return _fail(str(exc))
        print(f"OK={ok}")
        print(f"MSG={message}")
        return 0 if ok else 1

    if args.mode == "cloud" and not api_key:
        return _fail(
            "no MinerU API key. Set extensions.zotero.llmforzotero.mineruApiKey "
            "in Zotero, pass --api-key, or use --mode local."
        )

    try:
        pdf_path, attachment_key, parent_key, attachment_id = _resolve_target(
            config, args
        )
    except FileExistsError as exc:
        print(f"SKIP={exc}")
        return 0
    except (ZoteroDbError, MineruConfigError) as exc:
        return _fail(str(exc))

    if args.dry_run:
        print(f"PDF={pdf_path}")
        print(f"ATTACHMENT_ID={attachment_id}")
        print(f"ATTACHMENT_KEY={attachment_key or '-'}")
        print(f"PARENT_KEY={parent_key or '-'}")
        print(f"CACHE={os.path.join(config.mineru_cache, str(attachment_id))}")
        print("ACTION=would parse via MinerU")
        return 0

    # Shared with `mineru-sync`: one parse-and-write path, not two.
    try:
        written = ParseOne(
            config,
            reference=getattr(args, "reference", "") or "",
            pdf_path=pdf_path,
            attachment_id=attachment_id,
            attachment_key=attachment_key,
            parent_key=parent_key,
            overwrite=args.overwrite,
            client=client,
        )
    except MineruRateLimitError as exc:
        return _fail(f"{exc} (resume tomorrow)", code=4)
    except MineruAuthError as exc:
        return _fail(str(exc), code=5)
    except MineruParseError as exc:
        return _fail(str(exc))
    except MineruConfigError as exc:
        return _fail(str(exc))
    except FileExistsError as exc:
        print(f"SKIP={exc}")
        return 0
    except OSError as exc:
        return _fail(f"cannot write cache: {exc}")

    if args.json:
        print(json.dumps(written, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"OK parsed {os.path.basename(pdf_path)}")
        print(f"CACHE={os.path.join(config.mineru_cache, str(attachment_id))}")
        print(f"CHARS={written.get('markdown_chars')} SECTIONS={written.get('section_count')}")
        print(f"FIGURES={written.get('figure_count')} IMAGES={written.get('image_count')}")
        print(
            f"READ=python3 ~/scholar-agent/scripts/literature/zotero_cli.py "
            f"mineru read {attachment_key or attachment_id} --section 0"
        )
    return 0

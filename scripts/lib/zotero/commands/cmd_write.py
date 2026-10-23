#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cmd_write.py — Zotero writes through the unified CLI (no plugin).

Registers the ``write`` command group plus ``mineru-push``. Together with the
existing read/MinerU commands these make ``zotero_cli.py`` the single entry an
agent needs for the whole paper loop:

    paper-status   → is it local?
    mineru read    → read a section, bounded
    mineru-parse   → parse a PDF when no cache exists
    mineru-push    → index that parse into Zotero's own full-text search
    write ...      → tag / move / trash / merge / undo

Every mutation is snapshotted into the action ledger first, so ``write undo``
can reverse it. That is what lets this replace the plugin safely.

NOT responsible for: HTTP details (``local_write``), history (``action_ledger``),
MinerU parsing (``mineru_client``/``mineru_catalog``).
"""

from __future__ import annotations

import json
import os
import sys

from ..action_ledger import ActionLedger, REVERSIBILITY_PARTIAL
from ..api import ZoteroAPIError, normalize_key
from ..config import ZoteroConfig
from ..library_index import invalidate_library_index
from ..local_write import (
    ZoteroAuthRequired,
    ZoteroWriteClient,
    ZoteroWriteError,
    build_write_client,
)


#: Placeholder tags Zotero's own connectors add. Triage drops them so an item
#: leaving the inbox does not carry scraper noise into the curated taxonomy.
LEGACY_TAGS = (
    "/unread",
    "Computer Science - Robotics",
    "Computer Science - Computer Vision and Pattern Recognition",
    "Computer Science - Artificial Intelligence",
)


def Register(subparsers) -> None:
    p = subparsers.add_parser(
        "write",
        help="Safe Zotero writes: tag, move, trash, merge, undo (no plugin)",
    )
    ops = p.add_subparsers(dest="write_op", help="Write operation")

    # --- status ---
    ops.add_parser("status", help="Report whether writes are authorized")

    # --- tag ---
    t = ops.add_parser("tag", help="Add tags (merges; never clears existing)")
    t.add_argument("key", help="Zotero item key (8 chars)")
    t.add_argument("tags", nargs="+", help="Tags to add")
    t.add_argument("--json", action="store_true")

    # --- move ---
    m = ops.add_parser("move", help="Add an item to a collection")
    m.add_argument("key", help="Zotero item key")
    m.add_argument("collection", help="Target collection key")
    m.add_argument("--json", action="store_true")

    # --- collection ---
    c = ops.add_parser("collection", help="Create a collection")
    c.add_argument("name", help="Collection name")
    c.add_argument("--parent", help="Parent collection key (nested)")
    c.add_argument("--json", action="store_true")

    # --- trash / restore ---
    tr = ops.add_parser("trash", help="Move items to the trash (reversible)")
    tr.add_argument("keys", nargs="+")
    tr.add_argument("--json", action="store_true")

    rs = ops.add_parser("restore", help="Restore items from the trash")
    rs.add_argument("keys", nargs="+")
    rs.add_argument("--json", action="store_true")

    # --- merge ---
    mg = ops.add_parser("merge", help="Merge duplicates into a master (donors trashed)")
    mg.add_argument("master", help="Key of the item to keep")
    mg.add_argument("duplicates", nargs="+", help="Keys to fold into the master")
    mg.add_argument("--json", action="store_true")

    # --- triage (batch inbox -> collection + tags) ---
    tg = ops.add_parser(
        "triage", help="Batch: move items out of the inbox into collections and tag them"
    )
    tg.add_argument(
        "--plan",
        required=True,
        help='JSON array or path: [{"key":"X","target":"COL","tags":["a"]}]',
    )
    tg.add_argument("--inbox", help="Inbox collection key to remove items from")
    tg.add_argument("--json", action="store_true")

    # --- apply (batch tag replacement from a plan) ---
    ap = ops.add_parser(
        "apply", help="Batch: set each item's tags to a plan's target list"
    )
    ap.add_argument(
        "--plan",
        required=True,
        help='JSON array or path: [{"key":"X","after":["a","b"]}]',
    )
    ap.add_argument("--json", action="store_true")

    # --- attachment maintenance ---
    at = ops.add_parser("attachment", help="Rename, relink, or trash a PDF attachment")
    at.add_argument("action", choices=["rename", "relink", "trash"])
    at.add_argument("keys", nargs="+", help="Attachment keys")
    at.add_argument("--name", help="New filename (for rename)")
    at.add_argument("--path", help="New absolute path (for relink)")
    at.add_argument("--json", action="store_true")

    # --- history / undo ---
    h = ops.add_parser("history", help="Show recent recorded actions")
    h.add_argument("--last", type=int, default=10)
    h.add_argument("--json", action="store_true")

    u = ops.add_parser("undo", help="Revert recorded actions")
    u.add_argument("action_ids", nargs="*", help="Action ids to revert")
    u.add_argument("--last", type=int, help="Revert the newest N reversible actions")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--json", action="store_true")

    p.set_defaults(func=_Run)

    # --- mineru-push (MinerU -> Zotero full-text index) ---
    mp = subparsers.add_parser(
        "mineru-push",
        help="Index a local MinerU cache into Zotero's full-text search",
    )
    mp.add_argument("reference", help="MinerU cache id, attachment key, or parent key")
    mp.add_argument("--dry-run", action="store_true")
    mp.add_argument("--max-chars", type=int, default=0, help="Truncate (0 = whole text)")
    mp.add_argument("--json", action="store_true")
    mp.set_defaults(func=_RunPush)


def _client(config: ZoteroConfig) -> ZoteroWriteClient:
    # Auto-starts Zotero when it is closed, so `write status` is the only command
    # a caller needs to get from a cold machine to a writable library.
    return build_write_client(config)


def _emit_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _Run(args, config: ZoteroConfig) -> int:
    op = getattr(args, "write_op", None)
    if not op:
        print("write: choose an operation (status/tag/move/collection/trash/restore/merge/history/undo)", file=sys.stderr)
        return 1

    client = _client(config)
    ledger = ActionLedger()

    try:
        if op == "status":
            return _Status(client, args)
        if op == "tag":
            return _Tag(client, ledger, args)
        if op == "move":
            return _Move(client, ledger, args)
        if op == "collection":
            return _Collection(client, args)
        if op == "trash":
            return _Trash(client, ledger, args, deleted=True)
        if op == "restore":
            return _Trash(client, ledger, args, deleted=False)
        if op == "merge":
            return _Merge(client, ledger, args)
        if op == "triage":
            return _Triage(client, ledger, config, args)
        if op == "apply":
            return _Apply(client, ledger, args)
        if op == "attachment":
            return _Attachment(client, ledger, args)
        if op == "history":
            return _History(ledger, args)
        if op == "undo":
            return _Undo(client, ledger, args)
    except ZoteroAuthRequired as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 3
    except (ZoteroWriteError, ZoteroAPIError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1

    print(f"[ERROR] unknown write operation: {op}", file=sys.stderr)
    return 1


# ----------------------------------------------------------------------
#  Operations
# ----------------------------------------------------------------------


def _Status(client: ZoteroWriteClient, args) -> int:
    server_id = None
    error = None
    try:
        server_id = client.server_id()
    except ZoteroWriteError as exc:
        error = str(exc)
    writable = False
    if server_id:
        try:
            client.api_key
            writable = True
        except ZoteroAuthRequired:
            writable = False

    payload = {
        "serverId": server_id,
        "writable": writable,
        "authorized": writable,
        "error": error,
    }
    if getattr(args, "json", False):
        _emit_json(payload)
        return 0 if writable else 3
    if error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    print(f"SERVER_ID={server_id}")
    print(f"WRITABLE={'yes' if writable else 'no'}")
    if not writable:
        print("AUTHORIZE=POST /api/local/authorize (choose \"Always Allow\")")
        return 3
    return 0


def _Tag(client: ZoteroWriteClient, ledger: ActionLedger, args) -> int:
    key = normalize_key(args.key, "item key")
    before = ledger.snapshot_many(client, [key], ("tags",))
    final = client.add_tags(key, list(args.tags))
    ledger.record_write(
        operation="add_tags",
        keys=[key],
        before=before,
        after={key: {"tags": [{"tag": t} for t in final]}},
        description=f"tag {key} += {' '.join(args.tags)}",
    )
    if getattr(args, "json", False):
        _emit_json({"key": key, "tags": final})
    else:
        print(f"[OK] {key} tags: {' '.join(final)}")
    return 0


def _Move(client: ZoteroWriteClient, ledger: ActionLedger, args) -> int:
    key = normalize_key(args.key, "item key")
    collection = normalize_key(args.collection, "collection key")
    before = ledger.snapshot_many(client, [key], ("collections",))
    cols = client.add_to_collection(key, collection)
    ledger.record_write(
        operation="move_to_collection",
        keys=[key],
        before=before,
        after={key: {"collections": cols}},
        description=f"move {key} -> {collection}",
    )
    if getattr(args, "json", False):
        _emit_json({"key": key, "collections": cols})
    else:
        print(f"[OK] {key} collections: {' '.join(cols)}")
    return 0


def _Collection(client: ZoteroWriteClient, args) -> int:
    key = client.create_collection(args.name, getattr(args, "parent", None))
    if getattr(args, "json", False):
        _emit_json({"key": key, "name": args.name})
    else:
        print(f"[OK] created collection {key}: {args.name}")
    return 0


def _Trash(client: ZoteroWriteClient, ledger: ActionLedger, args, *, deleted: bool) -> int:
    keys = [normalize_key(k, "item key") for k in args.keys]
    before = ledger.snapshot_many(client, keys, ("tags", "collections"))
    done: list[str] = []
    failed: list[dict] = []
    for key in keys:
        try:
            client.set_deleted(key, deleted)
            done.append(key)
        except ZoteroWriteError as error:
            failed.append({"key": key, "error": str(error)})

    if done:
        ledger.record_write(
            operation="trash_items" if deleted else "restore_from_trash",
            keys=done,
            before=before,
            after={},
            description=(
                f"{'trash' if deleted else 'restore'} {len(done)} item(s)"
            ),
            # The post-state is "deleted flag", which snapshot does not carry;
            # undo re-applies the snapshot fields, so this is a partial record.
            reversibility=REVERSIBILITY_PARTIAL,
            extra={"deleted": deleted},
        )
    payload = {"changed": done, "failed": failed}
    if getattr(args, "json", False):
        _emit_json(payload)
    else:
        verb = "trashed" if deleted else "restored"
        print(f"[OK] {len(done)} item(s) {verb}: {' '.join(done)}")
        for item in failed:
            print(f"[WARN] {item['key']}: {item['error']}", file=sys.stderr)
    return 0 if not failed else 1


def _Merge(client: ZoteroWriteClient, ledger: ActionLedger, args) -> int:
    master = normalize_key(args.master, "master key")
    donors = [normalize_key(k, "duplicate key") for k in args.duplicates]
    before = ledger.snapshot_many(client, [master] + donors, ("tags", "collections"))
    result = client.merge(master, donors)
    # Recorded as reversible: donors are only trashed and every re-parented child
    # is logged in childMoves, so `undo` can restore the donors and send the
    # children back. (The plugin's merge could not be undone at all.)
    ledger.record_write(
        operation="merge_items",
        keys=[master] + donors,
        before=before,
        after={
            master: {
                "tags": [{"tag": t} for t in result["tags"]],
                "collections": result["collections"],
            },
        },
        description=f"merge {len(donors)} duplicate(s) into {master}",
        extra={
            "master": master,
            "others": donors,
            "moved": result["moved"],
            "childMoves": result.get("childMoves") or [],
        },
    )
    if getattr(args, "json", False):
        _emit_json(result)
    else:
        print(
            f"[OK] merged {result['merged']} into {master} "
            f"({result['moved']} child item(s) moved; donors trashed, undoable)"
        )
    return 0


def _LoadPlan(raw: str) -> list[dict]:
    """Accept either inline JSON or a path to a JSON file."""
    text = str(raw or "").strip()
    if not text:
        raise ZoteroWriteError("Empty plan")
    if os.path.isfile(os.path.expanduser(text)):
        with open(os.path.expanduser(text), "r", encoding="utf-8") as handle:
            text = handle.read()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ZoteroWriteError(f"Plan is not valid JSON: {error}") from error
    if isinstance(parsed, dict):
        parsed = parsed.get("items") or parsed.get("plan") or []
    if not isinstance(parsed, list):
        raise ZoteroWriteError("Plan must be a JSON array")
    return [row for row in parsed if isinstance(row, dict)]


def _Triage(client: ZoteroWriteClient, ledger: ActionLedger, config, args) -> int:
    """Batch inbox triage: preflight every row, then apply."""
    plan = _LoadPlan(args.plan)
    if not plan:
        print("[OK] nothing to triage")
        return 0

    inbox = getattr(args, "inbox", None) or config.inbox_collection_key

    # Preflight the whole batch before writing any of it: a partially applied
    # triage leaves the inbox in a state the user cannot reason about.
    problems = []
    for row in plan:
        if not row.get("key"):
            problems.append("row without a key")
        if not row.get("target"):
            problems.append(f"{row.get('key')}: no target collection")
    if problems:
        print(f"[ERROR] Triage preflight failed: {'; '.join(problems)}", file=sys.stderr)
        return 1

    keys = [row["key"] for row in plan]
    before = ledger.snapshot_many(client, keys, ("tags", "collections"))

    results = []
    failed = []
    for row in plan:
        try:
            outcome = client.triage_item(
                row["key"],
                target=row["target"],
                tags=row.get("tags") or [],
                inbox=inbox,
                drop_tags=tuple(row.get("dropTags") or LEGACY_TAGS),
            )
            results.append(outcome)
        except ZoteroWriteError as error:
            failed.append({"key": row["key"], "error": str(error)})

    if results:
        ledger.record_write(
            operation="triage_items",
            keys=[r["key"] for r in results],
            before=before,
            after={r["key"]: {"tags": [{"tag": t} for t in r["tags"]],
                              "collections": r["collections"]}
                   for r in results},
            description=f"triage {len(results)} item(s)",
        )

    payload = {"changed": len(results), "results": results, "failed": failed}
    if getattr(args, "json", False):
        _emit_json(payload)
    else:
        print(f"[OK] {len(results)} item(s) triaged")
        for item in failed:
            print(f"[WARN] {item['key']}: {item['error']}", file=sys.stderr)
    return 0 if not failed else 1


def _Apply(client: ZoteroWriteClient, ledger: ActionLedger, args) -> int:
    """Batch tag replacement: each row's ``after`` is authoritative."""
    plan = _LoadPlan(args.plan)
    changes = [
        row for row in plan
        if row.get("key") and "after" in row
    ]
    if not changes:
        print("[OK] nothing to apply")
        return 0

    keys = [row["key"] for row in changes]
    before = ledger.snapshot_many(client, keys, ("tags",))
    applied = []
    failed = []
    for row in changes:
        try:
            client.set_tags(row["key"], list(row.get("after") or []))
            applied.append(row["key"])
        except ZoteroWriteError as error:
            failed.append({"key": row["key"], "error": str(error)})

    if applied:
        ledger.record_write(
            operation="set_tags",
            keys=applied,
            before=before,
            after={row["key"]: {"tags": [{"tag": t} for t in (row.get("after") or [])]}
                   for row in changes if row["key"] in applied},
            description=f"apply tags to {len(applied)} item(s)",
        )

    payload = {"changed": len(applied), "keys": applied, "failed": failed}
    if getattr(args, "json", False):
        _emit_json(payload)
    else:
        print(f"[OK] {len(applied)} item(s) updated")
        for item in failed:
            print(f"[WARN] {item['key']}: {item['error']}", file=sys.stderr)
    return 0 if not failed else 1


def _Attachment(client: ZoteroWriteClient, ledger: ActionLedger, args) -> int:
    """Rename, relink, or trash attachments. Never permanently deletes."""
    keys = [normalize_key(k, "attachment key") for k in args.keys]
    action = args.action

    if action == "rename" and not args.name:
        print("[ERROR] --name is required for rename", file=sys.stderr)
        return 1
    if action == "relink" and not args.path:
        print("[ERROR] --path is required for relink", file=sys.stderr)
        return 1

    # Snapshot the field the action is about to change, so `write undo` can put
    # it back. Rename and relink change the attachment's own filename/path, not
    # its title: snapshotting "title" recorded an entry that reported itself as
    # fully revertible while capturing nothing this command actually wrote.
    if action == "rename":
        before = ledger.snapshot_many(client, keys, ("filename",))
    elif action == "relink":
        before = ledger.snapshot_many(client, keys, ("path",))
    else:
        before = {}

    done = []
    failed = []
    # ``after`` is what the drift check compares against on undo: if the value
    # differs, someone else changed the item and the undo refuses rather than
    # stamping over their edit.
    after: dict[str, dict] = {}
    for key in keys:
        try:
            if action == "rename":
                result = client.rename_attachment(key, args.name)
                after[key] = {"filename": result.get("filename")}
            elif action == "relink":
                client.relink_attachment(key, args.path)
                after[key] = {"path": os.path.abspath(os.path.expanduser(args.path))}
            else:
                client.trash(key)
            done.append(key)
        except ZoteroWriteError as error:
            failed.append({"key": key, "error": str(error)})

    if done:
        ledger.record_write(
            operation=f"{action}_attachment",
            keys=done,
            before=before or None,
            after=after or None,
            description=f"{action} {len(done)} attachment(s)",
        )
        # A rename/relink/trash changes which file an attachment resolves to, so
        # the cached identifier index now describes the previous state. Drop it
        # rather than let a later paper-status answer from a stale view.
        if action in ("rename", "relink", "trash"):
            invalidate_library_index()

    payload = {"action": action, "changed": len(done), "keys": done, "failed": failed}
    if getattr(args, "json", False):
        _emit_json(payload)
    else:
        print(f"[OK] {action}: {len(done)} attachment(s)")
        for item in failed:
            print(f"[WARN] {item['key']}: {item['error']}", file=sys.stderr)
    return 0 if not failed else 1


def _History(ledger: ActionLedger, args) -> int:
    entries = ledger.read()[-max(1, args.last):]
    if getattr(args, "json", False):
        _emit_json({"count": len(entries), "entries": entries})
        return 0
    if not entries:
        print("(no recorded actions)")
        return 0
    for entry in entries:
        # Gate on the same predicate `undo` uses, not on the stored string:
        # legacy plugin-journal entries say "full"/"partial" but carry no
        # snapshot, so advertising them as revertible would be a false promise.
        if not ActionLedger.is_reversible(entry):
            mark, note = "✗", "  (no snapshot - cannot revert)"
        elif entry.get("reversibility") == "full":
            mark, note = "↩", ""
        else:
            mark, note = "~", "  (partial)"
        print(
            f"{mark} {entry.get('actionId')}  {entry.get('operation')}  "
            f"{entry.get('description', '')[:60]}{note}"
        )
    print("\n↩ = fully revertible   ~ = partial   ✗ = cannot revert")
    return 0


def _Undo(client: ZoteroWriteClient, ledger: ActionLedger, args) -> int:
    if args.action_ids:
        targets = list(args.action_ids)
        skipped: list[dict] = []
    elif args.last:
        picked, skipped = ledger.select_last(args.last)
        targets = [e["actionId"] for e in picked]
    else:
        print("[ERROR] provide action ids or --last N", file=sys.stderr)
        return 1

    if not targets:
        print(
            "[ERROR] no reversible actions found in the ledger",
            file=sys.stderr,
        )
        return 1

    reports = [ledger.rollback(aid, client, dry_run=args.dry_run) for aid in targets]
    total = sum(r.get("reverted", 0) for r in reports)
    conflicts = [c for r in reports for c in (r.get("conflicts") or [])]
    errors = [r for r in reports if not r.get("ok")]

    if getattr(args, "json", False):
        _emit_json(
            {
                "dryRun": bool(args.dry_run),
                "reverted": total,
                "reports": reports,
                "skipped": skipped,
            }
        )
    else:
        for report in reports:
            if not report.get("ok"):
                print(f"[WARN] {report['actionId']}: {report.get('error')}", file=sys.stderr)
            else:
                verb = "would revert" if args.dry_run else "reverted"
                print(f"[OK] {report['actionId']}: {verb} {report.get('reverted', 0)} item(s)")
        for conflict in conflicts:
            print(f"[WARN] conflict on {conflict['key']}: {conflict['reason']}", file=sys.stderr)
        for entry in skipped:
            print(f"[SKIP] {entry.get('actionId')}: not reversible", file=sys.stderr)

    return 0 if not errors and not conflicts else 1


# ----------------------------------------------------------------------
#  MinerU -> Zotero full-text index
# ----------------------------------------------------------------------


def _RunPush(args, config: ZoteroConfig) -> int:
    """Push a MinerU cache into Zotero's full-text index.

    Zotero indexes PDFs with its own extractor; a MinerU parse is higher quality
    (structure, tables, equations) and would otherwise stay invisible to
    Zotero's search. This closes the loop between the two halves of the workflow.
    """
    from ..mineru_catalog import MineruCatalog, MineruNotFoundError
    from ..api import ZoteroAPI

    api = ZoteroAPI(config.api_url, config.user_id)
    catalog = MineruCatalog(config.mineru_cache, api, config)

    try:
        record = catalog.resolve(args.reference)
    except MineruNotFoundError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"[ERROR] cannot resolve {args.reference}: {error}", file=sys.stderr)
        return 1

    text = catalog.read_full(record)
    if not text:
        print(f"[ERROR] empty MinerU cache {record.cache_dir}", file=sys.stderr)
        return 2

    if args.max_chars and len(text) > args.max_chars:
        text = text[: args.max_chars]

    attachment_key = record.attachment_key
    if not attachment_key:
        print(
            f"[ERROR] cache {record.cache_id} has no attachment key; cannot index",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        if args.json:
            _emit_json({"attachment": attachment_key, "chars": len(text), "dryRun": True})
        else:
            print(f"[DRY-RUN] would index {len(text)} chars into {attachment_key}")
        return 0

    client = _client(config)
    client.set_fulltext(attachment_key, text)
    if args.json:
        _emit_json({"attachment": attachment_key, "chars": len(text), "indexed": True})
    else:
        print(f"[OK] indexed {len(text)} chars into {attachment_key}")
    return 0

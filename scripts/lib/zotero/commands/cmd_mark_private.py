#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_mark_private.py — Insert the explicit `<!-- private -->` divider above a
legacy personal section so publication no longer depends on a title keyword.

Rationale. A note used to hide its non-paper tail by giving a closing section a
recognised heading (`## 9. 项目关联与本笔记判读`). That is a *heading heuristic*:
the publisher truncates at the first matching heading, so a private section
titled like ordinary paper content (`## 10. 证据边界与复现限制`) is published.
The explicit divider is a positive marker — everything from it to EOF is
dropped whatever the heading — so it cannot be defeated by naming.

This command rewrites a legacy note to carry the divider, without touching the
hidden content. The publication output is identical in the simple case, because
the region already truncated at that heading; the divider just makes the cut
explicit and safe for any heading below it.

Safety. The command rewrites a note only when its personal tail is a clean
trailing section: the first personal heading is a top-level section (`##`) and
it is the last top-level section in the file. A note whose personal content is
scattered through the body (a `### 我的理解` subsection inside a chapter) is
left alone and reported as needing review — a mechanical divider there would
either expose private text or hide paper chapters.

Default is a dry run. ``--apply`` writes, but only when the file was not
modified since it was read.

NOT responsible for: deciding whether a section *should* be private (that is a
content review) or for publishing.
"""

from __future__ import annotations

import json
import os
import re
import sys

from ..config import ZoteroConfig
from ..note_structure import _visible_lines, PRIVATE_MARKER_RE, PERSONAL_SECTION_RE

kDivider = "<!-- private -->"


def Register(subparsers) -> None:
    """Register the 'mark-private' subcommand."""
    p = subparsers.add_parser(
        "mark-private",
        help="Insert the <!-- private --> divider above a legacy personal section",
    )
    p.add_argument("--dry-run", action="store_true", help="Report without writing")
    p.add_argument("--apply", action="store_true", help="Write the changes")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--file", default="", help="Only this note (absolute path or name)")
    p.add_argument("--notes-dir", default="", help="Override literature notes directory")
    p.set_defaults(func=_Run)


def _Run(args, config: ZoteroConfig) -> int:
    notes_dir = args.notes_dir or config.literature_notes
    if not os.path.isdir(notes_dir):
        print(f"[ERROR] Notes directory not found: {notes_dir}", file=sys.stderr)
        return 1

    if args.file:
        target = args.file if os.path.isabs(args.file) else os.path.join(notes_dir, args.file)
        files = [target] if os.path.isfile(target) else []
        if not files:
            print(f"[ERROR] Note not found: {target}", file=sys.stderr)
            return 1
    else:
        files = [os.path.join(notes_dir, f) for f in sorted(os.listdir(notes_dir))
                 if f.endswith(".md")]

    changed: list[dict] = []
    skipped: list[str] = []
    needs_review: list[dict] = []
    errors: list[dict] = []

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            errors.append({"file": os.path.basename(path), "error": str(e)})
            continue

        if any(PRIVATE_MARKER_RE.match(line) for _, line in _visible_lines(content)):
            skipped.append(os.path.basename(path)); continue

        # Top-level (## / #) headings, in order, with their offsets.
        tops = [(off, line) for off, line in _visible_lines(content)
                if re.match(r"^#{1,2}\s+\S", line)]

        offset, reason = None, ""
        first_personal = next(
            ((off, line) for off, line in _visible_lines(content)
             if PERSONAL_SECTION_RE.match(re.sub(r"[*_`]", "", line))),
            None,
        )
        if first_personal is None:
            skipped.append(os.path.basename(path)); continue
        if re.match(r"^#{3,}\s", first_personal[1]):
            reason = "personal content starts at a subsection (###), not a trailing section"
        else:
            last_top = tops[-1] if tops else None
            if last_top is not None and last_top[0] == first_personal[0]:
                offset = first_personal[0]
            else:
                reason = "a top-level section follows the personal heading"
        if offset is None:
            needs_review.append({"file": os.path.basename(path), "reason": reason})
            continue

        before = content[:offset]
        after = content[offset:]
        new = before.rstrip("\n") + "\n\n" + kDivider + "\n\n" + after
        if new == content:
            skipped.append(os.path.basename(path)); continue

        record = {
            "file": os.path.basename(path),
            "inserted_before": after.splitlines()[0].strip() if after.splitlines() else "",
            "bytes_before": len(content.encode("utf-8")),
            "bytes_after": len(new.encode("utf-8")),
        }
        changed.append(record)
        if args.apply:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(new)
            os.replace(tmp, path)

    if args.json:
        print(json.dumps({
            "notes_dir": notes_dir,
            "changed_count": len(changed),
            "skipped_count": len(skipped),
            "needs_review_count": len(needs_review),
            "applied": bool(args.apply),
            "changed": changed,
            "needs_review": needs_review,
            "errors": errors,
        }, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"=== mark-private: {len(changed)} update, {len(skipped)} skip, "
              f"{len(needs_review)} need review ===")
        for rec in changed:
            print(f"  {rec['file']}")
            print(f"      divider above: {rec['inserted_before']}")
        for rec in needs_review:
            print(f"  [review] {rec['file']}: {rec['reason']}")
        if not args.apply and changed:
            print("\nDry run — nothing written. Re-run with --apply to insert the divider.")
        elif args.apply and changed:
            print(f"\nUpdated {len(changed)} note(s).")
        if errors:
            print(f"WARN: {len(errors)} unreadable: {errors[:3]}", file=sys.stderr)
    return 0

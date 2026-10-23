# Obsidian + Zotero tag governance - agent spec

Unified tag taxonomy + audit + fix across vault and Zotero-linked notes.
Use only for an explicit tag audit, normalization, migration, or fix across
Zotero and Obsidian literature notes.

## Core rule

One canonical taxonomy for Obsidian + Zotero. Tags are structural metadata, not
keyword bags.

Authoritative files:

- ~/scholar-agent/shared/config/tag-taxonomy-rules.yaml - allowed values,
  namespaces, folder defaults
- This document - process and conventions

## Allowed namespaces

type/*, area/*, topic/*, method/*, task/*, software/*, hardware/*, source/*,
rel/*, and status/*.

Forbidden: bare tags, duplicate semantics, one-off keywords, mixed zh/en
duplicates, and inline YAML tag arrays.

## Paths

Follow shared/protocols/resolve-paths.md. Required keys:
obsidian.vault, obsidian.literature_notes, zotero.local_api, and zotero.user_id.

## Audit

For a broad read-only check, use one compact call:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py \
  audit-all --json --errors-only --quick
```

Use audit-tags --json for tag-only scope or audit-notes --json --errors-only
--limit N for a bounded note scope. Read compact JSON only; do not manually
scan every note. Produce a short table of violations and ambiguous mappings,
then stop for confirmation.

## Phase 1 - Plan

Build a deterministic two-sided plan; this command never writes:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py tag-governance plan \
  --output /tmp/paper-tag-plan.json --json
```

The plan scans top-level research items and Obsidian notes with a zotero_key.
It merges existing semantic tags, applies only missing type/*, area/*, and
status/* tags, and records before, after, and unknown values. There is no alias
resolution: every input tag must already be canonical. Inspect the JSON before
any write.

## Phase 2 - Review

The plan reports non-canonical input in two buckets, and only one of them blocks
apply:

| Bucket | Violation | Example | Action |
|---|---|---|---|
| `dropped_noise` | arXiv category | Computer Science - Robotics | auto-DROP, does not block |
| `dropped_noise` | arXiv shorthand | Machine Learning (cs.LG) | auto-DROP, does not block |
| `dropped_noise` | Field of Science | FOS: Computer and information sciences | auto-DROP, does not block |
| `dropped_noise` | Connector marker | /unread | auto-DROP, does not block |
| `unknown` | Bare tag | robotics | REVIEW or DELETE; **blocks apply** |
| `unknown` | Unknown bare tag | some-random | REVIEW or DELETE; **blocks apply** |
| `unknown` | Deprecated namespace | proj/example | REVIEW; use an explicit rel/* tag |
| `unknown` | Chinese tag | 强化学习 | REVIEW or DELETE; **blocks apply** |
| `unknown` | Missing taxonomy value | method/tracking | ADD to taxonomy, then re-plan |
| — | Duplicate semantics | area/robotics plus broad duplicate | REVIEW and remove redundant tag |
| — | Over limit | 4 topic/* tags | flag for user decision |

Connector noise is provably machine-generated and carries no user intent, so the
plan drops it instead of asking. Everything else stays in `unknown` and stops
apply: a sloppy human tag (`robotics`) is a decision to surface, not something to
delete silently. Resolve `unknown` by editing the taxonomy or the plan
explicitly; do not add an alias layer.

Obsidian audit issues include missing title/tags/authors/year/date, missing
type/* or area/*, inline tag arrays, an invalid source-note contract
section (or a `### 关联` in it without paper↔paper links), broken images, short
body, and any project reference left inside a published numbered section.

## Phase 3 - Apply

Only apply after the user confirms the reviewed plan:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py tag-governance apply \
  --plan /tmp/paper-tag-plan.json --json
```

The apply step checks plan drift and writes a `<plan>.before.json` snapshot for
Obsidian frontmatter rollback. Every changed Zotero item is also recorded in the
local action ledger with its prior tag list, so the whole sweep is revertible:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo --last 1 --dry-run
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo --last 1
```

It edits only the YAML tags block, never the note body.

For a single side:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py tag-governance apply \
  --plan /tmp/paper-tag-plan.json --zotero
python3 ~/scholar-agent/scripts/literature/zotero_cli.py tag-governance apply \
  --plan /tmp/paper-tag-plan.json --obsidian
```

Never fix without showing the report first. Never delete canonical compliant
tags. If a bare tag has no obvious mapping, ask the user. Keep Obsidian body
edits scoped.

## Phase 4 - Verify

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py tag-governance verify \
  --plan /tmp/paper-tag-plan.json --json
python3 ~/scholar-agent/scripts/literature/zotero_cli.py audit-all --json
```

The workflow is complete only when verify.pass is true, both tag audits pass,
and a second plan reports zero changes.

## Frontmatter shape

```yaml
---
title: "Readable title"
tags:
  - type/concept
  - area/robotics
status: active
updated: 2026-05-08
---
```

## Folder defaults

- 02-Literature/ZoteroNotes/**: require one type/*, one area/*, and one status/*
- 03-Concepts/**: require type/* + area/* when domain-specific
- 01-Projects/**: type/project when frontmatter exists

## Deliverables

- Audit-only: script output, proposed mappings, and ambiguous tags for review
- Fix: files touched, mappings applied, and re-scan confirmation

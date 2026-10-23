# Zotero Inbox triage - agent spec

Triage papers in Zotero 90_Inbox: folder targets, canonical tags, and a
transactional batch apply. Use only for an explicit request to classify or
organize unread or uncategorized Inbox items.

## Folder layout

```
01_Thesis/              <- research topics (themed subfolders)
  Aerial_Robotics/
  Control/
  Event_Vision/
  Hand_Tactile/
  Manipulation/
  Locomotion_RL/
  Perception/
  SLAM/
02_Lecture/
03_Reading/
04_Reference/
90_Inbox/               <- new items, short stay
99_Archive/
```

Routing:

- Research papers, conference papers, journal articles, preprints, and reports
  go to the most specific 01_Thesis/<subfolder>.
- 04_Reference is for books, textbooks, course material, manuals, and software
  documentation; do not place research papers there.
- Non-research reading goes to 03_Reading.
- No longer useful goes to 99_Archive.
- Unclear or metadata-incomplete items stay in 90_Inbox with an explanation.

## Tag rules

See ~/scholar-agent/shared/specs/obsidian-zotero-tag-governance-ai.md and
shared/config/tag-taxonomy-rules.yaml for the canonical taxonomy.

Keep at least one topic/* and one lifecycle status/* tag when evidence supports
them. Add rel/* only when the relationship to the user's work is clear. Never
invent tag values.

## Analysis phase

1. Follow shared/protocols/resolve-paths.md and resolve the configured Zotero
   API, user, Inbox collection, and MinerU cache paths.
2. Prefer one bounded batch call:

   ```bash
   python3 ~/scholar-agent/scripts/literature/zotero_cli.py \
     prepare-triage --json --limit 20
   ```

   This returns item keys, titles, abstracts, current tags, heuristic tags, and
   folder suggestions. Only request individual metadata when the batch result
   lacks evidence for a specific item.
3. Decide target folder, canonical tags, metadata gaps, duplicate DOI, and
   uncertainty. Keep unclear items in Inbox.
4. Output a review table with item key, target collection, tags, and uncertainty.
   Stop and wait for user confirmation.

## Execution phase

After confirmation, execute one batch call:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write triage \
  --plan '[{"key":"<ITEM_KEY>","target":"<COLLECTION_KEY>","tags":["topic/<x>","status/to-read"]}]'
```

`--inbox` is optional — it defaults to `zotero.inbox_collection_key` from
config. Do not hardcode collection keys; resolve targets with
`zotero_cli.py list` / `write collection` or `prepare-triage`.

Triage does three things per item in **one** write: removes Inbox membership,
adds the target collection, and applies the plan's tags while dropping
connector-noise tags (`/unread`, `Computer Science - *`). Single-item variants:
`write tag` (add-only, merges) and `write move` (keeps other memberships).
Prefer batch `triage`.

Every row is preflighted before any write, so a target collection that does not
exist aborts the whole batch rather than half-applying it.

Writes go through Zotero 10's native write API — no plugin, no UI confirmation.
The batch is snapshotted into the action ledger, so it is revertible.

## Verify

Report items/collections touched, unresolved items, and the final result. Keep
the plan for rollback:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo --last 1 --dry-run
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo --last 1
```

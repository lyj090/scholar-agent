# Literature pipeline — reference

How papers move from discovery to a note, and the constraints that shape every
step. This is the document to read before changing anything under
`scripts/lib/zotero/` or `skills/zotero-*`.

This document describes the system as it is. It deliberately carries no
development history: the record of what was tried first, and why it was replaced,
is kept with the maintainer rather than published, because it names third-party
internals and one machine's library.

---

## Prerequisites

1. Generate and validate machine-local paths — [README § Enabling the literature flow](../../README.md#enabling-the-literature-flow)
2. `python3 scripts/deploy/setup_paths.py --apply`, then edit `shared/paths.local.yaml`
3. `python3 scripts/deploy/check_paths.py --skill zotero-paper-to-obsidian`
4. `bash deploy.sh --group literature -y`

Literature skills are not symlinked by default; they join the deployment once
paths and a vault are ready.

---

## 1. The pipeline

```
 ┌─────────────┐     ┌─────────────┐     ┌─────────────────────┐
 │ arXiv / URL │────▶│   Zotero    │────▶│      Obsidian       │
 │  (discovery)│     │ (metadata+PDF)│   │ (notes + assets)    │
 └─────────────┘     └──────┬──────┘     └─────────┬───────────┘
                            │                       │
        ┌───────────────────┼───────────────────────┼──────────┐
        │                   ▼                       ▼          │
        │  ┌────────────────────────┐  ┌─────────────────────┐ │
        │  │ zotero-inbox-to-library│  │zotero-paper-to-obsid│ │
        │  │ (triage / classify)    │  │ian (generate note)  │ │
        │  └────────────────────────┘  └─────────────────────┘ │
        │                                                      │
        │  ┌────────────────────────┐                          │
        │  │ obsidian-zotero-tag-   │                          │
        │  │ governance (tags)      │                          │
        │  └────────────────────────┘                          │
        └──────────────────────────────────────────────────────┘
```

Recommended order:

```
import → triage inbox → MinerU parse → Obsidian note → tag audit
    ①          ②              ③              ④
```

| Stage | Skill | Input | Output |
|---|---|---|---|
| ① Triage | `zotero-inbox-to-library` | Zotero inbox collection | folder + tag (`write triage`) |
| ② Parse | `mineru-sync` (cron) **or** `mineru-parse` | PDF | `manifest.json` + `full.md` |
| ③ Note | `zotero-paper-to-obsidian` | MinerU cache / PDF | Obsidian note + `assets/` |
| ④ Tags | `obsidian-zotero-tag-governance` | Zotero items + vault notes | `tag-governance plan → apply → verify` |

---

## 2. Constraints that shape the code

### 2.1 Zotero's local API is writable from version 10

- **Zotero 9.x** — the endpoint on `127.0.0.1:23119` is read-only.
- **Zotero 10+** — native `POST` / `PUT` / `PATCH` / `DELETE`, plus file upload
  and full-text write. One runtime authorization; choosing "Always Allow"
  persists the key to `~/.zotero/zotero/<profile>/localAPIKeys.json`.

There is no official merge or undo endpoint; both are implemented locally
(`write merge`, `write undo`), and merge is reversible.

**Write-safety rule:** the official `DELETE` is permanent — it does not go to the
trash. A reversible delete must use `PATCH {"deleted":1}`, which is what
`write trash` does.

### 2.2 Three traps the wrapper must absorb

The official API guarantees protocol correctness, not data safety. All three
were measured, not assumed:

| Trap | Consequence | Wrapper |
|---|---|---|
| `PATCH` `tags` / `collections` is a **full overwrite** | Hand-rolled "add a tag" returns 204 and silently erases the other tags | `write tag` reads, merges, writes |
| `DELETE` is **permanent** | Bypasses the trash; unrecoverable | Only `write trash` is exposed |
| Every write needs a version precondition | `428` when missing, `412` when stale | Version added automatically, conflict retried |

Tagging is frequent, so a dangerous default has to be removed in the wrapper
rather than remembered at each call site.

### 2.3 MinerU first

PDF extraction prefers the MinerU cache (`manifest.json` + `full.md`); it is far
more structured than reading a PDF directly. The location comes from
`zotero.mineru_cache`.

### 2.4 Path resolution

Every skill must resolve paths through
[`shared/protocols/resolve-paths.md`](../../shared/protocols/resolve-paths.md):
read `paths.local.yaml` → locate the keys `skills.<name>` needs → expand to
absolute paths → verify existence, or stop.

### 2.5 Tags

Every tag value comes from `shared/config/tag-taxonomy-rules.yaml`, in
`namespace/value` form, as a YAML block array. Obsidian and Zotero share one
taxonomy. Bare tags, inline arrays and invented namespaces are rejected.

```bash
python3 scripts/literature/zotero_cli.py tag-governance plan \
  --output /tmp/paper-tag-plan.json --json
python3 scripts/literature/zotero_cli.py tag-governance apply \
  --plan /tmp/paper-tag-plan.json --json
python3 scripts/literature/zotero_cli.py tag-governance verify \
  --plan /tmp/paper-tag-plan.json --json
```

`plan` writes nothing; `apply` checks for plan drift and emits
`<plan>.before.json`; `verify` and a second `plan` should both report no
difference.

### 2.6 Collections are item-centric

The collection–item relation belongs to the item. Operate on the item
(`item.removeFromCollection()` / `item.addToCollection()` + `item.saveTx()`),
never on the collection (`collection.removeItem()` + `collection.saveTx()`).

---

## 3. Skill reference

| Skill | Source spec | Required paths | Key constraint |
|---|---|---|---|
| `zotero-paper-to-obsidian` | `shared/specs/zotero-paper-to-obsidian-ai.md` | `obsidian.vault`, `obsidian.literature_notes` | MinerU first; two-stage load; evidence first; Chinese by default |
| `zotero-inbox-to-library` | `shared/specs/zotero-inbox-to-library-ai.md` | `zotero.local_api`, `zotero.user_id`, `zotero.inbox_collection_key` | Batch via `write triage`; folder routing in the spec |
| `obsidian-zotero-tag-governance` | `shared/specs/obsidian-zotero-tag-governance-ai.md` | `obsidian.vault`, `obsidian.literature_notes`, `zotero.local_api`, `zotero.user_id` | Scan → report → apply (`tag-governance apply`, undoable); audits both sides; one taxonomy |

---

## 4. The CLI

One entry point covers read, write, MinerU, notes and governance:

```
zotero_cli.py
├── read     list / search / metadata / unprocessed
├── MinerU   mineru {grep,list,resolve,outline,read} / mineru-parse / mineru-push
├── preflight paper-status
├── notes    prepare-note / write-note / verify-note / assemble-note / update-section
├── write    write {status,tag,move,collection,trash,restore,merge,triage,
│                  apply,attachment,history,undo}
└── govern   tag-governance / audit-*
```

### Configuration is discovered by default

```
paths.local.yaml  >  environment  >  probe the machine  >  default
```

`shared/paths.local.yaml` is an optional override layer, not a checklist.

| Value | Automatic source |
|---|---|
| `zotero.data_dir` | `dataDir` in `~/.zotero/zotero/*/prefs.js` |
| `zotero.mineru_cache` | `<data_dir>/llm-for-zotero-mineru` |
| `zotero.local_api` | `http://127.0.0.1:23119` (port is fixed) |
| `zotero.user_id` | `0` — "the locally logged-in user" |
| `zotero.inbox_collection_key` | library lookup, name containing `inbox` |
| `obsidian.vault` | scan candidates, score by marker files |
| `obsidian.literature_notes` | `<vault>/02-Literature/ZoteroNotes` |

```bash
python3 scripts/lib/zotero/config.py     # show resolved values and their source
```

The one common override is multiple vaults: notes are **written** to that path,
so guessing wrong edits the wrong library. The program lists the candidates with
`[WARN]` instead of silently picking one.

```
obsidian:
  vault: ~/Documents/Obsidian Vault
```

### Undo is local

The ledger stores real before/after snapshots, not an external action id, so
undo works without any other component being installed:

```bash
write tag K method/x       # snapshot → write → record
write history --last 5     # ↩ full / ~ partial / ✗ not reversible
write undo --last 1 --dry-run
write undo --last 1
```

Three properties make this safe: snapshots are trimmed per field (undoing A does
not drop tags B added later); a conflict is refused rather than forced (a third
party changed the same field); and records without a snapshot are reported as
`✗`, never pretended to be reverts.

---

## 5. MinerU

| Stage | Command | Purpose |
|---|---|---|
| Discover | `paper-status <REF>` | arXiv / DOI / URL / title / key / cache id, four-state |
| Search | `mineru grep` | full-text grep across every cache, offline |
| Outline | `mineru outline` | section structure only, no body text |
| Read | `mineru read --section N --max-chars` | one section, hard character bound |
| Parse | `mineru-parse <REF>` | call MinerU on a cache miss (cloud or local) |
| Automatic | `mineru-sync` | scan PDFs without a cache and parse them (cron) |
| Backfill | `mineru-push <REF>` | index the cache into Zotero's own full-text search |

`mineru-push` joins two halves that otherwise do not meet: Zotero indexes PDFs
with its own extractor and cannot see MinerU's output.

`paper-status` accepts a parent item key, an attachment key, or a cache id. An
attachment key is not in the library index (which holds top-level items only),
so the catalog is asked directly — otherwise a paper already parsed on disk would
be reported `ABSENT` and send an agent to the web.

`mineru-sync` replaces in-process auto-parsing with polling, which is the right
trade: a parse takes seconds to minutes, so sub-second latency has no value, and
polling needs no code inside the Zotero process.

It parses **paper-like item types only** (`journalArticle`, `conferencePaper`,
`preprint`, …) by default. Unattended parsing costs real money, and on this
library most cache-missing PDFs are books. `--all` widens it.

```bash
zotero_cli.py mineru-sync --dry-run        # what would be parsed
zotero_cli.py mineru-sync --limit 5        # bounded, cron-friendly
bash scripts/deploy/schedule.sh enable mineru-sync   # hourly at :20
```

---

## 6. Boundaries

1. **Collection-level `PATCH` is unavailable** — it returns 400, so `triage` and
   `apply` loop one item at a time. Fine at the tens-of-items scale.
2. **The delete-tag endpoint returns 500** — instead, `set_tags` rewrites the
   whole list, which is equivalent.
3. **One authorization is required** — "Always Allow" persists it.
4. **The `llm-for-zotero-mineru` directory name stays** — it is a path on disk;
   renaming it invalidates every existing cache.
5. **`figure_count` needs `content_list.json`** — MinerU stores figure references
   there, not in `full.md`; grepping `full.md` yields a constant 0 and the check
   fails silently.

---

## 7. Related documents

| Document | Contents |
|---|---|
| [`publication-workflow.md`](publication-workflow.md) | Publishing the public subset safely |
| [`../design/philosophy.md`](../design/philosophy.md) | Design principles |
| [`../../shared/specs/literature-note-contract.md`](../../shared/specs/literature-note-contract.md) | What a note must contain |
| [`../../shared/protocols/resolve-paths.md`](../../shared/protocols/resolve-paths.md) | Path resolution protocol |

# arXiv -> Library

Read only after the gate, and only the section you need. The CLI owns
discovery, ranking, dedup, retries, key->id resolution and recommended-list
state. The LLM picks parameters and writes short reasons — never re-sorts,
never re-implements dedup, never hand-writes JS for these tasks.

## 0. Command map

| Intent | Command | Writes Zotero? |
|---|---|---|
| Discover new/recent/classic papers | `arxiv_fetch.py find` | no |
| Import a known ID/URL/DOI/title | `arxiv_fetch.py import` | yes |
| Publish a digest | `arxiv_fetch.py publish` | no |
| Prune items (reversible trash) | `arxiv_fetch.py remove` | yes |
| Inspect keys before a decision | `zotero_cli.py metadata <KEY>` | no |
| Trash / restore by key | `zotero_cli.py write trash` / `restore` | yes |
| Merge duplicates | `zotero_cli.py write merge` | yes, **reversible** |
| Undo recent changes | `zotero_cli.py write undo` | yes |
| Rename / relink / trash a PDF | `zotero_cli.py write attachment` | yes |

`arxiv_fetch.py` is the arXiv-shaped front door; `zotero_cli.py` is the
key-addressed Zotero bridge. Both default to a **preview**; `--yes` applies.
Neither needs the llm-for-zotero plugin: reads use the Local API / SQLite copy,
writes use Zotero 10's native write API.

## 1. Discovery — `find`

One time scope (`--period`):

- `today` — new arXiv RSS papers;
- `recent` — arXiv API search;
- `classic` — OpenAlex high-citation papers.

One scope (`--profile` is the default): `--field robotics|cv|rl|...`,
`--keywords "..."`, or `--cat cs.RO,cs.CV` (`today`/`recent`).

```bash
F=~/scholar-agent/scripts/literature/arxiv_fetch.py
python3 $F find --period today --profile --save-recommended --json
python3 $F find --period recent --keywords "event camera SLAM" \
  --save-recommended --report --output .ai/reports/arxiv-<stamp>.md
python3 $F find --period classic --field robotics --save-recommended --json
```

Always pass `--save-recommended`. Add `--dedup-recommended` to exclude
already-seen papers. Send long output to a file with `--report --output ...`
and show at most **five** results in chat.

## 2. Import

Writes Zotero. Show the target and wait for confirmation unless the user
already asked for the import (`--yes`):

```bash
python3 $F import <ID-or-URL> --json
python3 $F import --title "<title>" --json
```

No arbitrary fuzzy matching: `--title` searches arXiv and imports the first
match, so use it only when the user named the paper. On success the CLI prunes
that paper from the recommended list.

**`import` attaches the PDF.** For an arXiv ID the CLI downloads
`arxiv.org/pdf/<id>` into `data/pdf-cache/` and attaches it as an
`imported_file` child named `Author - Year - Title.pdf`, so the paper arrives
`LOCAL_UNPARSED` (parse it next) rather than metadata-only. What to know:

- A PDF failure **does not fail the import**. The item is still created and
  filed, and the result says `PDF NOT ATTACHED (<reason>)`. Never retry the
  import over this — that creates a duplicate. Supply the PDF another way
  (`write attachment relink`, or the Zotero UI).
- Re-importing an item that has no PDF attaches one; that is how items created
  before this behavior existed get completed.
- `--no-pdf` imports metadata only, on purpose.
- DOIs are never auto-downloaded: publisher PDFs are usually paywalled, and a
  guessed URL returns an HTML page that is not a PDF.
- Zotero is started automatically when it is not running. If it cannot be
  reached, the command fails with the reason rather than a traceback.

An import invalidates the identifier index, so `paper-status` sees the new
attachment immediately instead of answering from a pre-import cache.

## 3. Prune — `remove` (reversible)

Keys are 8-character Zotero item keys (`zotero_cli.py list` prints them).

```bash
python3 $F remove "<KEY>" ["<KEY>"...] --dry-run        # preview, writes nothing
python3 $F remove "<KEY>" [...] --yes                 # trash
python3 $F remove "<KEY>" [...] --restore --yes       # put back
python3 $F remove --undo <ACTION_ID>                # reverse that exact step
```

Guarantees: **all keys resolve before anything mutates** (one bad key aborts
the batch), `--yes` is required, and trashing never erases.

## 4. Dedupe — `merge` (reversible)

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py metadata "<KEY>"...   # compare first
python3 ~/scholar-agent/scripts/literature/zotero_cli.py \
  write merge <MASTER_KEY> <DUP_KEY>...                                     # apply
```

`metadata` prints tags, collections, attachment and note counts per item — that
is the evidence for choosing the master. Prefer the item with the most
attachments/notes/tags, and the one already in the right collection.

A merge folds the donors' tags and collections into the master, re-parents their
notes/attachments onto the master, and **trashes** (not erases) the donors. It is
recorded in the ledger as reversible, so `write undo` brings the donors back out
of the trash and returns the children:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo --last 1 --dry-run
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo --last 1
```

> This is stronger than the old plugin path, whose merge had no inverse. It is
> still not *lossless*: Zotero's native merge deduplicates child objects, which
> this deliberately does not attempt. Treat dedupe as the last step after other
> edits, not the first.

## 5. Undo — `write undo`

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write history --last 5
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo --last 2 --dry-run
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo --last 2
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write undo <ACTION_ID>
```

`--last N` reads the local ledger (`data/zotero-actions.jsonl`), which stores the
**before/after snapshot** of each write. Because the snapshot is ours, undo needs
nothing from Zotero beyond the same public API the write used.

History marks each entry by what can actually be replayed:

```
↩  fully revertible      ~  partial      ✗  cannot revert (no snapshot)
```

Pre-existing entries written by the old plugin mirror carry no snapshot and are
reported as ✗ — they are never silently "reverted".

A **conflict** means the item changed after our write; undo then refuses that
item and says so, because stamping the old value back would silently discard
someone else's edit. That is protection, not failure: the command exits non-zero
and leaves the data alone.

## 6. Repair — `write attachment`

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write attachment rename <ATT_KEY> --name "Author - Year - Title.pdf"
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write attachment relink <ATT_KEY> --path /abs/path.pdf
python3 ~/scholar-agent/scripts/literature/zotero_cli.py write attachment trash <ATT_KEY>
```

Attachments are addressed by their own key. `trash` is reversible (`write restore`);
there is deliberately no permanent-delete verb, because the local API's `DELETE`
erases the row outright without using the trash.

`rename` moves the file on disk as well as the database row. The API's
`filename` field only relabels the row — Zotero does not relocate the stored
file (verified against Zotero 10.0.5), so a row-only rename leaves the two
disagreeing and the next local read fails with "PDF file not found (linked
attachment?)". If the stored file cannot be found, `rename` still corrects the
row and reports `fileMoved: false`. A name that is a path (`../x.pdf`) is
refused, as is a rename onto an existing file.

## 7. Token and output rules

1. **Always pass `--quiet`** for agent runs; combine with `--json` when a later
   step parses the result. Progress lines crowd the transcript, errors still
   print.
2. **Never paste full JSON or a full report into chat.** Write it to
   `.ai/reports/` and return the absolute path plus a ≤5-line summary.
3. **Never re-derive** what the CLI already computed — no re-sorting, no manual
   dedup, no re-counting.
4. **Preview before writing.** `write undo --dry-run`, `remove --dry-run` and
   `import` (prompts unless `--yes`) all exist for this. Do not reach for the
   apply flag first.
5. **Batch in one call** — `write triage --plan` and `write apply --plan` take a
   whole batch, so do not loop per item.
6. Report the `actionId` you actually received, never an invented one.

## 8. Boundaries

The LLM chooses parameters and writes concise relevance reasons. It must not
invent metadata, must not claim an import/merge/undo succeeded when the command
failed, and must not hand-write HTTP against Zotero.

Two traps the CLI exists to absorb — do not work around them by calling the API
directly:

- `PATCH` treats `tags`/`collections` as a **complete list**, so "adding" a tag
  by hand silently erases the others. Use `write tag` / `write triage`.
- `DELETE` on the local API is **permanent** and bypasses the trash. Use
  `write trash`.

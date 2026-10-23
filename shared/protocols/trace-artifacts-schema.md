# .ai/research Schema — Agent & Maintainer Reference

> Read this when: a `.ai/research/` path behaves unexpectedly, or you are changing
> `research.sh`. Source of truth: `scripts/skills/research.sh` (single file,
> bash + python3/yaml).

## Purpose

Persist the artifacts of a `/research` run (including trace and plan modes) so a
later session can reuse them, avoid duplicate work, and detect staleness. The
agent only reads and writes through `research.sh`; it never manipulates
`.ai/research/` directly.

## Layout (single source of truth = `sessions/<SID>/meta.yaml`)

```
<repo>/.ai/research/
├── sessions/<SID>/
│   ├── meta.yaml         # status, layers_completed, files (script-managed)
│   └── chain.md          # layered findings (## L<n>) + final report (markdown)
└── .archive/             # gc destination
```

There is no index file: `query`/`list` scan `meta.yaml`, and `append` writes to
`chain.md`. **SID** = `YYYY-MM-DD_<slug>`; a second session for the same target
on the same day gets `_N`.

## Interface contract (`research.sh`)

| Subcommand | stdin | stdout | exit |
|--------|-------|--------|------|
| `gate --intent research\|trace [--root]` | — | `OK` + `READ=`/`READ_AT_L4=`/`FALLBACK` | 0 |
| `query <kw> [--root]` | — | `HIT <id> status=… stale=… age=Nd` + `FILES` + `RESUME_FROM`/`HINT` / `MISS` | 0 |
| `start <target> --type T --source S [--root]` | — | `SESSION=<id> PATH=<dir>` | 0 |
| `append <SID> --layer N` | one-line JSON | `OK layers=[…]` | 0 |
| `save-report <SID>` | Markdown | `OK file=chain.md` (appends a `## Report` section) | 0 |
| `complete <SID>` | — | `OK status=complete` | 0 |
| `read-chain <SID> [--summary\|--full] [--project NAME]` | — | `CHAIN=<abs>` + `## L` headings (summary) / full text | 0 |
| `list [--root\|--all]` | — | TSV rows: project/id/source/status/stale/age | 0 |
| `search-all <kw> [--since Nd] [--source S]` | — | `list` format + `TOTAL=n FRESH=x STALE=y` / `MISS` | 0 |
| `check-stale [--root\|--all]` | — | one `<id> STALE\|FRESH` per line | 0 |
| `gc [--max-age N] [--root\|--all]` | — | `ARCHIVED <id> (xKB)` or nothing | 0 |

Removed: `init` (`start` creates the session) and `rebuild-index` (no index).
Errors go to stderr with exit 1; the agent does not parse stderr. Layer numbers:
`L1–L5` for research, `S1–S5` for plan mode (`S5` = Change Plan).

## append JSON → chain.md rendering

```json
{"layer":1,"action":"locate files","files":["a.py"],"insight":"…","summary":"…"}
```
→
```markdown
## L1 — locate files
- files: a.py
- insight: …
- summary: …
```
`meta.yaml` also gets the layer appended to `layers_completed` (deduplicated) and
the paths merged into `files` (deduplicated).

## State machine and staleness

```
incomplete → complete → stale → gc
```

**Staleness** (one implementation, `is_stale`):
- git repo: a tracked file in `files` has a commit newer than `created` → stale;
  a tracked file was deleted → stale
- non-git: any path in `meta.yaml` `files` no longer exists → stale

**GC** (`--max-age`, default 30 days): status ≠ incomplete and stale, or status
superseded, and older than the limit → the whole directory is `mv`-ed into
`.archive/`.

## Edge cases

| Situation | Behaviour |
|------|------|
| Project has no `.ai/research/` | `query` returns MISS; `start` creates it |
| Malformed JSON on append | exit 1 + stderr |
| `meta.yaml` missing a field | `meta_field` returns empty, no abort (`set -e` safe) |
| A `repo-path-index` path is missing | `search-all`/`list`/`gc` skip it |

## External dependencies

- `repo-path-index.local.yaml` — the cross-project registry behind `--all` and
  `search-all`; `start` registers automatically
- `trace-ros-topology.sh` — trace L4 tool
- python3 + pyyaml — `meta.yaml` read/write (parsing YAML in bash is unreliable,
  so all of it goes through python)

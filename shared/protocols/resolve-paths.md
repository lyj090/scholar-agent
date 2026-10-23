# Path Resolution Protocol

> Read this when: a skill needs a Zotero or Obsidian location, or a path check
> fails. Referenced by: `zotero-inbox-to-library`, `obsidian-zotero-tag-governance`,
> `deploy.sh`, `check_paths.py`.

## At runtime: do nothing

**Everything is auto-discovered.** A config file is only an optional override.
Resolution order:

```
paths.local.yaml  >  environment  >  machine probing  >  defaults
```

| Value | Source |
|---|---|
| `zotero.data_dir` | `dataDir` in `~/.zotero/zotero/*/prefs.js` |
| `zotero.mineru_cache` | `<data_dir>/llm-for-zotero-mineru` |
| `zotero.local_api` | `http://127.0.0.1:23119` (port is fixed) |
| `zotero.user_id` | `0`, Zotero's "logged-in local user" alias |
| `zotero.inbox_collection_key` | API lookup, by a collection name containing `inbox` |
| `obsidian.vault` | candidate scan, scored by marker file/subdirectory |
| `obsidian.literature_notes` | `<vault>/02-Literature/ZoteroNotes` |

**A new machine needs no YAML.** To see the resolved values and where each came
from:

```bash
python3 scripts/lib/zotero/config.py
```

## When an override is needed

Two cases only.

**1. Several vaults score equally.** Notes get *written*, so a wrong guess
writes into the wrong vault. Pin it explicitly:

```yaml
obsidian:
  vault: ~/Documents/Obsidian Vault
```

Without a pin the program prints a `[WARN]` listing the tied candidates and the
fix, rather than silently choosing.

**2. The inbox has no "inbox" in its name,** or a real numeric `user_id` is
needed:

```yaml
zotero:
  inbox_collection_key: ABC12345
  user_id: "0"
```

Environment variables substitute for YAML in scripts:
`ZOTERO_DATA_DIR`, `ZOTERO_LOCAL_API`, `ZOTERO_USER_ID`, `ZOTERO_MINERU_CACHE`,
`ZOTERO_INBOX_KEY`, `OBSIDIAN_VAULT`, `OBSIDIAN_LITERATURE_NOTES`.

`shared/paths.local.yaml` is gitignored and never tracked.

## Validation

```bash
python3 scripts/deploy/check_paths.py --strict-core        # vault reachable
python3 scripts/deploy/check_paths.py --skill <name>       # one skill's requirements
python3 scripts/deploy/check_paths.py                      # everything resolved
```

Per-skill requirements live in the tracked
[`shared/config/skill-paths.yaml`](../config/skill-paths.yaml), so they are the
same on every clone. A skill declaring `[]` runs anywhere and is not blocked by a
missing config file.

`check_paths` and the runtime **share one resolver** (`lib/zotero/config.py`), so
"the tool says it is unconfigured but the CLI works" cannot happen.

## First-time setup

Usually **unnecessary**. When discovery picks wrong (tied vaults), use
`setup_paths.py`:

| Command | Behaviour |
|------|------|
| `--discover` | list vault candidates and scores |
| `--apply` | choose interactively, write `paths.local.yaml` |
| `--apply --yes` | pick the single highest-scoring candidate automatically |
| `--apply --vault ~/path` | skip discovery, name the vault |
| `--force` | overwrite an existing `obsidian.vault` |

```bash
python3 scripts/deploy/setup_paths.py --discover    # see candidates first
python3 scripts/deploy/setup_paths.py --apply       # writes the vault value
python3 scripts/deploy/check_paths.py --strict-core
bash deploy.sh --group literature -y
```

`deploy.sh` never writes this file for you: a config created as a side effect of
deploying is a config nobody reviewed. It fails with the command to run instead.

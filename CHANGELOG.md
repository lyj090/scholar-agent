# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `bash scripts/agent.sh doctor` — a read-only self-check that reports each
  capability as ready / partial / off and names the fix. It distinguishes "the
  literature flow is not configured", which is a valid install, from "the core
  is broken", which is not.
- GitHub Actions CI: the suite runs on Python 3.10 and 3.12 on Linux, on
  Windows, and a documentation job that enforces bilingual README parity,
  working internal links, and no tracked machine-local files.
- Issue and pull-request templates.
- Tests that keep the two READMEs in step and stop documentation links from
  rotting: both languages must keep the same section structure, every internal
  link must resolve, and machine-local files must stay out of the public tree.
- `scripts/deploy/build-public.sh` — builds the public branch from the private
  working tree as a single orphan commit, with one explicit exclusion list.
- `BRANCHES.md` — the two-branch model, what is excluded and why, and how to
  publish without leaking history.

### Changed

- **Output budget is one rule, not three.** ">60 lines → write a file", "<300
  tokens per turn" and "≤15 lines per Read" were all mandatory and mutually
  unachievable — ~300 tokens is about 15 lines, so the 60-line rule could never
  fire. Replaced by: answer in chat, persist evidence. No threshold to count.
- Per-skill path requirements moved out of `paths.local.yaml` (gitignored, so
  unknowable on a fresh clone) into the tracked
  `shared/config/skill-paths.yaml`. `check_paths.py --skill` used to return OK
  for a vault path that does not exist; it now fails, and an undeclared skill is
  an error rather than an implicit pass.
- The permission hook became opt-in (`deploy.sh --permission-hook`). It records
  command text, and a shell-history recorder is not a reasonable default.
- The shell-environment section became opt-in (`--shell-env`): it describes one
  machine's aliases and should not be merged into every reader's global prompt.
- `shared/config/allowlist-common.json` lost 19 unbounded rules (`rm *`,
  `source *`, `sed *`, `zsh -i -c *`, …) and gained a 13-entry deny layer, which
  `sync-allowlist.sh` previously never synchronized at all.
- `sync-allowlist.sh` merges `hooks.PermissionRequest` instead of replacing it,
  and is idempotent. It used to delete any hook the user already had, on every
  deploy.
- `deploy.sh` no longer writes `paths.local.yaml` as a side effect of deploying,
  and no longer "repairs" paths by rewriting them. A config nobody reviewed is
  worse than a loud failure.
- The trace spec describes tool-call shape instead of token arithmetic; the
  "~70% / ~91% reduction" and "~7% of fetches were cached" figures were
  unmeasurable from inside a session and are gone.
- Two Chinese protocols translated; `CONTRIBUTING.md` states the language rule
  and the reason for it.
- `docs/design/philosophy.md` records contributor-facing reasoning instead of
  restating the README, and no longer claims `deploy.sh` auto-configures paths.

### Changed

- **The always-loaded layer is now an index.** `global-config/claude-global.md`
  3006→2132 B, `dsh-global.md` 2828→2396 B, repo `AGENTS.md` 1872→1175 B, and the
  six `SKILL.md` files 4875→3603 B. Each global file used to restate the protocol
  it linked; the duplicate is always the copy that goes stale. Sizes are asserted
  in tests, because "keep it short" is not measurable and a byte budget is.
- **Progressive disclosure actually defers now.** The per-skill gate emitted
  `READ=` only under `--verbose`, so a compact run named no spec at all and the
  agent could not know one existed; meanwhile `zotero-paper-to-obsidian` emitted
  `READ_AT_STEP3=<abs path>` at gate time, inviting the writing rules before
  there was anything to write. Compact output now always carries
  `SPEC=<path>`; `--verbose` promotes it to `READ=` and reveals later steps.
- `zotero-paper-to-obsidian/SKILL.md` no longer paraphrases its own spec. Two
  copies of a rule drift, and the skill's copy was the shorter, less precise one.
- `test-skill-consistency.py` accepts `agent.sh gate <skill>` as well as
  `skill-gate.sh <skill>`. It was asserting the implementation name, so a skill
  documenting the documented entry point failed.

### Removed

- The five generated platform trees (`claude-skills/`, `cursor-skills/`,
  `codex-skills/`, `grok-skills/`, `dsh-skills/`) — 54 tracked files that were
  build output. `deploy.sh` now generates them by default (a fresh clone has
  none) and all three CI jobs run the generator, so a stale tree cannot ship.
- `scripts/notion/` — an integration that needed a private `NOTION_TOKEN`, had no
  test, and was not referenced by any skill.
- `scripts/deploy.sh` and `scripts/skill-gate.sh` — forwarding stubs with no
  remaining caller.
- `shared/workflows-map.md` — nothing linked it; its content mapped one person's
  vault structure onto skills.
- `docs/skill-index.md` — duplicated the README skill table; its useful links
  moved into README § Documentation.
- `.vscode/settings.json` — IDE state, not repository configuration.

### Changed

- **`paths.local.yaml` is treated as the optional override it is.** Every path
  check required the file to *exist* before resolving anything, so a machine
  whose vault and Zotero were both auto-discovered failed all four literature
  skills — while an empty file, carrying strictly less information, passed.
  Presence was being tested instead of readiness. `check_skill`, `check_key`,
  `compute_machine_state`, `--strict-core`, `--strict-literature`, the
  `literature` auto-group precondition and `deploy.sh` all now judge the
  resolved values, and a genuinely unconfigured machine still fails.
- `deploy.sh` generates the skill trees by default, so `--undeploy` no longer
  trips the "cannot be combined with --generate" guard. Only an explicitly
  passed `--generate` conflicts; a default that disables a command is a bug.

### Fixed

- `deploy.sh --undeploy` now removes managed links from all five platforms. The
  guard above meant it exited 2 and cleaned nothing, leaving every agent with
  stale links to a tree that may no longer exist.

### Removed

- `scripts/bench-research.py` — a 24 KB orphan whose headline claim was
  "saves ~11942 tokens (100%)", computed against a hardcoded
  `AD_HOC_TOKENS = 12000`. That is the same unmeasurable-target failure the
  repository had just removed from its protocols, so the tool that produced it
  goes too.

### Fixed

- Three tests reached a real library through `load_config()`, which hard-exits
  when nothing is configured. The suite therefore failed on every fresh machine
  and passed only on the maintainer's, so the CI badge would have been red from
  the first commit. They now skip, and `arxiv_fetch.py find` no longer prints
  the Zotero setup block it cannot act on.
- Embedding retrieval was imported at module load, so on an install without
  `requirements-retrieval.txt` (no torch, sentence-transformers or numpy)
  **every** arXiv command died with a traceback — including `find`, which only
  needs BM25. The import is now optional and the one feature that requires it
  reports how to install it.
- `scripts/tests/test-skill-gate.sh` leaked a temporary directory on every run;
  its cleanup trap did not cover the directory created for the ROS2 fixture.

### Removed

- The repository-level `.claude/settings.json`. It only mirrored permission
  globs that belong in the user-level `~/.claude/settings.json`; shipping a
  project allowlist makes a repository grant itself permissions. The
  portability check no longer expects the file.

### Changed

- Documentation consolidated. `pipeline-overview.md`, `unified-agent-entry.md`
  and `local-first-extraction.md` (563 lines, heavily overlapping) became
  `docs/literature/architecture.md` (268 lines), which describes the current
  system only. The history — why the Zotero plugin was removed, how the CLI was
  consolidated — moved to `docs/devlog/`, which is excluded from the public
  branch.

### Fixed

- `paper-status` no longer aborts when the library index cannot be built (no
  Zotero database and no API — a fresh clone, or CI). It degrades to answering
  from local caches, which is the only source those states need. Before, an
  unreadable library became "cannot answer", and the natural recovery from an
  exception in an agent loop is a needless web fetch.
- `deploy.sh --target <dir>` no longer writes outside that directory. It used
  to repoint the user's real `~/scholar-agent` symlink at the target and write
  DSH skill links into `$HOME/.dsh/skills`, so a test deploy into a scratch tree
  hijacked the live setup.
- The READMEs told readers to run `./agent.sh`, which does not exist at the
  repository root; the entry point is `scripts/agent.sh`.

## [0.1.0] — 2026-10-23

First public release.

### Added

- Six skills as a single source, generated into Codex, Cursor, Claude Code,
  Grok and Agent Harness trees:
  `paper-lookup`, `arxiv-to-library`, `zotero-inbox-to-library`,
  `zotero-paper-to-obsidian`, `obsidian-zotero-tag-governance`, `research`.
- Local-first paper lookup: resolve against the local library before any
  network fetch, and say so when the library is unavailable.
- Zotero 10 native local-API client — reads, writes, collections, trash,
  merge and undo — with no plugin required.
- MinerU extraction (cloud v4 or self-hosted) for figures, tables and
  equations, with a hash-bound figure ledger so numbering cannot drift.
- arXiv discovery with BM25 and embedding retrieval, rank fusion, and
  optional LLM filtering.
- Privacy split enforced at build time: an explicit marker cuts a private
  tail, and the publication audit rejects anything that slipped through.
- A gate that emits the exact next action (`RUN=`, `READ=`, `MSG=`) instead
  of loading whole manuals into context.

### Known limitations

- Zotero 9 is unsupported for writing — its local API is read-only.
- No packaging. Clone the repository; it is not a `pip install` target.

[Unreleased]: ../../compare/v0.1.0...HEAD
[0.1.0]: ../../releases/tag/v0.1.0

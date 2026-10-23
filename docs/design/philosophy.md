# Design Philosophy

This document captures the stable, high-level principles behind scholar-agent.
Specific technical decisions live in `docs/design/`.

---

## Why this document exists

The user-facing rationale is in [`README.md`](../../README.md) § *Why this exists*.
This file records the principles a contributor has to agree with before changing
the architecture — the reasoning behind decisions that otherwise look arbitrary.

The starting constraint: five assistants (Claude Code, Cursor, Codex CLI, Grok
CLI, Agent Harness) each have their own skill format, while the workflow
knowledge behind them is identical. Divergent copies drift, so the repository
keeps one source and generates the rest.

---

## Core Principles

### 1. Context is Budget

Every byte injected into an LLM's context window has a cost: latency, cache miss, and reduced room for actual work. Design every artifact — CLAUDE.md, skill files, MCP schemas — as if you're spending a limited budget.

Implications:
- Skills are lazy-loaded (slash command invocation), never pre-injected.
- MCP servers are scoped to the projects that need them, not global.
- Long outputs are collapsed; only summaries enter conversation history.
- Shared docs are split so a skill reads only what it needs, not the whole file.

### 2. Lazy Load over Eager Load

Nothing should be present in a session unless it's actively needed.

- Skill content enters context only when the user invokes `/command`.
- `CLAUDE.md` stays lean; details live in `shared/` and are pulled on demand.

### 3. Single Source, Multiple Targets

Workflow logic is authored once (in `skills/` or `shared/`), then generated into tool-specific formats. Duplication is a bug, not a feature.

- `shared/*.md` — long-form workflow docs referenced by all supported tools.
- `skills/<name>/SKILL.md` + `meta.yaml` — canonical definition per skill.
- `scripts/deploy/generate.sh` — produces `claude-skills/`, `cursor-skills/`, `codex-skills/`, `grok-skills/`, `dsh-skills/`.

### 4. Explicit Paths, Assisted Discovery

Machine-specific paths live in `shared/paths.local.yaml` (gitignored). The system does **not** infer Zotero collection keys, `user_id`, or arbitrary repo roots at skill runtime.

**`setup_paths.py`** is the only assisted discovery step:

- Scans configured roots for **Obsidian vault candidates** (scored heuristics; built-in defaults, overridable in `shared/paths-discovery.local.yaml`)
- Writes `paths.local.yaml` from `paths.example.yaml`, filling `obsidian.*` and default `zotero.local_api`
- Requires an explicit `--apply`; vault selection needs interaction, `--vault <path>`, or `--yes` when a single high-confidence candidate exists

`deploy.sh` may call `setup_paths.py --apply --yes` when `paths.local.yaml` is missing — it creates the file or fails loudly. Literature skills still deploy only after per-skill `check_paths.py` gates pass.

Why not full auto-detection: hidden state fails silently across machines; explicit config + loud failures are easier to debug.

### 5. Graceful Degradation

A missing optional component should not break the system — it should reduce scope.

- No `paths.local.yaml`? Core skills deploy; literature skills are **not symlinked** until paths are ready (zero overhead).
- Literature paths partially configured? Per-skill deploy gate skips only the skills that fail `check_paths --skill`; others link normally.
- A skill's `shared/` reference is broken? `lint-skills.sh` catches it; other skills are unaffected.

### 6. Native Commit Flow

Commit requests use plain git: `git status`/`git diff` → draft a Conventional
Commits message → show files + message → user confirms → `git add` selectively
and commit. No ownership-prefix script; the previous tag system
(`(Claude)(Shared)…`) was removed in 2026-09 — it cluttered subject lines and
the grouping was already visible in the diff.

---

## Non-Goals

- **Full automation of setup** — we prefer explicit over magic.
- **Supporting arbitrary AI tools** — primary targets are the five configured platform adapters.
- **Runtime skill discovery** — skills are statically deployed, not dynamically resolved.

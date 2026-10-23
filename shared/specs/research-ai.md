# research — Agent Spec

Structured code research with persistent artifacts. Explore systematically, persist findings to `.ai/research/`, keep chat minimal.

---

## Hard Gates

1. **Run `skill-gate.sh research` first** — only Read paths from gate `READ=` output.
2. **Persist via `research.sh`** — never write under `.ai/research/` directly.
3. Target repo must have `.ai/` in `.gitignore`. If missing: `bash ~/scholar-agent/scripts/deploy/init-ai-contract.sh <repo>` first.
4. **Output routing**: chat gets the conclusion; the chain stays in `.ai/research/`
   (`shared/protocols/context-budget-protocol.md`).

Routing: `~/scholar-agent/shared/protocols/skill-routing-ai.md`

---

## research.sh — core commands

| Command | Purpose |
|---|---|
| `gate [--intent research\|trace] [--root .]` | **Step 0.** Which spec files to Read; ROS2 check for trace |
| `query <kw> [--root .]` | Prior session `HIT`/`MISS` + `RESUME_FROM` hint |
| `start <slug> --type <...> --root .` | New session → `SESSION=<id>` |
| `append <SID> --layer N` | Pipe JSON discovery (→ chain.md `## L<n>` section) |
| `save-report <SID>` | Pipe final markdown (→ chain.md `## Report` section) |
| `complete <SID>` | Mark done |

Other commands: `read-chain`, `search-all`, `check-stale`, `gc`, `list`.
Run `bash ~/scholar-agent/scripts/skills/research.sh` (no args) for full help.

Skill routing table: `~/scholar-agent/shared/protocols/skill-routing-ai.md`

---

## Flow

### 0. Gate (mandatory)

```bash
bash ~/scholar-agent/scripts/skills/skill-gate.sh research --root . [--intent research|trace]
```

Read only gate `READ=` lines. For trace, defer `READ_AT_L4=` until Layer 4.

### 1. Step 0 — query before start (mandatory)

```bash
bash ~/scholar-agent/scripts/skills/research.sh query "<main_keyword>" --root .
```

| Result | Action |
|---|---|
| `MISS` | `start <slug> --type research --source user-research-cmd --root .` |
| `HIT … status=incomplete stale=false` | Resume — use `read-chain <SID> --summary` then append |
| `HIT … stale=true` | `read-chain <SID> --summary`, verify changed files, rerun from L1 |
| `HIT … status=complete` | `read-chain <SID> --summary` only; start new session if scope differs |

**Do NOT** Read full `chain.md` unless `--summary` is insufficient — then `read-chain <SID> --full`.

### 2. Explore → append (iterative)

Locate with grep, then read only that region. After each meaningful discovery:

```bash
echo '{"layer":N,"action":"...","files":["..."],"insight":"...","summary":"..."}' \
  | bash ~/scholar-agent/scripts/skills/research.sh append <SID> --layer N --root .
```

Each append writes a `## L<n> — <action>` section into `chain.md` and updates
the session meta. Layer semantics are a shared vocabulary (not a hard ritual):

| Layer | Meaning |
|---|---|
| L1 | Locations — files / symbols touched |
| L2 | Signatures — APIs / contracts |
| L3 | Data flow — call graph, state transitions |
| L4 | Cross-boundary — IPC / network / RPC |
| L5 | Synthesis — decision / Change Plan |

### 3. Save report

```bash
<full report markdown> | bash ~/scholar-agent/scripts/skills/research.sh save-report <SID> --root .
```

### 4. Chat output

Report the finding and the path. No fixed template — a template you have to fill
in for every turn is a cost, not a discipline.

```
Finding: <one sentence>
Path: .ai/research/sessions/<SID>/chain.md
```

### 5. Complete

```bash
bash ~/scholar-agent/scripts/skills/research.sh complete <SID> --root .
```

---

## Trace mode (ROS2 only)

When gate returns `OK intent=trace ros2=true`:

1. Read `research-trace-core.md` (from gate `READ=`).
2. Follow L0–L3 in that file; session: `--type trace --source research-trace`.
3. At L4, Read `research-trace-report.md` (from gate `READ_AT_L4=`); use `trace-ros-topology.sh` per `TOOL_L4=`.

Do **not** use trace mode on dotfiles, shell, or generic Python repos — gate will fallback to research.

---

## Plan mode (multi-file changes)

Same session flow, plus approval gates:

- L5 layer = Change Plan. Before any edit, present the plan (files + actions)
  and **wait for explicit user approval** — no mutations before approval.
- Execute the approved plan stepwise; report intermediate progress.
- After verification (tests/lint/build), finalize:
  `bash ~/scholar-agent/scripts/skills/research.sh complete <SID> --root .`
- If the plan becomes stale (new commits, user changed mind), re-run Phase 1.

---

## Commit workflow (native, no script)

For an explicit commit request:

1. `git status` + `git diff` (staged & unstaged) — the files ARE the scope.
2. `git log -5 --oneline` for style; default to Conventional Commits
   (`feat|fix|refactor|docs|chore(scope): summary`), repo style wins.
3. Draft the message, show it with the changed files, wait for confirmation.
4. `git add <paths>` selectively (never blanket-add unrelated dirty files),
   then `git commit -m`. Never push unless asked.

---

## Rules

- Conclusion in chat, evidence in `.ai/research/` — see
  `shared/protocols/context-budget-protocol.md`. No per-turn token target.
- Read the section you need; do not paste findings into chat.
- One `append` per discovery (not one per read).

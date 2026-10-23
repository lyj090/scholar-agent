# skill-routing — Agent Convention

> When to invoke a skill vs native tools. Run `skill-gate.sh` before reading any spec.

## Gate

```bash
bash ~/scholar-agent/scripts/agent.sh gate <skill-name> [--root .]
```

`agent.sh gate` is the documented entry point and forwards to
`scripts/skills/skill-gate.sh`; either name works, prefer `agent.sh`.

Execute `RUN=`. Read the file named by `SPEC=` only when the `RUN=` output was
not enough to act on; defer `READ_AT_*` / `READ_WITH=` until the step they name.

The default output is deliberately compact — a path check, one `RUN=`, and a
`SPEC=` pointer. `--verbose` promotes `SPEC=` to an absolute `READ=` and reveals
the later-step files. A gate that emits everything up front is not progressive
disclosure; a deferral the agent cannot see is a dead end.

## Script output (context budget)

Skill scripts print **≤6 lines** of `KEY=value` to stdout. Agent: summarize in chat; do not paste verbatim.

| Line | Meaning |
|------|---------|
| `OK ...` | Success; proceed |
| `ERR ...` | Failed; read `MSG=` / `FIX=` |
| `READ=` / `RUN=` | Next action |
| `SPEC=` | The spec behind this skill, read on demand |
| `MSG=` | One-line human hint |
| `FIX=` | One setup command (paths errors) |

Full JSON / file lists: add `--json` or `--verbose` explicitly.

## Selection table

| User intent | Use | Do not use |
|-------------|-----|------------|
| Single lookup ("where is X defined?") | grep, then read that region | `/research` |
| Multi-file architecture / data flow (non-ROS2) | `/research <topic>` → `skill-gate.sh research` | trace mode |
| ROS2 symbol / topic / control chain | `/research trace <target>` → `--intent trace` | deleted `/trace-code` |
| Multi-file change + approval gate | `/research` plan mode (user explicit) | auto planning |
| Commit request | native git: status/diff → draft Conventional Commits msg → confirm → commit | scripts |
| Token usage check | platform-native usage panel | paste transcripts into chat |
| Zotero / Obsidian pipelines | literature skill via `skill-gate.sh` | read spec when gate returns `ERR paths_not_ready` |
| Reading/citing/verifying a paper (arXiv ID, DOI, URL) | `paper-status` preflight → local cache | `web_fetch` before preflight |
| "Do I have work on X?" (literature recall) | `mineru grep "<topic>"` (local corpus) | `web_search` first |
| Cache miss / need to parse a local PDF | `mineru-parse "<ref>"` (no GUI needed) | waiting for the Zotero plugin UI |

## Paper preflight (local-first)

Before fetching any paper from the web, run:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py paper-status "<ref>"
```

`STATE=LOCAL_READY` → read the local cache and do **not** fetch. Only
`STATE=ABSENT` justifies an external fetch. Full protocol:
`~/scholar-agent/shared/protocols/literature-preflight.md`.

## Hard rules

1. **No spec before gate** — workflow skills run `skill-gate.sh` first (or `research.sh query` for L0 only).
2. **Read budget** — ≤2 spec files per turn; trace report only at L4.
3. **Invocation is declared per skill** — `skills/*/meta.yaml` owns `invocation`,
   and generators derive each platform's policy from it. `explicit` (most
   skills) fires only on a slash command or a direct request for that workflow.
   `natural` fires when the task matches its description; today only
   `paper-lookup` does, because "do I already have this paper?" is a question,
   not a command. Complexity by itself never triggers a skill.
5. **Machine output** — use compact JSON from script-first commands; do not pretty-print or paste full reports into chat.
6. **No deployment in a gate** — installing or syncing agents belongs to deploy scripts, not runtime routing.
7. **Flag vocabulary** — `--dry-run` writes nothing; `--yes` skips a confirmation
   prompt; `--force` overwrites an existing artifact. They are distinct and never
   implied by each other, so `--yes` never silently overwrites.

## Broad search offload

Task `subagent_type=explore` (readonly) or explicit `/research`; the main agent assigns clear scopes and integrates the results.

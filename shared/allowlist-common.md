<!--
  allowlist-common.md — Governance for the cross-device allowlist baseline.
  When/why to read: editing shared/config/allowlist-common.json, or deciding
  whether a new permission rule should be shared or kept local.
-->

# allowlist-common — cross-device rule governance

## What this file governs

`shared/config/allowlist-common.json` is the Claude Code permission baseline
every machine with scholar-agent receives.

`scripts/allowlist/sync-allowlist.sh`, called by `deploy.sh`, merges it into
`~/.claude/settings.json`:

```
machine.allowedTools = shared.allowedTools ∪ local-extras
machine.deniedTools  = machine.deniedTools ∪ shared.deniedTools
local-extras = current.allowedTools − previous-managed-snapshot
```

A rule added to shared arrives on the next deploy; a rule deleted from shared
disappears; anything the user added locally survives. Deny rules are only ever
added, never pruned — dropping a deny this script did not create would silently
widen permissions.

## This is not a security boundary

Worth stating plainly, because a file full of `Bash(...)` patterns implies
otherwise: it is a convenience baseline for one user's machine. It cannot stop an
agent that proposes a command you have not pre-approved, and `deniedTools` only
catches the shapes listed there. Friction reduction, not a sandbox.

The one rule that matters when editing it: **no unbounded grants.** A rule like
`Bash(rm *)`, `Bash(source *)` or `Bash(zsh -i -c *)` makes every other entry
decorative, because it can run anything. `Bash(python3 *)` is the documented
exception — every script here is invoked that way, and a baseline that prompts on
all of them teaches the user to click "always allow", a worse habit than the one
this file prevents.

## shared or local

| Situation | Where |
|---|---|
| A command common to any Linux dev machine | shared |
| Path is `~` or relative | shared |
| Useful to most people using these skills | shared |
| Contains `/home/<user>/` or another absolute path | local |
| Names a local service port (`localhost:23119`) | local |
| Invokes a script unique to one project | local |
| Depends on this host's hardware or desktop (`fcitx5`, `kitty`) | local |

## Adding a rule

```bash
bash scripts/allowlist/permission-prompt-stats.sh review
```

It lists patterns above a threshold (default 3) and asks, per pattern:
`s`hared / `l`ocal / `k`ip / `d`elete. Choosing `s` appends to
`shared/config/allowlist-common.json`; commit it, and other machines pick it up
on their next `deploy.sh` + `git pull`.

Hand-editing the JSON is fine — keep it valid and deduplicated.

## Removing a shared rule

Delete it from `shared/config/allowlist-common.json` and commit. The next
`deploy.sh` removes it from the machine too, provided it originally came from
shared; a same-named local rule is kept.

## The recording hook

`global-config/permission-counter.sh` records the text of commands that trigger a
permission prompt, into `~/.claude/permission-prompt-counts.json`, so that
`permission-prompt-stats.sh` can suggest rules worth adding.

It is **opt-in** (`deploy.sh --permission-hook`) and off by default: it is a
shell-history recorder, and command lines carry paths, tokens and queries.

## Maintenance

`scripts/allowlist/audit-allowlist.sh` finds and cleans redundancy without hand
editing:

| Subcommand | Purpose |
|---|---|
| `tree` | discover every Claude config and allowlist file (depth 3), handling both the `.allowedTools` and `.permissions.allow` schemas |
| `audit` (default) | report parent-rule redundancy (`Bash(ps *)` ⊇ `Bash(ps aux)`), exact duplicates, shared∩local overlap, stale `counts.json` entries |
| `fix [--shared\|--local\|--all] [--apply]` | delete redundant entries; dry-run by default, backs up to `~/.claude/backups/` before writing |
| `cross [--apply]` | drop local rules already covered by a shared parent rule; suggest promoting generally-useful ones |
| `project [--root DIR] [--apply]` | clean project-level allowlists (`.claude/settings*.json`), leaving `deny`/`ask`/MCP keys untouched |
| `promote [--apply]` | move general local rules into shared, filtering host-specific ones out |

Coverage is judged by anchoring each rule as a regex (`*` → `.*`, rest escaped):
A covers B when A's regex matches B exactly and A ≠ B. `Bash(docker ps)` and
`Bash(docker ps *)` are both kept — the latter requires an argument.

Typical loop: `audit` → `fix` (dry-run) → `fix --apply` → commit if shared
changed.

Longer-term upkeep:

- **System level** — run `cross` / `promote` periodically to clear local rules
  shared already covers and lift generally-useful ones into shared.
- **Project level** — `project --root <dir> --apply` for one project. An active
  project may have another session writing `settings.local.json`, so clean it
  while idle or the rules come back.
- **Why it is periodic, not instant** — clicking "always allow" in Claude does
  not pass through this script, so deduplication happens on a schedule instead of
  at the moment of writing.

## Daily cleanup

`scripts/allowlist/allowlist-daily-clean.sh` uses the **system crontab** (not a
session-internal scheduler) to run `fix --apply` and `project --apply` daily.

- **Why cron** — cleaning is pure shell and needs no model. System cron runs
  whether or not Claude is open; an in-session scheduler only fires while the
  session is idle, the wrong time for background maintenance.
- **Install** — `allowlist-daily-clean.sh --install 09:00` (`:00` shifts to
  `:07` to avoid the top of the hour) / `--uninstall`. Idempotent.
- **Safety** — `flock` serializes it against a live session (skips silently if
  the lock is held); the underlying commands back up, validate with `jq`,
  preserve order, and no-op when there is nothing to remove. It never runs
  `promote`, which writes the git-tracked shared file and stays manual.
- **Log** — `~/.claude/allowlist-clean.log`, rotated at about 512 KB.

## Invariants

- shared is the baseline; local is what this machine adds beyond it.
- One rule should not be in both — `sync-allowlist.sh` removes the local copy.
- A rule the user added locally, and that shared does not contain, is never
  removed.
- A rule removed from shared disappears at the next deploy, unless the machine
  also carried its own copy.

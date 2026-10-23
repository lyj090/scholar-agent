# Global Agent Instructions (Agent Harness)

Loaded into every session, so it stays short: details live in the files linked
at the end and are read on demand.

## Before acting

- Read the nearest project instruction file and `git status` before editing.
- State assumptions; make the smallest change that answers the request; name the
  check that would prove it wrong.
- Define the check, run it once, report what it showed. A failing check is a
  result, not a reason to re-run it, and never fan checks out over one upstream.

## Safety

- Never `git push` without explicit confirmation.
- Never modify `.env` or a credential-bearing file.
- Never install dependencies without explicit approval.

## Commits

On an explicit commit request: `git status` + `git diff` → draft a Conventional
Commits message → show it with the changed files → wait → `git add` selectively
and commit.

## Tools

`read` / `grep` / `glob` for a bounded slice — they truncate and number lines, so
they are the cheap option. Use bash when a step needs several commands at once
or has no native equivalent (`tail`, `ls`, `wc`, `find`); every extra call
re-sends the whole context.

## Skills

`bash ~/scholar-agent/scripts/agent.sh gate <name> --root .` before reading any
workflow spec. Act on the emitted `READ=` / `RUN=` lines and load only the next
file the gate names — never a whole directory.

## Papers

`zotero_cli.py paper-status "<ref>"` before fetching any paper. `LOCAL_READY`
means read the local cache and do not fetch. Only `ABSENT` justifies the web.

## Output

The conclusion goes in chat; anything the reader will revisit goes to a file and
chat gets its path.

## When something is unclear

Ask. A wrong assumption costs more than a question, and this file is deliberately
too short to answer every case — that is what the linked protocols are for.

---

Protocols, read only when the task needs them:

| Topic | File |
|---|---|
| Coding principles, verification discipline | `~/scholar-agent/shared/protocols/karpathy-guidelines-ai.md` |
| Paper reading, local-first corpus | `~/scholar-agent/shared/protocols/literature-preflight.md` |
| Output budget | `~/scholar-agent/shared/protocols/context-budget-protocol.md` |
| Skill routing and invocation | `~/scholar-agent/shared/protocols/skill-routing-ai.md` |
| Machine paths | `~/scholar-agent/shared/protocols/resolve-paths.md` |

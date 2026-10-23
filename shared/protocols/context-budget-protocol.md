# Output Budget — Agent Spec

> Read this when: a task may produce more than a screenful of output.
> Referenced by: `research`, `arxiv-to-library`, `obsidian-zotero-tag-governance`,
> and any skill that generates a report.

---

## One rule

> **Answer in chat. Persist evidence.**
>
> - Chat carries the conclusion: a direct answer plus the path to the evidence.
> - Anything the reader will scroll, scan, or come back to goes to a file.

There is no line or token threshold to count. Counting your own output before
emitting it costs more than it saves, and three different thresholds used to be
in force at once (60 lines here, "under 300 tokens" in `research-ai.md`, "≤15
lines per read" in its flow) — unachievable together, since ~300 tokens is about
15 lines of prose.

## What goes where

| Output | Destination | Chat |
|---|---|---|
| Direct answer, a few sentences | chat | full |
| Structured findings, audit, report | `.ai/reports/<date>_<name>.md` | conclusion + path |
| Research chain | `.ai/research/` via `research.sh` | conclusion + path |
| Design draft / change plan | `.ai/design/` via `design-session.sh` | what changed + path |
| Code | the target file | what it does + path |
| Command output being interpreted | — | the conclusion, not the transcript |

**The test is whether the reader will want it again later**, not how long it is.
A 100-line diff review belongs in chat when the reader asked to see the diff. A
20-line audit table belongs in a file when the reader will act on it over days.

## Reading

Read the slice that answers the question. Truncation and line numbers exist so
that a bounded read *is* the cheap option:

- Open at the section you need, not at line 1.
- `grep` for the symbol before reading around it.
- Continue a truncated read only for a section you are actually using.

Do not read a file twice to confirm the first read. Do not re-read a file you
just wrote to verify it — the write tool reports success, and the test suite is
the authority on correctness.

## Never

- Paste a file's contents into chat "for reference" when a path suffices.
- Echo a command's full output *and* summarize it; give the summary and the path.
- Re-present a full plan after a small revision. Show the delta.
- Pad chat to look thorough. Length is not evidence of work.

## Tool output discipline

Scripts here print `KEY=value` lines (≤6) for exactly this reason: the agent can
act on them without the transcript growing. Follow that convention in a new
script — one line per fact, no banners.

If a script must print a lot, give it `--json` or `--output <path>` and have it
report the path plus a one-line count. `audit-allowlist.sh` and
`arxiv_fetch.py --report` already work this way.

# Agent Gateway (scholar-agent)

Read this first when working in this repository. It is a gateway, not a manual:
every detail below is one gate call away.

## Rules

- Source of truth: `skills/<name>/SKILL.md` + `meta.yaml`. Specs live in
  `shared/specs/`; `*-skills/` is generated — never edit it.
- Before reading a workflow spec: `bash scripts/agent.sh gate <skill> --root .`
  and follow the emitted lines. Script-first skills usually need only `RUN=`.
- `git status` before editing. Preserve unrelated changes; never reset,
  auto-stash, or overwrite dirty work.
- Machine-local paths go in `shared/paths.local.yaml`.

## Commands

```bash
bash scripts/agent.sh gate <skill> --root .   # what to read/run next
bash scripts/agent.sh check                   # lint + portability
bash scripts/agent.sh doctor                  # what is configured, what is not
bash deploy.sh --all                          # generate and link all platforms
```

## When you are unsure

Ask, or run `doctor`. Guessing at this repository's state — which platform is
deployed, whether Zotero is reachable, whether a skill is usable — is how a
plausible-looking change ends up untested.

# Contributing

Thanks for looking. This repository is a real working setup, not a library
with a stable API, so the notes below describe what tends to be easy to merge
and what tends to be declined.

## Easy to accept

- **Bug fixes in `scripts/lib/`** — wrong path resolution, a parser that
  mis-reads a citation, a Zotero field that comes back empty. Include the PDF
  or a minimal fixture if the bug depends on one.
- **A test that fails today.** A failing test with a clear name is worth more
  than a patch without one.
- **Documentation corrections** — a command that does not work as written, a
  version claim that is wrong on your system.
- **New Zotero or MinerU version support** — if a newer release changes an
  endpoint, that is in scope.

## Likely to be declined

- **New skills for one workflow.** Five of the six skills exist because they
  are used daily. A skill that mirrors one person's habits adds surface without
  a user.
- **Reformatting for its own sake.** Diffs that are mostly whitespace make the
  next real change harder to review.
- **Depending on a service behind a personal account.** Anything that needs
  *your* API key or *your* server cannot be tested here.

## Before opening a PR

```bash
python3 -m pytest scripts/tests -q          # expect all passing
bash scripts/deploy/lint-skills.sh          # structure and reference parity
bash scripts/agent.sh check                 # lint plus portability
```

If you changed anything under `skills/`, regenerate the platform trees:

```bash
bash scripts/deploy/generate.sh
```

`claude-skills/`, `codex-skills/`, `cursor-skills/`, `grok-skills/` and
`dsh-skills/` are build output and are **not tracked**. Edit
`skills/<name>/SKILL.md` (plus `meta.yaml`) and regenerate; `deploy.sh` and CI
both run the generator, so a stale tree cannot be committed.

## Language

English for everything an agent reads: `skills/*/SKILL.md`, `shared/protocols/`,
`shared/specs/`, `AGENTS.md`, `global-config/`. These files are prompts, and an
agent copies the language of its instructions into its own output — a Chinese
protocol among English ones produces notes that drift between the two. Chinese is
fine in `README.zh-CN.md`, `docs/devlog/`, and commit-message bodies.

## Commit messages

Conventional Commits, one topic per commit, imperative mood:

```
fix(zotero): read the collection key from the item, not the parent
docs(arxiv): correct the retry count in the pipeline overview
```

## What is deliberately not here

- No CI that publishes anything. Builds run locally.
- No telemetry, no network calls you did not ask for.
- No vendored third-party code.

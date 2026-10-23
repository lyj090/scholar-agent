<div align="center">

# Scholar Agent

**Local-first literature workflows for AI coding agents.**

<p>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-2563eb?style=flat-square"></a>
  <a href="../../actions/workflows/ci.yml"><img alt="CI" src="https://github.com/lyj090/scholar-agent/actions/workflows/ci.yml/badge.svg"></a>
  <a href="../../releases"><img alt="Version" src="https://img.shields.io/badge/version-0.1.0-2563eb?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2563eb?style=flat-square">
  <img alt="Zotero" src="https://img.shields.io/badge/zotero-10%2B-2563eb?style=flat-square">
  <img alt="Agents" src="https://img.shields.io/badge/agents-5-17171a?style=flat-square">
</p>

Find the paper, read it properly, keep a note you can defend, and publish only
the part that can be public — from one source, deployed to five agents at once.

[**English**](README.md) · [简体中文](README.zh-CN.md)

</div>

---

## Why this exists

An agent will summarise a paper for you. The problems are around the summary,
and none of them look like errors.

**It fetches what you already have.** The agent holds an arXiv ID; the library is
keyed by item key. Nothing connects the two, so it goes to the web — even for a
paper already parsed on disk.

**It reads the whole paper.** Answering a question about one section means
loading every section, then paying for them again on every later turn.

**You cannot check the result.** Prose reads the same whether it came from the
paper or from the model's memory. A wrong figure and a right one are equally
confident.

**Private notes go public.** A heading that sounds private is not a guarantee.
The mistake is visible only after publication.

## What it does about it

**Ask locally before going remote.** Every reference is classified into one of
four states, and only "not in the library" allows a fetch. A local hit cannot
hallucinate.

**Read the outline, then one section.** Every paper is served section by section,
with sizes known in advance, so the parts nobody asked about are never loaded.

**Make every claim traceable.** A note may contain only what the source says,
with a locator on each claim. Anything the model adds is marked as its own act.

**Split public from private mechanically.** One explicit marker, cut by the
exporter. No heading heuristics, no reviewer to forget.

**Write once, deploy everywhere.** One source generates the skill trees for
Codex, Cursor, Claude Code, Grok and Agent Harness, so they cannot drift.

**Say what is missing instead of failing obscurely.** `doctor` reports every
capability in one place, so a broken install is a checklist rather than a
traceback.

## Skills

Six skills. Five handle literature; one does code research.

| Skill | What it does |
|---|---|
| `paper-lookup` | Check whether a paper is already local before fetching it |
| `arxiv-to-library` | Find arXiv papers, import to Zotero, drop duplicates |
| `zotero-inbox-to-library` | Sort and file a backlog of unsorted papers |
| `zotero-paper-to-obsidian` | Write a reading note that stays faithful to the source |
| `obsidian-zotero-tag-governance` | Keep tags consistent across Obsidian and Zotero |
| `research` | Structured code research, with notes kept on disk |

## Quick start

```bash
git clone git@github.com:<you>/scholar-agent.git
cd scholar-agent
./deploy.sh --all
```

Not sure what is set up yet? Ask:

```bash
bash scripts/agent.sh doctor
```

It reports each capability as ready, partial or off, and says what is missing.
It writes nothing. The literature flow being "off" is a valid install — only
those skills need a library.

That generates every platform tree and links it into place. Editing `skills/`
once updates all of them.

The literature skills need Zotero 10+ and a configured vault; everything else
works without either. Paths are discovered automatically — only pin the vault
when more than one candidate scores equally:

```bash
python3 scripts/deploy/setup_paths.py --discover   # see the candidates
python3 scripts/deploy/setup_paths.py --apply      # write the choice
```

## Requirements

Python 3.10+ and PyYAML. Zotero 10 or newer for the literature skills — 10 is
the floor because earlier versions expose no writable local API. MinerU is
optional: without it you lose figure, table and equation extraction, not the
ability to read and note a paper.

This is a set of scripts, not a package. Clone it; do not `pip install` it.

Windows works for the Python code and the CLI. The deployment scripts are POSIX
shell, so the symlink-based `deploy.sh` step is macOS and Linux only.

## Documentation

- **[How the literature pipeline works](docs/literature/architecture.md)** —
  Zotero, MinerU and the CLI, plus the constraints that were measured rather
  than assumed.
- **[Publishing a public subset](docs/literature/publication-workflow.md)** — how
  a note gets from a local vault to a site without leaking what should not ship.
- **[Runtime rules for agents](AGENTS.md)** — the gateway an agent reads first;
  per-skill specifications are in [`shared/specs/`](shared/specs/).
- **[Cursor skills](docs/setup/cursor-skills.md)** and
  **[Python environment](docs/setup/venv-setup.md)** — setup details.
- **[BRANCHES.md](BRANCHES.md)** — how the private and public branches relate.

Which skill handles what is in the table above; `bash scripts/agent.sh list`
prints the authoritative list for this checkout.

## Contributing

Issues and pull requests are welcome. This is also the maintainer's daily
setup, so changes serving one machine's quirks are unlikely to be merged.
[CONTRIBUTING.md](CONTRIBUTING.md) describes what is easy to accept.

## License

MIT — see [LICENSE](LICENSE).

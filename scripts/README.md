# scripts/ — CLI layout

Aligned with [`skills/`](../skills/) (source) and [`shared/config/skill-groups.yaml`](../shared/config/skill-groups.yaml) (deploy groups).

| Directory | Role | skill-groups |
|-----------|------|--------------|
| [`../deploy.sh`](../deploy.sh) | **Deploy entry** — symlink skills to Codex/Cursor/Claude/Grok/DSH | all |
| [`agent.sh`](agent.sh) | **Agent entry** — gate, audit, check, list, and deploy wrappers | all |
| [`deploy/`](deploy/) | Generate, lint, bootstrap, paths setup | — |
| [`skills/`](skills/) | Runtime gates & `.ai/` tooling | `core` |
| [`literature/`](literature/) | `zotero_cli.py` (read/write/MinerU/notes), `arxiv_fetch.py`, `mineru_parse.py` (arbitrary-URL parse), `validate_tags.py` | `literature` |
| [`allowlist/`](allowlist/) | Claude permission allowlist sync/audit | — |
| [`lib/`](lib/) | Shared Python/bash libraries (no CLI); `bootstrap_skills.sh` = shared deploy loop | — |
| [`tests/`](tests/) | Smoke tests for gates | — |

Standalone utilities (no skill wiring):

- `python3 scripts/agent-audit.py --root ~/scholar-agent` — audit AGENT*.md
  files for drift, broken links, duplicates
- `python3 scripts/literature/mineru_parse.py <URL>` — parse a **remote** PDF/DOCX
  via MinerU's Agent API to a single `.md` (no Zotero involvement). For a PDF
  already in Zotero use `zotero_cli.py mineru-parse <REF>`, which writes a
  reusable cache instead
- `python3 scripts/literature/zotero_launcher.py --json` — report/await Zotero
  running + local API ready (used before batch writes)
- `bash scripts/deploy/schedule.sh list|enable|disable` — manage cron entries
  for mineru-sync, tag-governance (notes audit) and allowlist-clean

## Common commands

```bash
bash scripts/agent.sh list                    # show groups from shared config
bash scripts/agent.sh audit --root "$HOME"    # audit local AGENT*.md files
bash scripts/agent.sh check                   # compact lint + portability result
bash scripts/agent.sh gate <name> --root .     # resolve a skill's next action
bash scripts/agent.sh undeploy --dry-run       # preview managed cleanup
bash deploy.sh                          # deploy skills + global config
bash scripts/deploy/generate.sh         # regenerate *-skills/ from skills/
bash scripts/deploy/lint-skills.sh      # structure + reference parity
bash scripts/skills/skill-gate.sh <name> --root .
python3 scripts/literature/zotero_cli.py --help
```

`bash deploy.sh --undeploy --dry-run` previews removal of only this repository's
managed symlinks, managed config blocks, and exact generated files. Use
`--undeploy --yes` to apply it; modified or unknown targets are preserved.



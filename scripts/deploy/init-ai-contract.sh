#!/usr/bin/env bash
# init-ai-contract.sh — Idempotently apply the .ai/ + Context Budget contract to a repo.
#
# Use when onboarding a new repo to the scholar-agent research protocol.
# Two effects (both idempotent — safe to re-run):
#   1. Ensure <repo>/.gitignore ignores `.ai/reports/` (scratch script output);
#      research/ and design/ stay tracked (they carry lasting value).
#      Migrates a legacy whole-`.ai/` ignore line to `.ai/reports/`.
#   2. Append a "Context Budget pointer" footer to <repo>/CLAUDE.md if missing
#
# Usage:
#   bash init-ai-contract.sh <repo_dir>
#   bash init-ai-contract.sh ~/src/dotfiles
#
# Exit codes: 0 OK / 1 bad arg / 2 missing CLAUDE.md (warning only, gitignore still patched)

set -euo pipefail

repo_dir="${1:-}"
if [[ -z "$repo_dir" || ! -d "$repo_dir" ]]; then
  echo "ERR: usage: init-ai-contract.sh <repo_dir>" >&2
  exit 1
fi
repo_dir="$(cd "$repo_dir" && pwd)"

gitignore="$repo_dir/.gitignore"
claudemd="$repo_dir/CLAUDE.md"
marker="<!-- ai-contract: managed by init-ai-contract.sh -->"

# 1. .gitignore .ai/reports/ (research/ + design/ stay tracked)
touch "$gitignore"
if grep -qE '^\.ai/reports/?$' "$gitignore"; then
  echo "gitignore: already has .ai/reports/  ($gitignore)"
elif grep -qE '^\.ai/?$' "$gitignore"; then
  # Migrate legacy whole-.ai ignore → reports-only.
  sed -i -E 's|^\.ai/?$|.ai/reports/|' "$gitignore"
  echo "gitignore: migrated .ai/ → .ai/reports/  ($gitignore)"
else
  printf '\n# Local AI artifacts — research/ + design/ tracked; reports/ is scratch (see ~/.claude/CLAUDE.md)\n.ai/reports/\n' >> "$gitignore"
  echo "gitignore: added .ai/reports/        ($gitignore)"
fi

# 2. CLAUDE.md pointer footer
if [[ ! -f "$claudemd" ]]; then
  echo "WARN: CLAUDE.md not found at $claudemd — skipping pointer insertion" >&2
  exit 2
fi

if grep -qF "$marker" "$claudemd"; then
  echo "CLAUDE.md: pointer already present ($claudemd)"
  exit 0
fi

cat >> "$claudemd" <<EOF

---

$marker
## Context Budget & .ai/ Contract

This repo follows the global \`.ai/\` contract — long-form trace / design / audit
artifacts go to \`.ai/\`. \`research/\` and \`design/\` are tracked in git (lasting
value); \`reports/\` is gitignored scratch output. Chat keeps a 5-line summary + path.

- Persist research findings: \`bash ~/scholar-agent/scripts/skills/research.sh query "<keyword>" --root .\`
- Full protocol: \`~/.claude/CLAUDE.md\` §"Context Budget" + \`~/scholar-agent/shared/protocols/context-budget-protocol.md\`
EOF
echo "CLAUDE.md: pointer appended ($claudemd)"

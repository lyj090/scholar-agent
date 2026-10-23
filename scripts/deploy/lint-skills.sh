#!/usr/bin/env bash
set -euo pipefail
# lint-skills.sh — Validate skill structure, references, and parity.
# Usage: bash scripts/deploy/lint-skills.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
ERRORS=0
WARNS=0

err() { echo "ERROR: $1"; ((ERRORS++)) || true; }
warn() { echo "WARN:  $1"; ((WARNS++)) || true; }
ok() { echo "  ok: $1"; }

check_structure() {
  local platform="$1"
  local filename="$2"
  echo "=== $platform Structure Check ==="
  for d in "$REPO_ROOT/$platform"/*/; do
    [[ -d "$d" ]] || continue
    local name="$(basename "$d")"
    # Skip hidden dirs or special ones
    [[ "$name" == .* ]] && continue
    [[ "$name" == "agents" ]] && continue
    
    # Platform specific logic
    local target="$d/$filename"
    [[ "$filename" == "{name}.md" ]] && target="$d/$name.md"

    if [[ -f "$target" ]]; then
      ok "$platform/$name verified"
      # Platforms whose loader ignores a file without YAML frontmatter must be
      # proven to carry it — a bare copy of the source body would silently
      # vanish from discovery instead of failing loudly.
      case "$platform" in
        codex-skills|dsh-skills)
          if ! head -n 1 "$target" | grep -q '^---$'; then
            err "$platform/$name/$(basename "$target") missing YAML frontmatter (regenerate via scripts/deploy/generate.sh)"
          fi
          ;;
      esac
    else
      err "$platform/$name missing $(basename "$target")"
    fi
  done
  echo ""
}

# 1. Structure Checks
check_structure "claude-skills" "{name}.md"
check_structure "cursor-skills" "SKILL.md"
check_structure "codex-skills" "SKILL.md"
check_structure "grok-skills" "SKILL.md"
check_structure "dsh-skills" "SKILL.md"

echo "=== Cross-Reference Check ==="
# 2. shared/ file references must exist
# Search in all platform skills and shared/ itself
while IFS= read -r ref; do
  if [[ -f "$REPO_ROOT/$ref" ]]; then
    ok "$ref exists"
  else
    err "referenced $ref does not exist"
  fi
done < <(grep -roh 'shared/[a-zA-Z0-9_/-]*\.\(md\|yaml\)' \
  "$REPO_ROOT/claude-skills/" \
  "$REPO_ROOT/cursor-skills/" \
  "$REPO_ROOT/codex-skills/" \
  "$REPO_ROOT/grok-skills/" \
  "$REPO_ROOT/dsh-skills/" \
  "$REPO_ROOT/shared/" 2>/dev/null | sort -u)

echo ""
echo "=== Orphan Platform Check ==="
# A platform removed from deploy.sh must not leave its generated tree behind.
# An orphaned `*-skills/` directory is not inert: generate.sh keeps rewriting it,
# it keeps stale platform names in the docs, and if a machine still has the old
# tool installed the bootstrap keeps linking rules the runtime no longer reads.
# The live set is exactly what generate.sh emits and deploy.sh can symlink.
LIVE_PLATFORMS=(claude cursor codex grok dsh)
for _plat_dir in "$REPO_ROOT"/*-skills; do
  [[ -d "$_plat_dir" ]] || continue
  _plat="$(basename "$_plat_dir" | sed 's/-skills$//')"
  if [[ " ${LIVE_PLATFORMS[*]} " != *" $_plat "* ]]; then
    err "$(basename "$_plat_dir")/ is not a live platform (removed from deploy.sh?)"
  fi
done
grep -q 'gemini' "$REPO_ROOT/deploy.sh" \
  && err "deploy.sh still references the retired gemini platform"
ok "platform set matches generate.sh output (${LIVE_PLATFORMS[*]})"

echo ""
echo "=== Parity Check ==="

# 3. Skill count parity
CLAUDE_COUNT=$(find "$REPO_ROOT/claude-skills" -maxdepth 1 -mindepth 1 -type d | wc -l)
CURSOR_COUNT=$(find "$REPO_ROOT/cursor-skills" -maxdepth 1 -mindepth 1 -type d | wc -l)
CODEX_COUNT=$(find "$REPO_ROOT/codex-skills" -maxdepth 1 -mindepth 1 -type d | wc -l)
GROK_COUNT=$(find "$REPO_ROOT/grok-skills" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l)
DSH_COUNT=$(find "$REPO_ROOT/dsh-skills" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l)

echo "  claude=$CLAUDE_COUNT cursor=$CURSOR_COUNT codex=$CODEX_COUNT grok=$GROK_COUNT dsh=$DSH_COUNT"

if [[ "$CLAUDE_COUNT" != "$CURSOR_COUNT" || "$CLAUDE_COUNT" != "$CODEX_COUNT" \
   || "$CLAUDE_COUNT" != "$GROK_COUNT" \
   || "$CLAUDE_COUNT" != "$DSH_COUNT" ]]; then
  warn "skill count mismatch across platforms"
fi

# Use claude-skills as the lead platform for name-based parity
for d in "$REPO_ROOT/claude-skills"/*/; do
  [[ -d "$d" ]] || continue
  name="$(basename "$d")"
  [[ -d "$REPO_ROOT/cursor-skills/$name" ]] || warn "cursor-skills/$name/ missing (exists in claude)"
  [[ -d "$REPO_ROOT/codex-skills/$name" ]] || warn "codex-skills/$name/ missing (exists in claude)"
  [[ -d "$REPO_ROOT/grok-skills/$name" ]] || warn "grok-skills/$name/ missing (exists in claude)"
  [[ -d "$REPO_ROOT/dsh-skills/$name" ]] || warn "dsh-skills/$name/ missing (exists in claude)"
done

echo ""
echo "=== Generator Check ==="

# 4. Source synchronization
if [[ -d "$REPO_ROOT/skills" ]]; then
  for d in "$REPO_ROOT"/skills/*/; do
    [[ -d "$d" ]] || continue
    name="$(basename "$d")"
    [[ -f "$d/SKILL.md" ]] || { warn "skills/$name/ missing SKILL.md source"; continue; }
    [[ -f "$REPO_ROOT/claude-skills/$name/$name.md" ]] || err "skills/$name not generated → claude-skills/"
    [[ -f "$REPO_ROOT/cursor-skills/$name/SKILL.md" ]] || err "skills/$name not generated → cursor-skills/"
    [[ -f "$REPO_ROOT/codex-skills/$name/SKILL.md" ]] || err "skills/$name not generated → codex-skills/"
    [[ -f "$REPO_ROOT/grok-skills/$name/SKILL.md" ]] || err "skills/$name not generated → grok-skills/"
    [[ -f "$REPO_ROOT/dsh-skills/$name/SKILL.md" ]] || err "skills/$name not generated → dsh-skills/"
  done
  ok "generator outputs verified"
fi

echo ""
echo "=== Gate Tests ==="
# Keep the sub-test's output on failure. A bare "failed" line cannot be diagnosed
# from a CI log, and this suite fails for environment reasons as well as real
# ones — the environment is exactly what a bare line hides.
gate_out="$(bash "$SCRIPTS_DIR/tests/test-skill-gate.sh" 2>&1)" && gate_rc=0 || gate_rc=$?
if [[ $gate_rc -eq 0 ]]; then
  ok "test-skill-gate.sh"
else
  err "test-skill-gate.sh failed"
  printf '%s\n' "$gate_out" | grep -E 'FAIL|not found|No such|error' | head -10 | sed 's/^/      /'
fi
if python3 "$SCRIPTS_DIR/tests/test-skill-consistency.py" >/dev/null 2>&1; then
  ok "test-skill-consistency.py"
else
  err "test-skill-consistency.py failed"
fi
if python3 "$SCRIPTS_DIR/tests/test_retrieval.py" >/dev/null 2>&1; then
  ok "test_retrieval.py"
else
  err "test_retrieval.py failed"
fi
if python3 "$SCRIPTS_DIR/tests/test_mineru_catalog.py" >/dev/null 2>&1; then
  ok "test_mineru_catalog.py"
else
  err "test_mineru_catalog.py failed"
fi
if [[ -x "$SCRIPTS_DIR/skills/skill-gate.sh" ]]; then
  ok "skill-gate.sh executable"
else
  err "skill-gate.sh missing or not executable"
fi

echo ""
echo "=== Global Config Check ==="

for f in claude-global.md dsh-global.md; do
  if [[ -f "$REPO_ROOT/global-config/$f" ]]; then
    ok "global-config/$f"
  else
    warn "global-config/$f missing"
  fi
done
if [[ -f "$REPO_ROOT/global-config/cursor-rules/karpathy-guidelines.mdc" ]]; then
  ok "global-config/cursor-rules/karpathy-guidelines.mdc"
else
  err "global-config/cursor-rules/karpathy-guidelines.mdc missing"
fi

echo ""
echo "=== Agent Docs Check ==="
AGENT_DOC_REPORT="$REPO_ROOT/.ai/reports/agent-docs-lint.md"
if python3 "$REPO_ROOT/scripts/agent-audit.py" \
  --root "$REPO_ROOT" --check --report "$AGENT_DOC_REPORT" >/dev/null; then
  ok "instruction files: links, absolute paths, headings, dead names (repo)"
else
  err "repo instruction file has a broken link, local absolute path, duplicated heading or dead name (see $AGENT_DOC_REPORT)"
fi
# Repo-local scan above cannot see damage in ~/.claude* or sibling
# projects, which is exactly where drift hides. Report-only: those trees are
# not ours to fail the build over.
HOME_AGENT_DOC_REPORT="$REPO_ROOT/.ai/reports/agent-docs-lint-home.md"
if python3 "$REPO_ROOT/scripts/agent-audit.py" \
  --root "$HOME" --report "$HOME_AGENT_DOC_REPORT" >/dev/null; then
  if grep -qE '^- (Missing relative links|Local absolute references|Duplicated headings within one file|Dead instruction names \(never auto-loaded\)): [1-9]' \
    "$HOME_AGENT_DOC_REPORT"; then
    warn "agent instruction findings outside the repo (see $HOME_AGENT_DOC_REPORT)"
  else
    ok "instruction files: links, absolute paths, headings, dead names (whole \$HOME)"
  fi
else
  warn "agent audit over \$HOME did not complete"
fi
if python3 "$REPO_ROOT/scripts/tests/test_agent_audit.py" >/dev/null 2>&1; then
  ok "test_agent_audit.py"
else
  err "test_agent_audit.py failed"
fi
if bash "$REPO_ROOT/scripts/tests/test-deploy.sh" >/dev/null 2>&1; then
  ok "test-deploy.sh"
else
  err "test-deploy.sh failed"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ $ERRORS -eq 0 && $WARNS -eq 0 ]]; then
  echo "✅ All checks passed"
elif [[ $ERRORS -eq 0 ]]; then
  echo "⚠️  Passed with $WARNS warning(s)"
else
  echo "❌ $ERRORS error(s), $WARNS warning(s)"
  exit 1
fi

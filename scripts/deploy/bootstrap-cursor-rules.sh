#!/usr/bin/env bash
# bootstrap-cursor-rules.sh — Deploy Cursor rules (.mdc) to ~/.cursor/rules
set -euo pipefail
# ─────────────────────────────────────────────────────────────────────────────
# bootstrap-cursor-rules.sh — Generate and deploy Cursor global rules (.mdc)
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
CURSOR_RULES_DST="${CURSOR_RULES_DST:-$HOME/.cursor/rules}"
CURSOR_RULES_SRC="$REPO_ROOT/global-config/cursor-rules"
DRY_RUN="${DEPLOY_DRY_RUN:-0}"
UNDEPLOY="${DEPLOY_UNDEPLOY:-0}"

# ─── Colors ──────────────────────────────────────────────────────────────────
C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_GREEN=$'\033[32m'
C_CYAN=$'\033[36m'; C_YELLOW=$'\033[33m'; C_DIM=$'\033[2m'

info()  { printf "  ${C_DIM}[INFO]${C_RESET} %s\n" "$*"; }
ok()    { printf "  ${C_BOLD}${C_GREEN}✔${C_RESET} %s\n" "$*"; }
warn()  { printf "  ${C_BOLD}${C_YELLOW}⚠${C_RESET} %s\n" "$*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --undeploy|--unploy) UNDEPLOY=1; shift ;;
    --target) CURSOR_RULES_DST="$2/.cursor/rules"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ "$DRY_RUN" != "1" ]]; then
  mkdir -p "$CURSOR_RULES_DST"
fi

# ─── Generate MDC from markdown source ──────────────────────────────────────
# Reads a markdown file and wraps it in MDC frontmatter.
# Usage: generate_mdc <source_md> <output_mdc> <alwaysApply> [description]
generate_mdc() {
  local src="$1"
  local dst="$2"
  local always_apply="${3:-true}"
  local description="${4:-Global instruction rule}"

  if [[ ! -f "$src" ]]; then
    warn "Source not found: $src"
    return 1
  fi

  local dst_path="$CURSOR_RULES_DST/$dst"

  if [[ "$DRY_RUN" == "1" ]]; then
    info "Would generate: $dst -> $dst_path"
    return 0
  fi

  {
    echo "---"
    echo "description: $description"
    echo "alwaysApply: $always_apply"
    echo "---"
    echo ""
    cat "$src"
  } > "$dst_path"

  ok "Generated: $dst → $dst_path"
}

# ─── Deploy pre-built MDC files ─────────────────────────────────────────────
# For MDC files that are manually maintained (like karpathy-guidelines.mdc)
deploy_premade_mdc() {
  local src="$1"
  local dst="${2:-$(basename "$src")}"

  if [[ ! -f "$src" ]]; then
    warn "Pre-made MDC not found: $src"
    return 1
  fi

  local dst_path="$CURSOR_RULES_DST/$dst"
  if [[ "$DRY_RUN" == "1" ]]; then
    info "Would deploy: $dst -> $dst_path"
    return 0
  fi
  cp "$src" "$dst_path"
  ok "Deployed pre-made: $dst → $dst_path"
}

remove_if_matches() {
  local src="$1" dst="$2"
  [[ -e "$dst" || -L "$dst" ]] || return 0

  if [[ -L "$dst" ]]; then
    local target
    target="$(readlink -f "$dst" 2>/dev/null || readlink "$dst")"
    [[ "$target" == "$src" ]] || { warn "Preserved non-managed link: $dst"; return 0; }
  elif ! cmp -s "$src" "$dst"; then
    warn "Preserved modified file: $dst"
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    info "Would remove: $dst"
  else
    rm -f "$dst"
    ok "Removed: $dst"
  fi
}

remove_generated_mdc() {
  local src="$1" dst="$2" always_apply="$3" description="$4"
  local expected
  expected="$(mktemp)"
  {
    echo "---"
    echo "description: $description"
    echo "alwaysApply: $always_apply"
    echo "---"
    echo ""
    cat "$src"
  } > "$expected"
  remove_if_matches "$expected" "$dst"
  rm -f "$expected"
}

undeploy_rules() {
  remove_generated_mdc \
    "$REPO_ROOT/global-config/claude-global.md" \
    "$CURSOR_RULES_DST/global-instructions.mdc" \
    "true" \
    "Global instructions: coding principles, git conventions, context budget"
  remove_generated_mdc \
    "$REPO_ROOT/global-config/shell-env.md" \
    "$CURSOR_RULES_DST/shell-environment.mdc" \
    "true" \
    "Shell environment: zsh defaults and terminal conventions"
  remove_if_matches \
    "$REPO_ROOT/global-config/cursor-rules/karpathy-guidelines.mdc" \
    "$CURSOR_RULES_DST/karpathy-guidelines.mdc"
}

# ─── Main ───────────────────────────────────────────────────────────────────

header() {
  printf "\n${C_BOLD}${C_CYAN}%s${C_RESET}\n" "$*"
}

if [[ "$UNDEPLOY" == "1" ]]; then
  header "🧹 Removing Cursor Global Rules"
  undeploy_rules
  info "Rules target: $CURSOR_RULES_DST"
  if [[ "$DRY_RUN" == "1" ]]; then
    info "Dry-run complete; no files changed"
  fi
  exit 0
fi

header "🔧 Generating Cursor Global Rules"

# 1. Generate global-instructions.mdc from claude-global.md
#    This makes the global instructions available to Cursor
generate_mdc \
  "$REPO_ROOT/global-config/claude-global.md" \
  "global-instructions.mdc" \
  "true" \
  "Global instructions: coding principles, git conventions, context budget"

# 1b. Shell environment (zsh on this machine)
generate_mdc \
  "$REPO_ROOT/global-config/shell-env.md" \
  "shell-environment.mdc" \
  "true" \
  "Shell environment: zsh defaults and terminal conventions"

# 2. Deploy pre-made MDC files (if they exist)
if [[ -d "$CURSOR_RULES_SRC" ]]; then
  for mdc in "$CURSOR_RULES_SRC"/*.mdc; do
    [[ -f "$mdc" ]] || continue
    deploy_premade_mdc "$mdc"
  done
fi

# 3. Special handling: ifCursor is being used with a specific project,
#    also generate project-level rules from CLAUDE.md
if [[ -n "${CURSOR_PROJECT_ROOT:-}" && -f "$CURSOR_PROJECT_ROOT/CLAUDE.md" ]]; then
  generate_mdc \
    "$CURSOR_PROJECT_ROOT/CLAUDE.md" \
    "project-instructions.mdc" \
    "false" \
    "Project-specific instructions (from CLAUDE.md)"
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "Project-level rule would be generated (alwaysApply: false)"
  else
    warn "Project-level rule generated (alwaysApply: false) — enable via Cursor settings"
  fi
fi

header "✅ Cursor Rules Deployment Complete"

# ─── Summary ───────────────────────────────────────────────────────────────
info "Rules target: $CURSOR_RULES_DST"
if [[ "$DRY_RUN" == "1" ]]; then
  info "Dry-run complete; no files changed"
elif [[ -d "$CURSOR_RULES_DST" ]]; then
  info "Files:"
  ls -1 "$CURSOR_RULES_DST/"*.mdc 2>/dev/null | while read -r f; do
    info "  - $(basename "$f")"
  done
fi

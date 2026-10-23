#!/usr/bin/env bash
# bootstrap-claude-skills.sh — Deploy generated claude-skills to ~/.claude/commands
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
source "$SCRIPTS_DIR/lib/ui.sh"
source "$SCRIPTS_DIR/lib/skill_groups.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skill_filter.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skills.sh"

export CODEX_CONFIG_HOME="${CODEX_CONFIG_HOME:-$REPO_ROOT}"
SRC_DIR="$CODEX_CONFIG_HOME/claude-skills"

MODE="public"
CLAUDE_COMMANDS_DIR=""
DEPLOY_GROUPS=()
UNDEPLOY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --internal) MODE="internal"; shift ;;
    --public)   MODE="public"; shift ;;
    --target)   CLAUDE_COMMANDS_DIR="$2/.claude/commands"; shift 2 ;;
    --group)    DEPLOY_GROUPS+=("$2"); shift 2 ;;
    --undeploy|--unploy) UNDEPLOY=true; shift ;;
    *) shift ;;
  esac
done

bootstrap_resolve_deploy_groups "default"

if [[ -z "$CLAUDE_COMMANDS_DIR" ]]; then
  if [[ -n "${CLAUDE_COMMANDS_HOME:-}" ]]; then
    CLAUDE_COMMANDS_DIR="$CLAUDE_COMMANDS_HOME"
  elif [[ "$MODE" == "public" ]]; then
    CLAUDE_COMMANDS_DIR="$HOME/.claude/commands"
  else
    CLAUDE_COMMANDS_DIR="$HOME/.claude/commands"
  fi
fi

if [[ "$UNDEPLOY" == true ]]; then
  bootstrap_undeploy_skills "Claude" "$SRC_DIR" "$CLAUDE_COMMANDS_DIR" "file"
  exit 0
fi

if ui_is_full; then
  printf "  ${C_DIM}mode:     ${C_BOLD}%s${C_RESET}\n" "$MODE"
  printf "  ${C_DIM}groups:   ${C_BOLD}%s${C_RESET}\n" "${DEPLOY_GROUPS[*]}"
  printf "  ${C_DIM}target:   ${C_BOLD}%s${C_RESET}\n\n" "$CLAUDE_COMMANDS_DIR"
fi

bootstrap_deploy_skills "Claude" "$SRC_DIR" "$CLAUDE_COMMANDS_DIR" "file" "${DEPLOY_GROUPS[@]}"

info "Claude commands synced to ${C_BOLD}$CLAUDE_COMMANDS_DIR${C_RESET}"
if [[ ${#SKILL_DIRS[@]} -gt 0 ]]; then
  info "Hint: 按分组部署 skills，可用 --group <name>（core/literature）"
  if ui_is_full; then printf "  ${C_DIM}bash deploy.sh --claude --group core${C_RESET}\n"; fi
fi

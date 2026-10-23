#!/usr/bin/env bash
# bootstrap-cursor-skills.sh — Deploy generated cursor-skills to ~/.cursor/skills-cursor
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
source "$SCRIPTS_DIR/lib/ui.sh"
source "$SCRIPTS_DIR/lib/skill_groups.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skill_filter.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skills.sh"

export CODEX_CONFIG_HOME="${CODEX_CONFIG_HOME:-$REPO_ROOT}"
CURSOR_SKILLS_HOME="${CURSOR_SKILLS_HOME:-$HOME/.cursor/skills-cursor}"
SRC_DIR="$CODEX_CONFIG_HOME/cursor-skills"
DST_DIR="$CURSOR_SKILLS_HOME"
DEPLOY_GROUPS=()
UNDEPLOY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) DST_DIR="$2"; shift 2 ;;
    --group)  DEPLOY_GROUPS+=("$2"); shift 2 ;;
    --undeploy|--unploy) UNDEPLOY=true; shift ;;
    *) shift ;;
  esac
done

bootstrap_resolve_deploy_groups "cursor"
if [[ "$UNDEPLOY" == true ]]; then
  bootstrap_undeploy_skills "Cursor" "$SRC_DIR" "$DST_DIR" "dir"
  exit 0
fi
bootstrap_deploy_skills "Cursor" "$SRC_DIR" "$DST_DIR" "dir" "${DEPLOY_GROUPS[@]}"

info "Cursor skills synced to ${C_BOLD}$DST_DIR${C_RESET}"
info "Groups: ${C_BOLD}${DEPLOY_GROUPS[*]}${C_RESET} (literature: bash deploy.sh --cursor --group literature)"

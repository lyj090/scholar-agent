#!/usr/bin/env bash
# bootstrap-codex-skills.sh — Deploy generated codex-skills to ~/.codex/skills
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
source "$SCRIPTS_DIR/lib/ui.sh"
source "$SCRIPTS_DIR/lib/skill_groups.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skill_filter.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skills.sh"

export CODEX_CONFIG_HOME="${CODEX_CONFIG_HOME:-$REPO_ROOT}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SRC_DIR="$CODEX_CONFIG_HOME/codex-skills"
DST_DIR="$CODEX_HOME/skills"
DEPLOY_GROUPS=()
UNDEPLOY=false

if [[ ! -d "$SRC_DIR" && -d "$CODEX_CONFIG_HOME/skills" ]]; then
  SRC_DIR="$CODEX_CONFIG_HOME/skills"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) DST_DIR="$2/skills"; shift 2 ;;
    --group)  DEPLOY_GROUPS+=("$2"); shift 2 ;;
    --undeploy|--unploy) UNDEPLOY=true; shift ;;
    *) shift ;;
  esac
done

bootstrap_resolve_deploy_groups "default"
if [[ "$UNDEPLOY" == true ]]; then
  bootstrap_undeploy_skills "Codex" "$SRC_DIR" "$DST_DIR" "dir"
  exit 0
fi
bootstrap_deploy_skills "Codex" "$SRC_DIR" "$DST_DIR" "dir" "${DEPLOY_GROUPS[@]}"

info "Codex skills synced to ${C_BOLD}$DST_DIR${C_RESET}"

#!/usr/bin/env bash
# bootstrap-grok-skills.sh — Deploy generated grok-skills to ~/.grok/skills
set -euo pipefail

# bootstrap-grok-skills.sh — symlink grok-skills/ → ~/.grok/skills/
#
# Grok discovers skills from ~/.grok/skills/<name>/SKILL.md (user scope).
# Bundled skills (help, create-skill, docx, …) live as real dirs in the same
# place; prune only removes our managed symlinks (see bootstrap_skills.sh).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
source "$SCRIPTS_DIR/lib/ui.sh"
source "$SCRIPTS_DIR/lib/skill_groups.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skill_filter.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skills.sh"

SRC_DIR="$REPO_ROOT/grok-skills"
DST_DIR="${GROK_SKILLS_HOME:-$HOME/.grok/skills}"
DEPLOY_GROUPS=()
UNDEPLOY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --global) DST_DIR="${GROK_SKILLS_HOME:-$HOME/.grok/skills}"; shift ;;
    --target) DST_DIR="$2/.grok/skills"; shift 2 ;;
    --group)  DEPLOY_GROUPS+=("$2"); shift 2 ;;
    --undeploy|--unploy) UNDEPLOY=true; shift ;;
    *) shift ;;
  esac
done

if [[ ! -d "$SRC_DIR" && "$UNDEPLOY" != true ]]; then
  err "grok-skills/ directory not found. Run scripts/deploy/generate.sh first."
  exit 1
fi

if [[ "$DST_DIR" == "$HOME/.grok/skills" || "$DST_DIR" == "${GROK_SKILLS_HOME:-}" ]]; then
  info "Deploying to global directory: ${C_BOLD}$DST_DIR${C_RESET}"
else
  info "Deploying to directory: ${C_BOLD}$DST_DIR${C_RESET}"
fi

bootstrap_resolve_deploy_groups "default"
if [[ "$UNDEPLOY" == true ]]; then
  bootstrap_undeploy_skills "Grok" "$SRC_DIR" "$DST_DIR" "dir"
  exit 0
fi
bootstrap_deploy_skills "Grok" "$SRC_DIR" "$DST_DIR" "dir" "${DEPLOY_GROUPS[@]}"

info "Grok skills synced to ${C_BOLD}$DST_DIR${C_RESET}"
info "Verify with: ${C_BOLD}grok inspect${C_RESET} (Skills section should list deployed names)."

#!/usr/bin/env bash
# bootstrap-dsh-skills.sh — symlink dsh-skills/ → $DSH_HOME/skills/
#
# DeepSeek Harness (dsh) discovers skills from <dshHome>/skills/<name>/SKILL.md
# at user scope, plus <cwd>/.dsh/skills and <cwd>/.agents/skills at project
# scope. dshHome is $DSH_HOME or ~/.dsh. This bootstrap owns the USER root only,
# matching the other platforms (all deploy to $HOME); project scope is left to
# the repository you are working in.
#
# A SKILL.md without YAML frontmatter is logged and ignored by dsh, so
# dsh-skills/ must be generated (scripts/deploy/generate.sh), never copied.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
source "$SCRIPTS_DIR/lib/ui.sh"
source "$SCRIPTS_DIR/lib/skill_groups.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skill_filter.sh"
source "$SCRIPTS_DIR/lib/bootstrap_skills.sh"

SRC_DIR="$REPO_ROOT/dsh-skills"
# DSH_SKILLS_HOME wins when set explicitly; otherwise follow dsh's own home
# resolution ($DSH_HOME, default ~/.dsh) so a relocated home stays consistent.
DSH_HOME_DIR="${DSH_HOME:-$HOME/.dsh}"
DST_DIR="${DSH_SKILLS_HOME:-$DSH_HOME_DIR/skills}"
DEPLOY_GROUPS=()
UNDEPLOY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --global) DST_DIR="${DSH_SKILLS_HOME:-$DSH_HOME_DIR/skills}"; shift ;;
    --group)  DEPLOY_GROUPS+=("$2"); shift 2 ;;
    --undeploy|--unploy) UNDEPLOY=true; shift ;;
    *) shift ;;
  esac
done

if [[ ! -d "$SRC_DIR" && "$UNDEPLOY" != true ]]; then
  err "dsh-skills/ directory not found. Run scripts/deploy/generate.sh first."
  exit 1
fi

bootstrap_resolve_deploy_groups "default"
if [[ "$UNDEPLOY" == true ]]; then
  bootstrap_undeploy_skills "DSH" "$SRC_DIR" "$DST_DIR" "dir"
  exit 0
fi

info "Deploying to user scope: ${C_BOLD}$DST_DIR${C_RESET}"
bootstrap_deploy_skills "DSH" "$SRC_DIR" "$DST_DIR" "dir" "${DEPLOY_GROUPS[@]}"

info "DSH skills synced to ${C_BOLD}$DST_DIR${C_RESET}"
info "Verify with: ${C_BOLD}dsh --profile web${C_RESET} then ${C_BOLD}/<skill-name>${C_RESET}, or list the skills directory."

#!/usr/bin/env bash
# bootstrap_skill_filter.sh — Per-skill literature path gate for bootstrap scripts
#
# Requires: skill_groups.sh sourced, ui.sh for colors optional

LITERATURE_SKIPPED=()

bootstrap_reset_literature_skipped() {
  LITERATURE_SKIPPED=()
}

# Returns 0 if skill should be symlinked
bootstrap_should_link_skill() {
  local skill="$1"
  shift
  local groups=("$@")

  if ! is_skill_in_groups "$skill" "${groups[@]}"; then
    return 1
  fi

  if is_skill_in_group "$skill" "literature"; then
    if ! is_skill_path_ready "$skill"; then
      LITERATURE_SKIPPED+=("$skill")
      return 1
    fi
  fi
  return 0
}

bootstrap_print_literature_skips() {
  local agent="${1:-Skills}"
  if [[ ${#LITERATURE_SKIPPED[@]} -eq 0 ]]; then
    return 0
  fi
  if [[ "${UI_LEVEL:-full}" == "silent" ]]; then
    return 0
  fi
  local skill
  printf "\n  ${C_YELLOW}Skipped literature skills (paths not ready):${C_RESET}\n"
  for skill in "${LITERATURE_SKIPPED[@]}"; do
    printf "    ${C_DIM}- %s${C_RESET}\n" "$skill"
    if [[ "${UI_LEVEL:-full}" != "silent" ]]; then
      python3 "$CHECK_PATHS" --skill "$skill" --setup-hints 2>&1 | head -12 | sed 's/^/      /' || true
    fi
  done
  printf "  ${C_DIM}Retry:${C_RESET} bash deploy.sh --group literature -y\n"
  printf "  ${C_DIM}Guide:${C_RESET} README.md § Literature / Zotero 条件部署\n\n"
}

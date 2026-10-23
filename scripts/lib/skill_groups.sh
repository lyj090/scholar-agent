#!/usr/bin/env bash
# skill_groups.sh — Parse shared/config/skill-groups.yaml and provide group filtering
#
# Usage (source in bootstrap scripts):
#   source "$SCRIPT_DIR/lib/skill_groups.sh"
#   init_skill_groups                         # parse YAML once
#   is_skill_in_groups "research" "core"      # returns 0 if skill is in group
#   get_default_groups                        # prints default group names
#   get_group_skills "core"                   # prints skill names in group

SKILL_GROUPS_YAML="${SKILL_GROUPS_YAML:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/shared/config/skill-groups.yaml}"
_SKILL_GROUPS_CACHE=""

# Parse YAML and cache result (requires python3 + yaml)
init_skill_groups() {
  if [[ -n "$_SKILL_GROUPS_CACHE" ]]; then
    return 0
  fi
  if [[ ! -f "$SKILL_GROUPS_YAML" ]]; then
    echo "error: skill-groups.yaml not found: $SKILL_GROUPS_YAML" >&2
    return 1
  fi
  _SKILL_GROUPS_CACHE=$(python3 - "$SKILL_GROUPS_YAML" <<'PYTHON'
import sys, yaml

# The group descriptions are Chinese. Both ends need an explicit UTF-8: reading
# the YAML, and writing the result to stdout — on Windows, or a C locale, the
# locale codec is ASCII and the write raises after the work is already done.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open(sys.argv[1], encoding="utf-8") as f:
    data = yaml.safe_load(f)

groups = data.get("groups", {})
defaults = data.get("defaults", [])
cursor_defaults = data.get("cursor_defaults", defaults)

# Output format: GROUP:skill1,skill2,...
for gname, gdata in groups.items():
    skills = gdata.get("skills", [])
    print(f"GROUP:{gname}:{','.join(skills)}")

auto_groups = data.get("auto_groups", {})
for gname, gdata in auto_groups.items():
    req_file = gdata.get("requires_file", "")
    req_keys = gdata.get("requires_keys", [])
    print(f"AUTO_GROUP:{gname}:{req_file}:{','.join(req_keys)}")

print(f"DEFAULTS:{','.join(defaults)}")
print(f"CURSOR_DEFAULTS:{','.join(cursor_defaults)}")
PYTHON
  )
}

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
CHECK_PATHS="${CHECK_PATHS:-$REPO_ROOT/scripts/deploy/check_paths.py}"

# Check if skill belongs to a named group (membership, not deploy selection)
is_skill_in_group() {
  local skill="$1"
  local group="$2"
  init_skill_groups
  local allowed
  allowed=$(get_group_skills "$group")
  echo "$allowed" | grep -qx "$skill"
}

# Returns 0 if auto_group prerequisites are met
is_auto_group_ready() {
  local group="$1"
  init_skill_groups
  local line req_file req_keys
  line=$(echo "$_SKILL_GROUPS_CACHE" | grep "^AUTO_GROUP:${group}:" || true)
  [[ -n "$line" ]] || return 1
  req_file=$(echo "$line" | cut -d: -f3)
  req_keys=$(echo "$line" | cut -d: -f4)
  if [[ -n "$req_file" && "$req_file" != " " ]]; then
    [[ -f "$REPO_ROOT/$req_file" ]] || return 1
  fi
  if [[ -z "$req_keys" ]]; then
    return 0
  fi
  local key
  IFS=',' read -ra _keys <<< "$req_keys"
  for key in "${_keys[@]}"; do
    [[ -n "$key" ]] || continue
    python3 "$CHECK_PATHS" --check-key "$key" --quiet 2>/dev/null || return 1
  done
  return 0
}

# Per-skill path gate for literature skills
is_skill_path_ready() {
  local skill="$1"
  python3 "$CHECK_PATHS" --skill "$skill" --quiet 2>/dev/null
}

# Print setup hint when literature group is skipped
print_literature_setup_hint() {
  printf "\n  ${C_YELLOW}⚠${C_RESET} literature 组未部署 — paths.local.yaml 或 obsidian.vault 未就绪\n"
  printf "    ${C_DIM}快速开始:${C_RESET}\n"
  printf "      python3 scripts/deploy/setup_paths.py --apply\n"
  printf "    ${C_DIM}完整说明:${C_RESET} README.md § Literature / Zotero 条件部署\n"
  printf "    ${C_DIM}强制重试:${C_RESET} bash deploy.sh --group literature -y\n\n"
}

# Resolve final deploy groups to stdout (one per line)
# Usage: resolve_deploy_groups <platform> <no_literature> <force_literature> [explicit groups...]
#   platform: default | cursor
#   no_literature: true|false
#   force_literature: true|false (explicit --group literature)
resolve_deploy_groups() {
  local platform="${1:-default}"
  local no_literature="${2:-false}"
  local force_literature="${3:-false}"
  shift 3 || true
  local -a explicit=("$@")
  local -a groups=()
  local g

  if [[ ${#explicit[@]} -gt 0 ]]; then
    groups=("${explicit[@]}")
  elif [[ "$platform" == "cursor" ]]; then
    while IFS= read -r g; do [[ -n "$g" ]] && groups+=("$g"); done < <(get_cursor_default_groups)
  else
    while IFS= read -r g; do [[ -n "$g" ]] && groups+=("$g"); done < <(get_default_groups)
  fi

  if [[ "$no_literature" == "true" ]]; then
    local -a filtered=()
    for g in "${groups[@]}"; do
      [[ "$g" == "literature" ]] && continue
      filtered+=("$g")
    done
    groups=("${filtered[@]}")
  elif [[ "$force_literature" == "true" ]]; then
    local has_lit=false
    for g in "${groups[@]}"; do
      [[ "$g" == "literature" ]] && has_lit=true
    done
    [[ "$has_lit" == false ]] && groups+=("literature")
  elif [[ "$platform" == "cursor" ]]; then
    # Cursor now uses the same defaults as every other platform (uniform
    # deployment); literature skills still individually path-gated at runtime.
    :
  elif is_auto_group_ready "literature"; then
    local has_lit=false
    for g in "${groups[@]}"; do
      [[ "$g" == "literature" ]] && has_lit=true
    done
    [[ "$has_lit" == false ]] && groups+=("literature")
  fi

  # dedupe preserving order
  local -a out=()
  for g in "${groups[@]}"; do
    local dup=false
    for o in "${out[@]}"; do
      [[ "$o" == "$g" ]] && dup=true
    done
    [[ "$dup" == false ]] && out+=("$g")
  done
  for g in "${out[@]}"; do
    echo "$g"
  done
}

group_contains() {
  local name="$1"
  shift
  local g
  for g in "$@"; do
    [[ "$g" == "$name" ]] && return 0
  done
  return 1
}

# Get skills in a specific group (one per line)
get_group_skills() {
  local group="$1"
  init_skill_groups
  echo "$_SKILL_GROUPS_CACHE" | grep "^GROUP:${group}:" | cut -d: -f3 | tr ',' '\n'
}

# Get default group names (one per line)
get_default_groups() {
  init_skill_groups
  echo "$_SKILL_GROUPS_CACHE" | grep "^DEFAULTS:" | cut -d: -f2 | tr ',' '\n'
}

# Get Cursor default group names (one per line)
get_cursor_default_groups() {
  init_skill_groups
  echo "$_SKILL_GROUPS_CACHE" | grep "^CURSOR_DEFAULTS:" | cut -d: -f2 | tr ',' '\n'
}

# Get all skills for multiple groups (one per line, deduplicated)
get_skills_for_groups() {
  local groups=("$@")
  local all_skills=""
  for g in "${groups[@]}"; do
    local skills
    skills=$(get_group_skills "$g")
    if [[ -n "$skills" ]]; then
      all_skills="${all_skills}${all_skills:+$'\n'}${skills}"
    fi
  done
  echo "$all_skills" | sort -u
}

# Check if a skill name is in one of the allowed groups
# Returns 0 (true) if skill is in any of the specified groups
is_skill_in_groups() {
  local skill="$1"
  shift
  local groups=("$@")
  local allowed
  allowed=$(get_skills_for_groups "${groups[@]}")
  echo "$allowed" | grep -qx "$skill"
}

# List all available group names
list_groups() {
  init_skill_groups
  echo "$_SKILL_GROUPS_CACHE" | grep "^GROUP:" | cut -d: -f2
}

# Describe all groups: name, description, and skills list
# Output format per group: 3 lines — name, description, skills (comma-separated)
describe_groups() {
  if [[ ! -f "$SKILL_GROUPS_YAML" ]]; then
    echo "error: skill-groups.yaml not found" >&2
    return 1
  fi
  python3 - "$SKILL_GROUPS_YAML" <<'PYTHON'
import sys, yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open(sys.argv[1], encoding="utf-8") as f:
    data = yaml.safe_load(f)

groups = data.get("groups", {})
defaults = data.get("defaults", [])

for gname, gdata in groups.items():
    desc = gdata.get("description", "")
    skills = gdata.get("skills", [])
    is_default = "yes" if gname in defaults else "no"
    # Format: name|description|skill1,skill2,...|default
    print(f"{gname}|{desc}|{','.join(skills)}|{is_default}")
PYTHON
}

# Print a formatted group listing (for --list-groups and interactive mode)
print_groups_table() {
  local idx=0
  while IFS='|' read -r name desc skills is_default; do
    idx=$((idx + 1))
    local marker=" "
    [[ "$is_default" == "yes" ]] && marker="✔"
    printf "  ${C_BOLD}[%d]${C_RESET} %s ${C_GREEN}%-12s${C_RESET} — %s\n" "$idx" "$marker" "$name" "$desc"
    printf "      ${C_DIM}Skills: %s${C_RESET}\n" "$skills"
  done < <(describe_groups)
}

# Interactive group selection — sets SELECTED_GROUPS array
# Requires terminal (stdin is a tty)
interactive_select_groups() {
  local -a group_names=()
  local -a group_defaults=()
  local -a selected=()

  while IFS='|' read -r name desc skills is_default; do
    group_names+=("$name")
    if [[ "$is_default" == "yes" ]]; then
      group_defaults+=("1")
      selected+=("1")
    else
      group_defaults+=("0")
      selected+=("0")
    fi
  done < <(describe_groups)

  local total=${#group_names[@]}

  while true; do
    printf "\n${C_BOLD}  Select workflow groups to deploy:${C_RESET}\n\n"
    local idx=0
    while IFS='|' read -r name desc skills is_default; do
      local marker="  "
      [[ "${selected[$idx]}" == "1" ]] && marker="${C_GREEN}✔${C_RESET} "
      printf "  ${C_BOLD}[%d]${C_RESET} %s${C_GREEN}%-12s${C_RESET} — %s\n" "$((idx + 1))" "$marker" "$name" "$desc"
      printf "       ${C_DIM}%s${C_RESET}\n" "$skills"
      idx=$((idx + 1))
    done < <(describe_groups)

    printf "\n  ${C_DIM}Toggle: type numbers (e.g. 1,3) | [a]ll on | [n]one | [Enter] confirm${C_RESET}\n"
    printf "  > "
    read -r input

    case "$input" in
      "") break ;;
      a|A|all)
        for ((i=0; i<total; i++)); do selected[$i]="1"; done
        ;;
      n|N|none)
        for ((i=0; i<total; i++)); do selected[$i]="0"; done
        ;;
      *)
        IFS=',' read -ra nums <<< "$input"
        for num in "${nums[@]}"; do
          num=$(echo "$num" | tr -d ' ')
          if [[ "$num" =~ ^[0-9]+$ ]] && (( num >= 1 && num <= total )); then
            local i=$((num - 1))
            [[ "${selected[$i]}" == "1" ]] && selected[$i]="0" || selected[$i]="1"
          fi
        done
        ;;
    esac
    # Clear screen section for re-draw (move up)
    for ((i=0; i < total*2 + 5; i++)); do printf "\033[A\033[K"; done
  done

  # Export result
  INTERACTIVE_GROUPS=()
  for ((i=0; i<total; i++)); do
    if [[ "${selected[$i]}" == "1" ]]; then
      INTERACTIVE_GROUPS+=("${group_names[$i]}")
    fi
  done
}

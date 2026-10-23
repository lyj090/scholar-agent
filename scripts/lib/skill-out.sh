#!/usr/bin/env bash
# skill-out.sh — compact stdout for skill-related scripts (agent context budget)
# Format: KEY=value lines only; ≤6 lines per invocation; details → files not chat.
#
# Usage: source "$(dirname ...)/lib/skill-out.sh"

SKILL_OUT_MAX_LINES="${SKILL_OUT_MAX_LINES:-6}"

skill_out_compact_default() {
  [[ -n "${SKILLS_COMPACT:-}" ]] && return 0
  [[ ! -t 1 ]] && return 0
  return 1
}

# Emit one line; no-op if over budget (except ERR always prints).
skill_out() {
  local key="$1"
  local val="$2"
  if [[ "$key" == ERR* ]] || [[ "$key" == "ERR" ]]; then
    echo "${key}=${val}"
    return 0
  fi
  echo "${key}=${val}"
}

skill_out_err() {
  echo "ERR $1"
  [[ -n "${2:-}" ]] && echo "MSG=$2"
}

skill_out_ok() {
  echo "OK $1"
}

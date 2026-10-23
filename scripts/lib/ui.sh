#!/usr/bin/env bash
# ui.sh — Common UI helpers for scholar-agent scripts.
#
# Output level model (3 tiers + auto-detect):
#   full    → banner + headers + per-item info/ok + animated progress + summary
#   quiet   → headers + summary (no banner, no per-item, no animation)
#   silent  → errors only + final 1-line OK/FAIL
#
# Auto-detect on source:
#   stdout not a TTY                       → quiet
#   $CLAUDE_AGENT or $CI is non-empty      → silent
#   $DEPLOY_QUIET=1 / $DEPLOY_SILENT=1     → forces level
#   later, deploy.sh may call apply_ui_overrides() to honor CLI flags

# ─── Colors ─────────────────────────────────────────────────────────────────

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
  C_CYAN=$'\033[36m'
  C_BLUE=$'\033[34m'
  C_MAGENTA=$'\033[35m'
else
  C_RESET='' C_BOLD='' C_DIM='' C_GREEN='' C_YELLOW='' C_RED='' C_CYAN='' C_BLUE='' C_MAGENTA=''
fi

# ─── Text measurement (display columns, not bytes) ──────────────────────────
# Bash counts bytes, so "🏁" (4 bytes, 2 columns) and "中" (3 bytes, 2 columns)
# break %-Ns padding — the classic misaligned box-border bug. Everything below
# measures in terminal columns instead.

# Terminal width, clamped to a readable range. Falls back to 80 when detached.
ui_term_width() {
  local w
  w="$(tput cols 2>/dev/null || echo 80)"
  [[ "$w" =~ ^[0-9]+$ ]] || w=80
  (( w > 100 )) && w=100
  (( w < 56 )) && w=56
  printf '%s' "$w"
}

# Visible width of $1 in columns (ANSI stripped, wide chars + emoji = 2).
ui_width() {
  printf '%s' "$1" | sed -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' | wc -L
}

# Left-align $1 into $2 columns. Truncates with "…" when it does not fit.
ui_pad() {
  local s="$1" w="$2"
  local cur pad
  cur="$(ui_width "$s")"
  if (( cur > w )); then
    local keep=$(( w - 1 ))
    (( keep < 0 )) && keep=0
    s="$(printf '%s' "$s" | cut -c1-$(( keep )))…"
    cur=$w
  fi
  pad=$(( w - cur ))
  (( pad < 0 )) && pad=0
  printf '%s%*s' "$s" "$pad" ""
}

# Right-align $1 into $2 columns.
ui_rpad() {
  local s="$1" w="$2"
  local cur pad
  cur="$(ui_width "$s")"
  pad=$(( w - cur ))
  (( pad < 0 )) && pad=0
  printf '%*s%s' "$pad" "" "$s"
}

# ─── UI level ───────────────────────────────────────────────────────────────

UI_LEVEL="${UI_LEVEL:-full}"

auto_detect_ui_level() {
  # Highest-priority explicit env vars win.
  if [[ -n "${DEPLOY_SILENT:-}" ]]; then UI_LEVEL=silent; return; fi
  if [[ -n "${DEPLOY_QUIET:-}" ]];  then UI_LEVEL=quiet;  return; fi
  # Agent / CI sentinel → silent (1-line summary).
  if [[ -n "${CLAUDE_AGENT:-}" || -n "${CI:-}" ]]; then UI_LEVEL=silent; return; fi
  # Plain non-TTY (piped, redirected) → quiet (no animation, headers kept).
  if [[ ! -t 1 ]]; then UI_LEVEL=quiet; return; fi
  UI_LEVEL=full
}
auto_detect_ui_level

# Called by deploy.sh after CLI parsing — flag wins over auto-detect.
# Usage: apply_ui_overrides "$CLI_QUIET_FLAG" "$CLI_SILENT_FLAG"   ("" / "1")
apply_ui_overrides() {
  local q="${1:-}"
  local s="${2:-}"
  [[ "$s" == "1" ]] && { UI_LEVEL=silent; return 0; }
  [[ "$q" == "1" ]] && { UI_LEVEL=quiet;  return 0; }
  return 0
}

ui_is_full()   { [[ "$UI_LEVEL" == "full"   ]]; }
ui_is_quiet()  { [[ "$UI_LEVEL" == "quiet"  ]]; }
ui_is_silent() { [[ "$UI_LEVEL" == "silent" ]]; }

# ─── Logging primitives (level-aware) ───────────────────────────────────────

# info / ok hidden when quiet or silent.
info()   { ui_is_full && printf "  ${C_DIM}${C_CYAN}ℹ${C_RESET} %s\n" "$*"; return 0; }
ok()     { ui_is_full && printf "  ${C_GREEN}✔${C_RESET} %s\n" "$*"; return 0; }
# warn always to stderr; suppressed only at silent.
warn()   { ui_is_silent || printf "  ${C_YELLOW}⚠${C_RESET} %s\n" "$*" >&2; return 0; }
# err always shown.
err()    { printf "  ${C_RED}✖${C_RESET} %s\n" "$*" >&2; }

# ─── Header (boxed section title) ───────────────────────────────────────────

# Hidden at silent. Shown at full+quiet.
# Width adapts to the terminal and is measured in columns, so emoji titles
# (🧠 🖱️ 🎭) align with plain-text ones.
header() {
  ui_is_silent && return 0
  local text=" $1 "
  local color="${2:-$C_BLUE}"
  local tw inner rule
  tw="$(ui_term_width)"
  inner=$(( tw - 4 ))
  (( inner < 8 )) && inner=8
  rule="$(printf '━%.0s' $(seq 1 $(( tw - 2 ))))"

  printf "\n${C_BOLD}${color}┏%s┓${C_RESET}\n" "$rule"
  printf "${C_BOLD}${color}┃ ${C_RESET}%s${C_BOLD}${color} ┃${C_RESET}\n" "$(ui_pad "$text" "$inner")"
  printf "${C_BOLD}${color}┗%s┛${C_RESET}\n" "$rule"
}

# ─── Horizontal rule ────────────────────────────────────────────────────────

# Full-width divider; pass "thin" for a lighter variant.
hr() {
  ui_is_silent && return 0
  local tw char
  tw="$(ui_term_width)"
  char="─"
  [[ "${1:-}" == "thin" ]] && char="┄"
  local rule
  rule="$(printf '%s%.0s' "$char" $(seq 1 "$tw"))"
  printf "${C_DIM}%s${C_RESET}\n" "$rule"
}

# ─── Aligned key/value row ──────────────────────────────────────────────────

# Right-aligned label + value, e.g. "   CODEX  ✔ ACTIVE".
# Usage: kv_row <label> <value> [label_width]
kv_row() {
  ui_is_silent && return 0
  local label="$1" value="$2" lw="${3:-8}"
  printf "  ${C_BOLD}%s${C_RESET} ${C_DIM}│${C_RESET} %s\n" \
    "$(ui_rpad "$label" "$lw")" "$value"
}

# Status pill: " ACTIVE " / " inactive " in a fixed-width column so rows line up.
status_pill() {
  local val="$1" color="${2:-$C_GREEN}"
  if [[ "$val" == true ]]; then
    printf "${C_BOLD}${color} ● ACTIVE  ${C_RESET}"
  else
    printf "${C_DIM} ○ inactive${C_RESET}"
  fi
}

# ─── Banner (full level only) ───────────────────────────────────────────────

# 256-color cyan→blue sweep. Each line gets one shade for a vertical gradient.
_BANNER_GRADIENT=(51 45 39 33 27 21)

_banner_emit() {
  ui_is_full || return 0
  local -a lines=("$@")
  local i=0
  for line in "${lines[@]}"; do
    local shade="${_BANNER_GRADIENT[$(( i % ${#_BANNER_GRADIENT[@]} ))]}"
    if [[ -t 1 ]]; then
      printf "${C_BOLD}\033[38;5;%sm%s${C_RESET}\n" "$shade" "$line"
    else
      printf "%s\n" "$line"
    fi
    i=$(( i + 1 ))
  done
}

banner_ai_workflows() {
  _banner_emit \
"  █████╗ ██╗     ██╗    ██╗ ██████╗ ██████╗ ██╗  ██╗██╗      ██████╗ ██╗    ██╗███████╗" \
" ██╔══██╗██║     ██║    ██║██╔═══██╗██╔══██╗██║ ██╔╝██║     ██╔═══██╗██║    ██║██╔════╝" \
" ███████║██║     ██║ █╗ ██║██║   ██║██████╔╝█████╔╝ ██║     ██║   ██║██║ █╗ ██║███████╗" \
" ██╔══██║██║     ██║███╗██║██║   ██║██╔══██╗██╔═██╗ ██║     ██║   ██║██║███╗██║╚════██║" \
" ██║  ██║██║     ╚███╔███╔╝╚██████╔╝██║  ██║██║  ██╗███████╗╚██████╔╝╚███╔███╔╝███████║" \
" ╚═╝  ╚═╝╚═╝      ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝  ╚══╝╚══╝ ╚══════╝"
}

# ─── Progress bar ───────────────────────────────────────────────────────────

# Animated single-line bar at full level; no-op at quiet/silent.
# Usage: progress_bar <current> <total> [label]
progress_bar() {
  ui_is_full || return 0
  [[ -t 1 ]] || return 0
  local cur="$1" total="$2" label="${3:-}"
  (( total > 0 )) || return 0
  local width=24
  local filled=$(( cur * width / total ))
  (( filled > width )) && filled=$width
  local pct=$(( cur * 100 / total ))
  local bar=""
  local i=0
  while (( i < filled )); do bar+="█"; i=$(( i + 1 )); done
  while (( i < width  )); do bar+="░"; i=$(( i + 1 )); done
  # \r to overwrite the same line; \033[K to clear remainder.
  printf "\r  ${C_CYAN}%s${C_RESET} ${C_BOLD}%3d%%${C_RESET} ${C_DIM}(%d/%d)${C_RESET} %s\033[K" \
    "$bar" "$pct" "$cur" "$total" "$label"
}

progress_done() {
  ui_is_full || return 0
  [[ -t 1 ]] || return 0
  printf "\r\033[K"
}

# ─── Summary line (printed at all levels except silent-by-itself) ──────────

# Compact "Agent: N/M linked, K skipped" — emitted by bootstrap scripts after
# the per-skill loop. Always shown at full and quiet; at silent it's suppressed
# in favor of deploy.sh's final 1-line aggregate.
summary_line() {
  ui_is_silent && return 0
  local agent="$1" linked="$2" total="$3"
  local skipped=$(( total - linked ))
  if (( skipped > 0 )); then
    printf "  ${C_GREEN}✔${C_RESET} %s: ${C_BOLD}%d/%d${C_RESET} linked, ${C_DIM}%d skipped${C_RESET}\n" \
      "$agent" "$linked" "$total" "$skipped"
  else
    printf "  ${C_GREEN}✔${C_RESET} %s: ${C_BOLD}%d/%d${C_RESET} linked\n" \
      "$agent" "$linked" "$total"
  fi
}

summary_removed_line() {
  ui_is_silent && return 0
  local agent="$1" removed="$2" preserved="$3"
  printf "  ${C_GREEN}✔${C_RESET} %s: ${C_BOLD}%d${C_RESET} removed" "$agent" "$removed"
  (( preserved > 0 )) && printf ", ${C_DIM}%d preserved${C_RESET}" "$preserved"
  printf "\n"
}

# ─── Structured action recorder (unchanged, level-independent) ──────────────

# Record action for summary table.
# Usage: record_action <agent> <skill> <status:OK|SKIP>
record_action() {
  if [[ -n "${DEPLOY_REPORT:-}" ]]; then
    printf "%s|%s|%s\n" "$1" "$2" "$3" >> "$DEPLOY_REPORT"
  fi
}

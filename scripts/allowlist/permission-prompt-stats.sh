#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# permission-prompt-stats.sh — 弹窗统计 + review + 分流加白
# ─────────────────────────────────────────────────────────────────────────────
# permission-counter.sh hook 把每次权限弹窗的命令归一化成 Bash(...) pattern
# 累计到 ~/.claude/permission-prompt-counts.json。本脚本读它，
# 让你（或 agent）决定加到 shared 还是 local。
#
# 用法：
#   list   [--min N] [--json]                 列出计数 ≥N（默认 3）
#   review [--min N]                          交互式审阅；逐条问 [s]hared/[l]ocal/[k]ip/[d]elete
#   apply  --scope shared|local --pattern "Bash(...)"
#                                             非交互式加白；agent 调用入口
#   remove --pattern "Bash(...)"              从计数文件删该条
#   clear                                     清空计数
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SHARED_FILE="$REPO_ROOT/shared/config/allowlist-common.json"
COUNTS_FILE="${HOME}/.claude/permission-prompt-counts.json"
SETTINGS="${HOME}/.claude/settings.json"

if [[ -t 1 ]]; then
  G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; D=$'\033[2m'; C=$'\033[36m'; R=$'\033[0m'
else G=''; Y=''; B=''; D=''; C=''; R=''; fi

usage() { sed -n '2,20p' "$0" | sed 's|^# \?||'; exit "${1:-0}"; }

ensure_counts() { [[ -f "$COUNTS_FILE" ]] || echo '{}' > "$COUNTS_FILE"; }
command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }

# ── list ─────────────────────────────────────────────────────────────────────
cmd_list() {
  local min=3 json=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --min) min="$2"; shift 2 ;;
      --json) json=true; shift ;;
      *) usage 2 ;;
    esac
  done
  ensure_counts

  if [[ "$json" == true ]]; then
    jq --argjson min "$min" '
      to_entries | map(select(.value.count >= $min))
      | sort_by(-.value.count)
      | map({pattern: .key, count: .value.count, last_seen: .value.last_seen, examples: .value.examples})
    ' "$COUNTS_FILE"
    return
  fi

  echo "${B}Permission-prompt counts (≥ $min):${R}"
  jq -r --argjson min "$min" '
    to_entries | map(select(.value.count >= $min))
    | sort_by(-.value.count)
    | .[]
    | [
        (.value.count | tostring),
        .key,
        ((.value.examples[-1] // "") | gsub("[\n\t]"; " ") | .[0:120])
      ]
    | @tsv
  ' "$COUNTS_FILE" \
    | awk -F'\t' -v c1="$Y" -v c2="$C" -v d="$D" -v r="$R" '
        { printf "  %s%4d%s  %s%s%s\n          %sex:%s %s%s%s\n", c1,$1,r, c2,$2,r, d,r, d,$3,r }
      '

  local total
  total=$(jq --argjson min "$min" 'to_entries | map(select(.value.count >= $min)) | length' "$COUNTS_FILE")
  echo
  echo "  ${B}$total${R} patterns above threshold."
}

# ── apply (non-interactive add) ──────────────────────────────────────────────
cmd_apply() {
  local scope="" pattern=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scope) scope="$2"; shift 2 ;;
      --pattern) pattern="$2"; shift 2 ;;
      *) usage 2 ;;
    esac
  done
  [[ -z "$scope" || -z "$pattern" ]] && { echo "need --scope and --pattern" >&2; exit 2; }
  case "$scope" in shared|local) ;; *) echo "scope must be shared|local" >&2; exit 2 ;; esac

  local target
  case "$scope" in
    shared) target="$SHARED_FILE" ;;
    local)  target="$SETTINGS" ;;
  esac
  [[ -f "$target" ]] || { echo "missing $target" >&2; exit 1; }

  # already present?
  if jq -e --arg p "$pattern" '(.allowedTools // []) | index($p)' "$target" >/dev/null; then
    echo "  ${D}skip${R} (already in $scope): $pattern"
    return
  fi

  local tmp
  tmp="$(mktemp "$target.XXXXXX")"
  jq --arg p "$pattern" '
    .allowedTools = ((.allowedTools // []) + [$p] | unique_by(.))
  ' "$target" > "$tmp"
  jq empty "$tmp" >/dev/null || { rm -f "$tmp"; echo "invalid JSON, abort" >&2; exit 1; }
  mv -f "$tmp" "$target"
  echo "  ${G}added${R} → $scope: $pattern"
}

# ── remove from counts ───────────────────────────────────────────────────────
cmd_remove() {
  local pattern=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --pattern) pattern="$2"; shift 2 ;;
      *) usage 2 ;;
    esac
  done
  [[ -z "$pattern" ]] && { echo "need --pattern" >&2; exit 2; }
  ensure_counts

  local tmp
  tmp="$(mktemp "$COUNTS_FILE.XXXXXX")"
  jq --arg p "$pattern" 'del(.[$p])' "$COUNTS_FILE" > "$tmp"
  mv -f "$tmp" "$COUNTS_FILE"
  echo "  ${G}removed from counts${R}: $pattern"
}

# ── clear ────────────────────────────────────────────────────────────────────
cmd_clear() {
  echo '{}' > "$COUNTS_FILE"
  echo "  ${G}counts cleared${R}: $COUNTS_FILE"
}

# ── review (interactive) ─────────────────────────────────────────────────────
cmd_review() {
  local min=3
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --min) min="$2"; shift 2 ;;
      *) usage 2 ;;
    esac
  done
  ensure_counts

  local items
  items=$(jq -r --argjson min "$min" '
    to_entries | map(select(.value.count >= $min))
    | sort_by(-.value.count)
    | .[]
    | [
        (.value.count | tostring),
        .key,
        ((.value.examples[-1] // "") | gsub("[\n\t]"; " ") | .[0:120])
      ]
    | @tsv
  ' "$COUNTS_FILE")

  if [[ -z "$items" ]]; then
    echo "  ${D}no patterns above threshold $min${R}"
    return
  fi

  if ! [[ -t 0 ]]; then
    echo "review needs a TTY for interactive prompts; use 'list --json' + 'apply' for non-TTY" >&2
    exit 2
  fi

  while IFS=$'\t' read -r count pattern example; do
    echo
    echo "  ${B}[$count×]${R} ${C}$pattern${R}"
    echo "  ${D}example: $example${R}"
    # shared 检查
    if jq -e --arg p "$pattern" '(.allowedTools // []) | index($p)' "$SHARED_FILE" >/dev/null; then
      echo "  ${D}(already in shared)${R}"
    elif jq -e --arg p "$pattern" '(.allowedTools // []) | index($p)' "$SETTINGS" >/dev/null; then
      echo "  ${D}(already in local)${R}"
    fi

    while true; do
      read -r -p "  add to [s]hared / [l]ocal / [k]ip / [d]elete-from-counts / [q]uit > " ans
      case "$ans" in
        s|S) cmd_apply --scope shared --pattern "$pattern"; cmd_remove --pattern "$pattern"; break ;;
        l|L) cmd_apply --scope local --pattern "$pattern"; cmd_remove --pattern "$pattern"; break ;;
        k|K) echo "  ${D}skipped${R}"; break ;;
        d|D) cmd_remove --pattern "$pattern"; break ;;
        q|Q) echo "  ${D}quit${R}"; return ;;
        *) echo "    pick one of: s l k d q" ;;
      esac
    done
  done <<<"$items"

  echo
  echo "${G}review done.${R}"
  echo "${D}note: shared changes go to git-tracked $SHARED_FILE — commit & push to sync to other devices.${R}"
}

# ── dispatch ─────────────────────────────────────────────────────────────────
case "${1:-}" in
  list)   shift; cmd_list "$@" ;;
  review) shift; cmd_review "$@" ;;
  apply)  shift; cmd_apply "$@" ;;
  remove) shift; cmd_remove "$@" ;;
  clear)  shift; cmd_clear "$@" ;;
  -h|--help|help|"") usage 0 ;;
  *) usage 2 ;;
esac

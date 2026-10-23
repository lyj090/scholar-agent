#!/usr/bin/env bash
# schedule.sh — Manage all system-scheduled workflows for scholar-agent.
#
# Usage:
#   bash scripts/deploy/schedule.sh list
#   bash scripts/deploy/schedule.sh enable <workflow> [time]
#   bash scripts/deploy/schedule.sh disable <workflow>

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MARK_PREFIX="# scholar-agent: schedule:"

list_workflows() {
  echo "Available workflows to schedule:"
  echo "  1) tag-governance       - Audit Zotero & Obsidian tags    (Default: Monday 09:00)"
  echo "  2) allowlist-clean      - Clean settings allowlist        (Default: 09:07)"
  echo "  3) mineru-sync          - Parse MinerU for new papers     (Default: hourly at :20)"
  echo ""
  echo "Active scheduled cron jobs:"
  local active
  active="$(crontab -l 2>/dev/null | grep -E "$MARK_PREFIX" || true)"
  if [[ -z "$active" ]]; then
    echo "  No active scheduled workflows managed by this tool found."
  else
    echo "$active" | sed 's/^/  /'
  fi

}

enable_workflow() {
  local name="${1:-}"
  local time="${2:-}"
  
  if [[ -z "$name" ]]; then
    echo "ERR: Workflow name is required. Run 'schedule.sh list' to see options." >&2
    exit 1
  fi
  
  local cron_line=""
  
  case "$name" in
    tag-governance)
      # Weekly on Monday at 09:00 by default
      local t="${time:-09:00}"
      local hh="${t%%:*}" mm="${t##*:}"
      [[ "$hh" =~ ^[0-9]{1,2}$ && "$mm" =~ ^[0-9]{1,2}$ ]] || { echo "bad time '$t' (want HH:MM)" >&2; exit 2; }
      cron_line="$mm $hh * * 1 python3 $REPO_ROOT/scripts/literature/zotero_cli.py audit-notes --errors-only >> $REPO_ROOT/tag-governance.log 2>&1"
      ;;
    allowlist-clean)
      local t="${time:-09:07}"
      local hh="${t%%:*}" mm="${t##*:}"
      [[ "$hh" =~ ^[0-9]{1,2}$ && "$mm" =~ ^[0-9]{1,2}$ ]] || { echo "bad time '$t' (want HH:MM)" >&2; exit 2; }
      cron_line="$mm $hh * * * bash $REPO_ROOT/scripts/allowlist/allowlist-daily-clean.sh --run"
      ;;
    mineru-sync)
      # Hourly by default. This replaces the llm-for-zotero plugin's notifier-based
      # auto-parse: polling is enough because one parse takes seconds to minutes,
      # and it keeps the work out of Zotero's process entirely.
      local t="${time:-:20}"
      local mm="${t##*:}"
      [[ "$mm" =~ ^[0-9]{1,2}$ ]] || { echo "bad minute '$t' (want :MM)" >&2; exit 2; }
      # --limit keeps a single tick bounded so a large backlog drains gradually
      # instead of hammering the MinerU API in one burst.
      cron_line="$mm * * * * python3 $REPO_ROOT/scripts/literature/zotero_cli.py mineru-sync --limit 5 --quiet >> $REPO_ROOT/mineru-sync.log 2>&1"
      ;;
    *)
      echo "Unknown workflow: $name. Run 'schedule.sh list' to see options." >&2
      exit 1
      ;;
  esac

  local mark="$MARK_PREFIX$name"
  local current; current="$(crontab -l 2>/dev/null | grep -vF "$mark" || true)"
  printf '%s\n%s\n' "$current" "$cron_line $mark" | grep -v '^$' | crontab -
  echo "Enabled '$name' with schedule: $cron_line"
}

disable_workflow() {
  local name="${1:-}"
  if [[ -z "$name" ]]; then
    echo "ERR: Workflow name is required." >&2
    exit 1
  fi
  local mark="$MARK_PREFIX$name"
  local current; current="$(crontab -l 2>/dev/null | grep -vF "$mark" || true)"
  if [[ -z "$current" ]]; then
    crontab -r 2>/dev/null || true
  else
    printf '%s\n' "$current" | grep -v '^$' | crontab -
  fi
  echo "Disabled '$name'"
}

case "${1:-list}" in
  list) list_workflows ;;
  enable) shift; enable_workflow "$@" ;;
  disable) shift; disable_workflow "$@" ;;
  *) echo "Usage: $0 {list|enable <workflow> [time]|disable <workflow>}" >&2; exit 2 ;;
esac

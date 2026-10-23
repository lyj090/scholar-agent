#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# allowlist-daily-clean.sh — daily allowlist dedup, driven by system cron.
# ─────────────────────────────────────────────────────────────────────────────
# Why a wrapper (not Claude's own scheduler): the cleanup is pure shell — it
# never needs Claude's judgement. A system crontab entry runs regardless of
# whether Claude Code is open, was restarted, or is mid-task; Claude's in-app
# CronCreate only fires while Claude runs and its REPL is idle.
#
# What it does (each run):
#   1. flock so it never races a concurrent Claude session writing settings.
#   2. audit-allowlist.sh fix     --apply   (system: shared + local settings.json)
#   3. audit-allowlist.sh project --apply   (all project .claude/settings*.json)
#   Both already: back up to ~/.claude/backups/, jq-validate, no-op when
#   clean, order-preserving. promote is intentionally NOT run (it writes the
#   git-tracked shared baseline — left to manual review).
#
# Logs to ~/.claude/allowlist-clean.log (rotated at ~512 KB).
# Install / remove the cron entry:   this script --install [HH:MM] / --uninstall
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AUDIT="$REPO_ROOT/scripts/allowlist/audit-allowlist.sh"
LOG="${HOME}/.claude/allowlist-clean.log"
LOCK="${HOME}/.claude/allowlist-clean.lock"
MARK="# scholar-agent: allowlist-daily-clean"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo now)" "$*" >>"$LOG"; }

rotate_log() {
  [[ -f "$LOG" ]] || return 0
  local size
  size="$(stat -c %s "$LOG" 2>/dev/null || echo 0)"
  if [[ "$size" -gt 524288 ]]; then
    mv -f "$LOG" "$LOG.1" 2>/dev/null || true
  fi
}

# ── install / uninstall the crontab entry ────────────────────────────────────
cmd_install() {
  local when="${1:-09:00}"
  local hh mm
  hh="${when%%:*}"; mm="${when##*:}"
  [[ "$hh" =~ ^[0-9]{1,2}$ && "$mm" =~ ^[0-9]{1,2}$ ]] || { echo "bad time '$when' (want HH:MM)" >&2; exit 2; }
  # Nudge off the :00 mark so the whole fleet doesn't hit at once — unless the
  # user pinned an exact non-zero minute.
  [[ "$mm" == "00" ]] && mm="7"

  local line="$mm $hh * * * $(readlink -f "$0") --run  $MARK"
  local current; current="$(crontab -l 2>/dev/null | grep -vF "$MARK" || true)"
  printf '%s\n%s\n' "$current" "$line" | grep -v '^$' | crontab -
  echo "installed cron: $hh:$mm daily"
  echo "  $line"
  crontab -l 2>/dev/null | grep -F "$MARK"
}

cmd_uninstall() {
  local current; current="$(crontab -l 2>/dev/null | grep -vF "$MARK" || true)"
  if [[ -z "$current" ]]; then
    crontab -r 2>/dev/null || true
  else
    printf '%s\n' "$current" | grep -v '^$' | crontab -
  fi
  echo "removed allowlist-daily-clean cron entries"
}

# ── the actual daily run ─────────────────────────────────────────────────────
cmd_run() {
  rotate_log
  log "=== allowlist daily clean start ==="
  [[ -x "$AUDIT" || -f "$AUDIT" ]] || { log "ERROR: $AUDIT missing"; exit 1; }

  log "[system] fix --apply"
  bash "$AUDIT" fix --apply >>"$LOG" 2>&1 || log "WARN: fix returned $?"

  log "[project] project --apply"
  bash "$AUDIT" project --apply >>"$LOG" 2>&1 || log "WARN: project returned $?"

  log "=== done ==="
}

case "${1:-}" in
  --install)   shift; cmd_install "${1:-09:00}" ;;
  --uninstall) cmd_uninstall ;;
  --run|"")
    # Serialize via flock so a daily run never races a concurrent Claude
    # session writing settings. If the lock is held, skip silently (exit 0).
    /usr/bin/flock -n "$LOCK" "$0" --locked-run
    rc=$?
    [[ $rc -eq 1 ]] && exit 0   # flock: lock held → treat as clean skip
    exit $rc
    ;;
  --locked-run) cmd_run ;;
  -h|--help)
    sed -n '2,28p' "$0" | sed 's|^# \?||' ;;
  *) echo "usage: $0 [--install HH:MM | --uninstall | --run]" >&2; exit 2 ;;
esac

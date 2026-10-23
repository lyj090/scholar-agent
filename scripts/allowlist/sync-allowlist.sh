#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# sync-allowlist.sh — 同步 shared/config/allowlist-common.json → 本机 settings.json
# ─────────────────────────────────────────────────────────────────────────────
# 由 deploy.sh 调用（也可手动跑）。维护以下不变量：
#
#   本机.allowedTools = shared.allowedTools  ∪  local-extras
#   其中  local-extras = 上次 sync 后用户/agent 单独加进来的 rule
#                      = current.allowedTools − previous_managed_snapshot
#
# 这样：
#   · shared 加一条 → deploy 后本机自动有
#   · shared 删一条 → deploy 后本机也清掉（不影响用户单独加的同名 local）
#   · 用户手编加的 → 保留
#
# 同时：
#   · 部署 hook 脚本 global-config/permission-counter.sh
#         → ~/.claude/hooks/permission-counter.sh
#   · 在 ~/.claude/settings.json 注入 hooks.PermissionRequest
#
# 使用：
#   sync-allowlist.sh           # 实际同步
#   sync-allowlist.sh --dry-run # 只显示 diff，不写文件
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SHARED_FILE="$REPO_ROOT/shared/config/allowlist-common.json"
HOOK_SRC="$REPO_ROOT/global-config/permission-counter.sh"

CLAUDE_HOME="${HOME}/.claude"
SETTINGS="$CLAUDE_HOME/settings.json"
SNAPSHOT="$CLAUDE_HOME/.allowlist-managed-snapshot.json"
HOOK_DIR="$CLAUDE_HOME/hooks"
HOOK_DST="$HOOK_DIR/permission-counter.sh"

DRY_RUN=false
# The permission hook is opt-in. It records the text of commands that trigger a
# permission prompt, and a default-on recorder of shell history is not a
# reasonable thing for a workflow repo to install silently. `deploy.sh` passes
# --with-hook only when the user asked for it.
INSTALL_HOOK=false
for _arg in "$@"; do
  case "$_arg" in
    --dry-run|-n) DRY_RUN=true ;;
    --with-hook)  INSTALL_HOOK=true ;;
    -h|--help)
      printf 'sync-allowlist.sh [--dry-run] [--with-hook]\n'
      printf '  --with-hook  also install the permission-counter hook (records command text)\n'
      exit 0 ;;
    *) echo "sync-allowlist: unknown arg: $_arg" >&2; exit 2 ;;
  esac
done

# colors
if [[ -t 1 ]]; then
  G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; R=$'\033[0m'
else G=''; Y=''; D=''; B=''; R=''; fi

log()  { printf '  %s\n' "$*"; }
info() { printf "  ${B}%s${R}\n" "$*"; }

# ── 0. preconditions ─────────────────────────────────────────────────────────
[[ -d "$CLAUDE_HOME" ]] || { echo "no $CLAUDE_HOME — skipping" >&2; exit 0; }
[[ -f "$SHARED_FILE" ]]   || { echo "no shared file: $SHARED_FILE" >&2; exit 1; }
if [[ "$INSTALL_HOOK" == true && ! -f "$HOOK_SRC" ]]; then
  echo "no hook source: $HOOK_SRC" >&2; exit 1
fi
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

# ── 1. compute new allowedTools ──────────────────────────────────────────────
# Read shared, current, and previous-managed snapshot. Compute set algebra.
shared_arr="$(jq '.allowedTools // []' "$SHARED_FILE")"
# Deny is a union with whatever the machine already denies and is never pruned:
# dropping a deny rule this script did not add would silently widen permissions.
shared_deny="$(jq '.deniedTools // []' "$SHARED_FILE")"

if [[ -f "$SETTINGS" ]]; then
  current_arr="$(jq '.allowedTools // []' "$SETTINGS")"
else
  current_arr='[]'
fi

if [[ -f "$SNAPSHOT" ]]; then
  prev_managed="$(jq '.allowedTools // []' "$SNAPSHOT")"
else
  # First sync on this machine: treat any current rule that ALSO appears in
  # shared as "previously managed" (i.e. came from baseline). Everything else
  # in current is genuine local extras. Without this, the first run would
  # mis-classify all rules as local-only, defeating later "shared deletes
  # also delete locally" behaviour.
  prev_managed="$(jq -n --argjson cur "$current_arr" --argjson sh "$shared_arr" '
    $cur - ($cur - $sh)
  ')"
fi

# local_extras = current - prev_managed  (anything user added on top)
local_extras="$(jq -n --argjson cur "$current_arr" --argjson prev "$prev_managed" '
  $cur - $prev
')"

# new = shared ∪ local_extras  (dedup, preserve shared order then extras order)
new_arr="$(jq -n --argjson s "$shared_arr" --argjson l "$local_extras" '
  ($s + $l) | unique_by(.)
')"

# diff stats for reporting
added="$(jq -n --argjson cur "$current_arr" --argjson new "$new_arr" '$new - $cur')"
removed="$(jq -n --argjson cur "$current_arr" --argjson new "$new_arr" '$cur - $new')"

n_shared="$(echo "$shared_arr" | jq 'length')"
n_extras="$(echo "$local_extras" | jq 'length')"
n_added="$(echo "$added" | jq 'length')"
n_removed="$(echo "$removed" | jq 'length')"

info "allowlist sync"
log "  shared baseline:   $n_shared rules"
log "  local extras kept: $n_extras rules"
log "  + adding to settings.json:   ${G}$n_added${R}"
log "  - removing from settings.json: ${Y}$n_removed${R}"

if [[ "$n_added" -gt 0 ]]; then
  echo "$added" | jq -r '.[]' | sed "s|^|    ${G}+${R} |"
fi
if [[ "$n_removed" -gt 0 ]]; then
  echo "$removed" | jq -r '.[]' | sed "s|^|    ${Y}-${R} |"
fi

# ── 2. write back to settings.json (preserve other keys) ─────────────────────
# The hook is *merged*, never assigned. `.hooks.PermissionRequest = [...]` used
# to replace the whole array, silently deleting any PermissionRequest hook the
# user already had — deploy.sh runs this script on every deploy, so the damage
# was invisible and repeatable. Idempotent: an entry pointing at HOOK_DST is
# dropped before appending, so re-running cannot duplicate it.
write_settings() {
  local tmp
  tmp="$(mktemp "$SETTINGS.XXXXXX")"
  local want_hook=false
  [[ "$INSTALL_HOOK" == true ]] && want_hook=true

  if [[ -f "$SETTINGS" ]]; then
    jq --argjson new "$new_arr" \
       --argjson deny "$shared_deny" \
       --arg hook "$HOOK_DST" \
       --argjson want "$want_hook" '
      .allowedTools = $new
      | .deniedTools = (((.deniedTools // []) + $deny) | unique)
      | .hooks = (.hooks // {})
      | .hooks.PermissionRequest = (
          ((.hooks.PermissionRequest // [])
            | map(select(
                ([.hooks[]?.command] | any(. == $hook)) | not
              )))
          + (if $want then
               [{ matcher: "Bash",
                  hooks: [{ type: "command", command: $hook }] }]
             else [] end)
        )
      | if .hooks.PermissionRequest == [] then del(.hooks.PermissionRequest) else . end
      | if .hooks == {} then del(.hooks) else . end
    ' "$SETTINGS" > "$tmp"
  else
    jq -n --argjson new "$new_arr" --argjson deny "$shared_deny" \
          --arg hook "$HOOK_DST" --argjson want "$want_hook" '
      {
        allowedTools: $new,
        deniedTools: $deny
      }
      + (if $want then
           { hooks: { PermissionRequest: [
               { matcher: "Bash",
                 hooks: [{ type: "command", command: $hook }] }
             ] } }
         else {} end)
    ' > "$tmp"
  fi
  # JSON validity sanity check
  jq empty "$tmp" >/dev/null || { rm -f "$tmp"; echo "invalid JSON would be written" >&2; exit 1; }
  mv -f "$tmp" "$SETTINGS"
}

write_snapshot() {
  jq -n --argjson tools "$shared_arr" '{ allowedTools: $tools, _synced_at: now | todate }' > "$SNAPSHOT"
}

install_hook() {
  [[ "$INSTALL_HOOK" == true ]] || return 0
  mkdir -p "$HOOK_DIR"
  install -m 755 "$HOOK_SRC" "$HOOK_DST"
}

if [[ "$DRY_RUN" == true ]]; then
  log "${D}(dry-run) would write $SETTINGS, $SNAPSHOT${R}"
  [[ "$INSTALL_HOOK" == true ]] && log "${D}(dry-run) would install $HOOK_DST${R}"
  exit 0
fi

write_settings
write_snapshot
install_hook
log "${G}wrote${R}    $SETTINGS"
log "${G}snapshot${R} $SNAPSHOT"
if [[ "$INSTALL_HOOK" == true ]]; then
  log "${G}hook${R}     $HOOK_DST"
else
  log "${D}hook     not installed (pass --with-hook; it records command text)${R}"
fi

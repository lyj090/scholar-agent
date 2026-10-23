#!/usr/bin/env bash
set -euo pipefail
# ═══════════════════════════════════════════════════════════════════════════════
# design-session.sh — Design iteration without chat bloat
# ═══════════════════════════════════════════════════════════════════════════════
# Agent writes design documents to .design/<name>/ instead of outputting to chat.
# Chat only sees compact summaries and diffs.
#
# Usage:
#   design-session.sh <subcommand> <session_name> [args...]
#
# Subcommands:
#   init      — Create .design/<name>/ skeleton
#   draft     — Write/overwrite draft from stdin, output diff summary
#   update    — Apply patch/section update from stdin
#   diff      — Show compact diff of last change
#   decisions — Record a confirmed decision point
#   finalize  — Mark session complete, output final stats
#   list      — List active design sessions
#   show      — Print current draft path (for agent to Read)
# ═══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Helpers ─────────────────────────────────────────────────────────────────

resolve_design_root() {
  local hint="${1:-.}"
  local git_root
  git_root=$(cd "$hint" && git rev-parse --show-toplevel 2>/dev/null) || true
  local base
  if [[ -n "$git_root" ]]; then
    base="$git_root"
  else
    base="$(cd "$hint" && pwd)"
  fi

  # New unified path: .ai/design/
  local new_root="$base/.ai/design"
  local old_root="$base/.design"
  if [[ -d "$old_root" && ! -d "$new_root" && ! -L "$old_root" ]]; then
    echo "$old_root"
  else
    echo "$new_root"
  fi
}

iso_now() {
  date -Iseconds 2>/dev/null || date "+%Y-%m-%dT%H:%M:%S%z"
}

# ─── cmd_init ────────────────────────────────────────────────────────────────

cmd_init() {
  local name="${1:?Usage: design-session.sh init <name>}"
  local root
  root=$(resolve_design_root ".")
  local session_dir="$root/$name"

  if [[ -d "$session_dir" ]]; then
    echo "EXISTS path=$session_dir"
    return 0
  fi

  mkdir -p "$session_dir"
  cat > "$session_dir/meta.yaml" <<EOF
name: "$name"
created: "$(iso_now)"
status: active
versions: 0
decisions: []
EOF
  touch "$session_dir/draft.md"
  touch "$session_dir/decisions.yaml"

  echo "CREATED path=$session_dir"
}

# ─── cmd_draft ───────────────────────────────────────────────────────────────

cmd_draft() {
  local name="${1:?Usage: design-session.sh draft <name> < content}"
  local root
  root=$(resolve_design_root ".")
  local session_dir="$root/$name"
  [[ ! -d "$session_dir" ]] && { echo "ERR session not found: $name" >&2; exit 1; }

  local draft="$session_dir/draft.md"
  local prev_version=""

  # Archive previous version if exists
  if [[ -s "$draft" ]]; then
    local version
    version=$(grep "^versions:" "$session_dir/meta.yaml" | awk '{print $2}')
    version=$((version + 1))
    cp "$draft" "$session_dir/draft-v${version}.md"
    prev_version="$session_dir/draft-v${version}.md"
    sed -i "s/^versions: .*/versions: $version/" "$session_dir/meta.yaml"
  fi

  # Write new draft from stdin
  cat > "$draft"

  # Output compact diff summary
  local lines added
  lines=$(wc -l < "$draft")
  if [[ -n "$prev_version" ]]; then
    added=$(diff "$prev_version" "$draft" 2>/dev/null | grep "^>" | wc -l || echo 0)
    local removed
    removed=$(diff "$prev_version" "$draft" 2>/dev/null | grep "^<" | wc -l || echo 0)
    echo "DRAFT path=$draft lines=$lines +$added -$removed"
  else
    sed -i "s/^versions: .*/versions: 1/" "$session_dir/meta.yaml"
    echo "DRAFT path=$draft lines=$lines (new)"
  fi
}

# ─── cmd_update ──────────────────────────────────────────────────────────────

cmd_update() {
  local name="${1:?Usage: design-session.sh update <name> --section <heading> < content}"
  shift
  local section=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --section) section="$2"; shift 2 ;;
      *) shift ;;
    esac
  done

  local root
  root=$(resolve_design_root ".")
  local session_dir="$root/$name"
  local draft="$session_dir/draft.md"
  [[ ! -f "$draft" ]] && { echo "ERR no draft found" >&2; exit 1; }

  if [[ -n "$section" ]]; then
    # Append to specific section (after the heading)
    local content
    content=$(cat)
    local tmp="${draft}.tmp.$$"
    awk -v sec="$section" -v new_content="$content" '
      $0 ~ "^##+ " sec { print; print new_content; found=1; next }
      { print }
    ' "$draft" > "$tmp"
    mv "$tmp" "$draft"
    echo "UPDATED section=\"$section\" in $draft"
  else
    # Append to end
    cat >> "$draft"
    echo "APPENDED to $draft"
  fi
}

# ─── cmd_diff ────────────────────────────────────────────────────────────────

cmd_diff() {
  local name="${1:?Usage: design-session.sh diff <name>}"
  local root
  root=$(resolve_design_root ".")
  local session_dir="$root/$name"
  local draft="$session_dir/draft.md"

  # Find latest archived version
  local latest_archive
  latest_archive=$(ls -t "$session_dir"/draft-v*.md 2>/dev/null | head -1)

  if [[ -z "$latest_archive" ]]; then
    echo "NO_PREV (first draft)"
    wc -l < "$draft" | xargs -I{} echo "CURRENT lines={}"
    return 0
  fi

  # Compact diff (stat only)
  local stat
  stat=$(diff --stat "$latest_archive" "$draft" 2>/dev/null | tail -1)
  echo "DIFF vs $(basename "$latest_archive"): $stat"

  # Show first 10 changed lines
  diff "$latest_archive" "$draft" 2>/dev/null | grep "^[<>]" | head -10 | sed 's/^/  /'
}

# ─── cmd_decisions ───────────────────────────────────────────────────────────

cmd_decisions() {
  local name="${1:?Usage: design-session.sh decisions <name> < decision_text}"
  local root
  root=$(resolve_design_root ".")
  local session_dir="$root/$name"
  local decisions_file="$session_dir/decisions.yaml"

  local decision
  decision=$(cat)
  local now
  now=$(iso_now)

  cat >> "$decisions_file" <<EOF
- timestamp: "$now"
  decision: "$decision"
EOF

  local count
  count=$(grep -c "^-" "$decisions_file" 2>/dev/null || echo 0)
  echo "OK decisions=$count"
}

# ─── cmd_finalize ────────────────────────────────────────────────────────────

cmd_finalize() {
  local name="${1:?Usage: design-session.sh finalize <name>}"
  local root
  root=$(resolve_design_root ".")
  local session_dir="$root/$name"

  sed -i "s/^status: .*/status: finalized/" "$session_dir/meta.yaml"

  local lines versions decisions
  lines=$(wc -l < "$session_dir/draft.md")
  versions=$(grep "^versions:" "$session_dir/meta.yaml" | awk '{print $2}')
  decisions=$(grep -c "^-" "$session_dir/decisions.yaml" 2>/dev/null || echo 0)

  echo "FINALIZED name=$name lines=$lines versions=$versions decisions=$decisions"
}

# ─── cmd_list ────────────────────────────────────────────────────────────────

cmd_list() {
  local root
  root=$(resolve_design_root ".")
  [[ ! -d "$root" ]] && { echo "NO_SESSIONS"; return 0; }

  for dir in "$root"/*/; do
    [[ ! -d "$dir" ]] && continue
    local name status lines
    name=$(basename "$dir")
    status=$(grep "^status:" "$dir/meta.yaml" 2>/dev/null | awk '{print $2}' || echo "?")
    lines=$(wc -l < "$dir/draft.md" 2>/dev/null || echo 0)
    printf "%s\t%s\t%s lines\n" "$name" "$status" "$lines"
  done
}

# ─── cmd_show ────────────────────────────────────────────────────────────────

cmd_show() {
  local name="${1:?Usage: design-session.sh show <name>}"
  local root
  root=$(resolve_design_root ".")
  local draft="$root/$name/draft.md"
  [[ ! -f "$draft" ]] && { echo "ERR not found" >&2; exit 1; }
  echo "PATH=$draft"
}

# ─── cmd_gc ──────────────────────────────────────────────────────────────────

cmd_gc() {
  local max_age=30
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --max-age) max_age="$2"; shift 2 ;;
      *) shift ;;
    esac
  done

  local root
  root=$(resolve_design_root ".")
  [[ ! -d "$root" ]] && { echo "NO design sessions"; return 0; }

  local removed=0 freed=0
  for dir in "$root"/*/; do
    [[ ! -d "$dir" ]] && continue
    local meta="$dir/meta.yaml"
    [[ ! -f "$meta" ]] && continue

    local status
    status=$(grep "^status:" "$meta" 2>/dev/null | awk '{print $2}')
    [[ "$status" != "finalized" ]] && continue

    local created
    created=$(grep "^created:" "$meta" 2>/dev/null | sed 's/created: *"\{0,1\}\([^"]*\)"\{0,1\}/\1/')
    local created_epoch now_epoch age_days
    created_epoch=$(date -d "$created" +%s 2>/dev/null || echo 0)
    now_epoch=$(date +%s)
    age_days=$(( (now_epoch - created_epoch) / 86400 ))

    if [[ $age_days -gt $max_age ]]; then
      local size
      size=$(du -sk "$dir" 2>/dev/null | awk '{print $1}')
      rm -rf "$dir"
      removed=$((removed + 1))
      freed=$((freed + size))
    fi
  done

  echo "REMOVED=$removed FREED=${freed}KB"
}

# ─── Dispatch ────────────────────────────────────────────────────────────────

case "${1:-}" in
  init)      shift; cmd_init "$@" ;;
  draft)     shift; cmd_draft "$@" ;;
  update)    shift; cmd_update "$@" ;;
  diff)      shift; cmd_diff "$@" ;;
  decisions) shift; cmd_decisions "$@" ;;
  finalize)  shift; cmd_finalize "$@" ;;
  list)      shift; cmd_list "$@" ;;
  show)      shift; cmd_show "$@" ;;
  gc)        shift; cmd_gc "$@" ;;
  -h|--help) echo "Usage: design-session.sh <init|draft|update|diff|decisions|finalize|list|show|gc> <name>" ;;
  *)         echo "Usage: design-session.sh <init|draft|update|diff|decisions|finalize|list|show|gc> <name>" >&2; exit 1 ;;
esac

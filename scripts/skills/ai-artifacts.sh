#!/usr/bin/env bash
set -euo pipefail
# ═══════════════════════════════════════════════════════════════════════════════
# ai-artifacts.sh — Unified .ai/ directory management
# ═══════════════════════════════════════════════════════════════════════════════
# Single entry point for viewing and managing all AI-generated intermediate
# files in a project: research sessions, design drafts, reports.
#
# Usage:
#   ai-artifacts.sh <subcommand> [args...]
#
# Subcommands:
#   status    — Overview of all .ai/ contents (sizes, counts, staleness)
#   gc        — Garbage collect across all sub-directories
#   migrate   — Move legacy .research/ and .design/ into .ai/
#   init      — Create .ai/ skeleton
#   gitignore — Add .ai/ to project .gitignore
#   tree      — Compact tree view of .ai/ structure
# ═══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Helpers ─────────────────────────────────────────────────────────────────

resolve_ai_root() {
  local hint="${1:-.}"
  hint="$(cd "$hint" 2>/dev/null && pwd)" || { echo "ERR path not found: $1" >&2; exit 1; }
  local git_root
  git_root=$(cd "$hint" && git rev-parse --show-toplevel 2>/dev/null) || true
  if [[ -n "$git_root" ]]; then
    echo "$git_root/.ai"
  else
    echo "$hint/.ai"
  fi
}

parse_root_flag() {
  local root=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [[ -n "$root" ]]; then
    resolve_ai_root "$root"
  else
    resolve_ai_root "."
  fi
}

human_size() {
  local bytes=$1
  if [[ $bytes -gt 1048576 ]]; then
    echo "$((bytes / 1048576))MB"
  elif [[ $bytes -gt 1024 ]]; then
    echo "$((bytes / 1024))KB"
  else
    echo "${bytes}B"
  fi
}

# ─── cmd_init ────────────────────────────────────────────────────────────────

cmd_init() {
  local root
  root=$(parse_root_flag "$@")

  mkdir -p "$root/research/sessions"
  mkdir -p "$root/design"
  mkdir -p "$root/reports"

  if [[ ! -f "$root/meta.yaml" ]]; then
    cat > "$root/meta.yaml" <<EOF
# .ai/ directory — AI-generated intermediate artifacts
# Managed by: ai-artifacts.sh, research.sh, design-session.sh
created: "$(date -Iseconds)"
last_gc: null
EOF
  fi

  echo "INIT path=$root"
  echo "  research/  — research (incl. plan mode) sessions"
  echo "  design/    — design iteration drafts"
  echo "  reports/   — generated reports & audits"
}

# ─── cmd_status ──────────────────────────────────────────────────────────────

cmd_status() {
  local root
  root=$(parse_root_flag "$@")

  if [[ ! -d "$root" ]]; then
    echo "NO .ai/ directory found"
    echo "Run: ai-artifacts.sh init [--root PATH]"
    return 0
  fi

  local total_size
  total_size=$(du -sb "$root" 2>/dev/null | awk '{print $1}' || echo 0)

  echo "═══════════════════════════════════════════════════"
  echo " .ai/ Status — $(human_size $total_size) total"
  echo "═══════════════════════════════════════════════════"
  echo ""

  # Research
  local research_dir="$root/research"
  if [[ -d "$research_dir/sessions" ]]; then
    local r_count r_complete r_stale r_size
    r_count=$(find "$research_dir/sessions" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
    r_complete=$(find "$research_dir/sessions" -name "meta.yaml" -exec grep -l "^status: complete" {} \; 2>/dev/null | wc -l)
    r_stale=$(find "$research_dir/sessions" -name "meta.yaml" -exec grep -l "stale: true" {} \; 2>/dev/null | wc -l)
    r_size=$(du -sb "$research_dir" 2>/dev/null | awk '{print $1}' || echo 0)
    echo "  research/   $(human_size $r_size)  sessions=$r_count  complete=$r_complete  stale=$r_stale"
  else
    echo "  research/   (empty)"
  fi

  # Design
  local design_dir="$root/design"
  if [[ -d "$design_dir" ]]; then
    local d_count d_active d_finalized d_size
    d_count=$(find "$design_dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
    d_active=$(find "$design_dir" -name "meta.yaml" -exec grep -l "^status: active" {} \; 2>/dev/null | wc -l)
    d_finalized=$(find "$design_dir" -name "meta.yaml" -exec grep -l "^status: finalized" {} \; 2>/dev/null | wc -l)
    d_size=$(du -sb "$design_dir" 2>/dev/null | awk '{print $1}' || echo 0)
    echo "  design/     $(human_size $d_size)  sessions=$d_count  active=$d_active  finalized=$d_finalized"
  else
    echo "  design/     (empty)"
  fi

  # Reports
  local reports_dir="$root/reports"
  if [[ -d "$reports_dir" ]]; then
    local rp_count rp_size
    rp_count=$(find "$reports_dir" -name "*.md" 2>/dev/null | wc -l || echo 0)
    rp_size=$(du -sb "$reports_dir" 2>/dev/null | awk '{print $1}' || echo 0)
    echo "  reports/    $(human_size $rp_size)  files=$rp_count"
  else
    echo "  reports/    (empty)"
  fi

  # Archive
  if [[ -d "$root/research/.archive" ]]; then
    local a_size
    a_size=$(du -sb "$root/research/.archive" 2>/dev/null | awk '{print $1}' || echo 0)
    echo "  .archive/   $(human_size $a_size)"
  fi

  echo ""
  echo "═══════════════════════════════════════════════════"
}

# ─── cmd_gc ──────────────────────────────────────────────────────────────────

cmd_gc() {
  local root max_age=30
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root="$2"; shift 2 ;;
      --max-age) max_age="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  root=$(resolve_ai_root "${root:-.}")

  [[ ! -d "$root" ]] && { echo "NO .ai/ directory"; return 0; }

  local total_archived=0 total_freed=0

  # GC research (delegate to research.sh)
  if [[ -d "$root/research/sessions" ]]; then
    local r_result
    r_result=$(bash "$SCRIPT_DIR/research.sh" gc --max-age "$max_age" --root "$(dirname "$root")" 2>&1)
    echo "  research: $r_result"
    local r_freed
    r_freed=$(echo "$r_result" | grep -oP 'FREED=\K[0-9]+' || echo 0)
    total_freed=$((total_freed + r_freed))
  fi

  # GC design (finalized + older than max_age days)
  if [[ -d "$root/design" ]]; then
    local d_archived=0
    for dir in "$root/design"/*/; do
      [[ ! -d "$dir" ]] && continue
      local meta="$dir/meta.yaml"
      [[ ! -f "$meta" ]] && continue

      local status created
      status=$(grep "^status:" "$meta" 2>/dev/null | awk '{print $2}')
      [[ "$status" != "finalized" ]] && continue

      created=$(grep "^created:" "$meta" 2>/dev/null | sed 's/created: *"\{0,1\}\([^"]*\)"\{0,1\}/\1/')
      local created_epoch now_epoch age_days
      created_epoch=$(date -d "$created" +%s 2>/dev/null || echo 0)
      now_epoch=$(date +%s)
      age_days=$(( (now_epoch - created_epoch) / 86400 ))

      if [[ $age_days -gt $max_age ]]; then
        local size
        size=$(du -sk "$dir" 2>/dev/null | awk '{print $1}')
        rm -rf "$dir"
        d_archived=$((d_archived + 1))
        total_freed=$((total_freed + size))
      fi
    done
    echo "  design:   REMOVED=$d_archived finalized sessions older than ${max_age}d"
  fi

  # GC reports (older than max_age × 2 days)
  if [[ -d "$root/reports" ]]; then
    local rp_removed=0
    find "$root/reports" -name "*.md" -mtime "+$((max_age * 2))" 2>/dev/null | while read -r f; do
      rm -f "$f"
      rp_removed=$((rp_removed + 1))
    done
    echo "  reports:  REMOVED=$rp_removed files older than $((max_age * 2))d"
  fi

  # Update meta
  sed -i "s/^last_gc: .*/last_gc: \"$(date -Iseconds)\"/" "$root/meta.yaml" 2>/dev/null || true

  echo ""
  echo "TOTAL FREED=${total_freed}KB"
}

# ─── cmd_migrate ─────────────────────────────────────────────────────────────

cmd_migrate() {
  local root
  root=$(parse_root_flag "$@")
  local project_root="${root%/.ai}"

  local migrated=0

  # Migrate .research/ → .ai/research/
  if [[ -d "$project_root/.research" && ! -L "$project_root/.research" ]]; then
    mkdir -p "$root"
    if [[ -d "$root/research" ]]; then
      # Merge: move sessions from old to new
      if [[ -d "$project_root/.research/sessions" ]]; then
        mkdir -p "$root/research/sessions"
        cp -rn "$project_root/.research/sessions"/* "$root/research/sessions/" 2>/dev/null || true
      fi
      if [[ -f "$project_root/.research/index.yaml" && ! -f "$root/research/index.yaml" ]]; then
        cp "$project_root/.research/index.yaml" "$root/research/"
      fi
    else
      mv "$project_root/.research" "$root/research"
    fi
    # Leave symlink for backward compat
    ln -sf "$root/research" "$project_root/.research"
    echo "MIGRATED .research/ → .ai/research/ (symlink left)"
    migrated=$((migrated + 1))
  fi

  # Migrate .design/ → .ai/design/
  if [[ -d "$project_root/.design" && ! -L "$project_root/.design" ]]; then
    mkdir -p "$root"
    if [[ -d "$root/design" ]]; then
      cp -rn "$project_root/.design"/* "$root/design/" 2>/dev/null || true
    else
      mv "$project_root/.design" "$root/design"
    fi
    ln -sf "$root/design" "$project_root/.design"
    echo "MIGRATED .design/ → .ai/design/ (symlink left)"
    migrated=$((migrated + 1))
  fi

  # Migrate .reports/ → .ai/reports/
  if [[ -d "$project_root/.reports" && ! -L "$project_root/.reports" ]]; then
    mkdir -p "$root"
    if [[ -d "$root/reports" ]]; then
      cp -rn "$project_root/.reports"/* "$root/reports/" 2>/dev/null || true
    else
      mv "$project_root/.reports" "$root/reports"
    fi
    ln -sf "$root/reports" "$project_root/.reports"
    echo "MIGRATED .reports/ → .ai/reports/ (symlink left)"
    migrated=$((migrated + 1))
  fi

  if [[ $migrated -eq 0 ]]; then
    echo "NOTHING to migrate (no legacy .research/, .design/, or .reports/ found)"
  else
    echo "TOTAL migrated=$migrated directories"
    # Init meta if missing
    [[ ! -f "$root/meta.yaml" ]] && cmd_init --root "$(dirname "$root")" > /dev/null
  fi
}

# ─── cmd_gitignore ───────────────────────────────────────────────────────────

cmd_gitignore() {
  local root
  root=$(parse_root_flag "$@")
  local project_root="${root%/.ai}"
  local gi="$project_root/.gitignore"

  if [[ -f "$gi" ]] && grep -q "^\.ai/" "$gi" 2>/dev/null; then
    echo "ALREADY in .gitignore"
    return 0
  fi

  cat >> "$gi" <<'EOF'

# AI-generated intermediate artifacts (managed by ai-artifacts.sh)
.ai/
EOF
  echo "ADDED .ai/ to $gi"
}

# ─── cmd_tree ────────────────────────────────────────────────────────────────

cmd_tree() {
  local root
  root=$(parse_root_flag "$@")

  if [[ ! -d "$root" ]]; then
    echo "NO .ai/ directory"
    return 0
  fi

  echo ".ai/"

  # Research sessions
  if [[ -d "$root/research/sessions" ]]; then
    local count
    count=$(ls -d "$root/research/sessions"/*/ 2>/dev/null | wc -l || echo 0)
    echo "├── research/ ($count sessions)"
    ls -d "$root/research/sessions"/*/ 2>/dev/null | tail -5 | while read -r d; do
      local name
      name=$(basename "$d")
      local status
      status=$(grep "^status:" "$d/meta.yaml" 2>/dev/null | awk '{print $2}' || echo "?")
      echo "│   ├── $name [$status]"
    done
    [[ $count -gt 5 ]] && echo "│   └── ... ($((count - 5)) more)"
  else
    echo "├── research/ (empty)"
  fi

  # Design sessions
  if [[ -d "$root/design" ]]; then
    local count
    count=$(ls -d "$root/design"/*/ 2>/dev/null | wc -l || echo 0)
    echo "├── design/ ($count sessions)"
    ls -d "$root/design"/*/ 2>/dev/null | while read -r d; do
      local name status
      name=$(basename "$d")
      status=$(grep "^status:" "$d/meta.yaml" 2>/dev/null | awk '{print $2}' || echo "?")
      echo "│   ├── $name [$status]"
    done
  else
    echo "├── design/ (empty)"
  fi

  # Reports
  if [[ -d "$root/reports" ]]; then
    local count
    count=$(find "$root/reports" -name "*.md" 2>/dev/null | wc -l || echo 0)
    echo "└── reports/ ($count files)"
    find "$root/reports" -name "*.md" 2>/dev/null | tail -5 | while read -r f; do
      echo "    ├── $(basename "$f")"
    done
  else
    echo "└── reports/ (empty)"
  fi
}

# ─── Dispatch ────────────────────────────────────────────────────────────────

case "${1:-}" in
  init)      shift; cmd_init "$@" ;;
  status)    shift; cmd_status "$@" ;;
  gc)        shift; cmd_gc "$@" ;;
  migrate)   shift; cmd_migrate "$@" ;;
  gitignore) shift; cmd_gitignore "$@" ;;
  tree)      shift; cmd_tree "$@" ;;
  -h|--help)
    echo "Usage: ai-artifacts.sh <init|status|gc|migrate|gitignore|tree> [--root PATH]"
    echo ""
    echo "Manages the unified .ai/ directory for AI-generated intermediate artifacts."
    echo "Sub-directories: research/ (trace/plan sessions), design/ (drafts), reports/"
    ;;
  *)
    echo "Usage: ai-artifacts.sh <init|status|gc|migrate|gitignore|tree> [--root PATH]" >&2
    exit 1
    ;;
esac

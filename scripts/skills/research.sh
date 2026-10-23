#!/usr/bin/env bash
set -euo pipefail
# ═══════════════════════════════════════════════════════════════════════════════
# research.sh — .ai/research/ session lifecycle manager
# ═══════════════════════════════════════════════════════════════════════════════
# Agents MUST NOT read/write .ai/research/ files directly; all I/O goes here.
#
# Layout (single source of truth = sessions/<SID>/meta.yaml):
#   <repo>/.ai/research/
#     sessions/<SID>/meta.yaml    status, layers_completed, files
#     sessions/<SID>/chain.md     layered findings + final report (markdown)
#     .archive/                   gc destination
#
# Usage: research.sh <subcommand> [args...]
#   gate, query, start, append, save-report, complete, read-chain,
#   list, search-all, check-stale, gc
#
# Schema: shared/protocols/trace-artifacts-schema.md
# ═══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_PATH_INDEX="$SCRIPT_DIR/../../shared/repo-path-index.local.yaml"
AI_WORKFLOWS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ─── Helpers ─────────────────────────────────────────────────────────────────

resolve_root() {
  local hint="${1:-.}" base git_root
  hint="$(cd "$hint" 2>/dev/null && pwd)" || { echo "ERR path not found: $1" >&2; exit 1; }
  git_root=$(cd "$hint" && git rev-parse --show-toplevel 2>/dev/null) || true
  base="${git_root:-$hint}"
  echo "$base/.ai/research"
}

parse_root_flag() {
  local root=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  resolve_root "${root:-.}"
}

# Extract a single-line scalar from a meta.yaml. Only ^-anchored keys.
# Always exits 0 (missing key → empty string) so callers under set -e survive.
meta_field() {
  { grep "^$2:" "$1" 2>/dev/null | head -1 \
      | sed "s/^$2: *//; s/^'\{1,2\}//; s/'\{1,2\}$//; s/^\"\{1,2\}//; s/\"\{1,2\}$//"; } || true
}

slugify() {
  echo "$1" | sed 's|[/ _]|-|g; s|[^a-zA-Z0-9._-]||g' | cut -c1-40
}

iso_now() { date -Iseconds 2>/dev/null || date "+%Y-%m-%dT%H:%M:%S%z"; }
date_prefix() { date "+%Y-%m-%d"; }

compute_age_days() {
  local created_epoch now_epoch
  created_epoch=$(date -d "$1" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%S" "$1" +%s 2>/dev/null || echo 0)
  now_epoch=$(date +%s)
  echo $(( (now_epoch - created_epoch) / 86400 ))
}

ensure_dir() { [[ -d "$1" ]] || mkdir -p "$1"; }

auto_register_project() {
  local project_root="$1"
  [[ ! -f "$REPO_PATH_INDEX" ]] && return 0
  local project_name
  project_name=$(basename "$project_root")
  grep -q "^${project_name}:" "$REPO_PATH_INDEX" 2>/dev/null && return 0
  echo "${project_name}: ${project_root}" >> "$REPO_PATH_INDEX"
  echo "REGISTERED ${project_name} in repo-path-index" >&2
}

project_root_of() {  # .ai/research → project root
  echo "${1%/research}" | sed 's|/\.ai$||'
}

# Staleness: git repo → any commit touching tracked files since creation.
# Non-git → any tracked file deleted. Writes nothing; caller persists.
is_stale() {
  local session_dir="$1"
  local meta="$session_dir/meta.yaml"
  [[ ! -f "$meta" ]] && return 1
  local created files
  created=$(meta_field "$meta" created)
  [[ -z "$created" ]] && return 1
  files=$(python3 -c "import sys,yaml;print('\n'.join(yaml.safe_load(open(sys.argv[1])).get('files') or []))" "$meta" 2>/dev/null)
  [[ -z "$files" ]] && return 1
  local proot
  proot=$(project_root_of "$(dirname "$(dirname "$session_dir")")")
  if (cd "$proot" && git rev-parse --git-dir &>/dev/null); then
    local hits=0
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      [[ ! -f "$proot/$f" ]] && return 0  # deleted → stale
      if (cd "$proot" && git log --oneline --since="$created" -- "$f" 2>/dev/null | grep -q .); then
        hits=1
      fi
    done <<< "$files"
    [[ $hits -eq 1 ]] && return 0
  else
    while IFS= read -r f; do
      [[ -n "$f" && ! -f "$proot/$f" ]] && return 0
    done <<< "$files"
  fi
  return 1
}

# ─── cmd_gate ────────────────────────────────────────────────────────────────

detect_ros2_repo() {
  local root="$1" force="${2:-false}"
  [[ "$force" == true ]] && return 0
  if find "$root" -name colcon.meta -print -quit 2>/dev/null | grep -q .; then return 0; fi
  local pkg
  while IFS= read -r -d '' pkg; do
    grep -qE 'rclcpp|rclpy|ament' "$pkg" 2>/dev/null && return 0
  done < <(find "$root" -name package.xml -print0 2>/dev/null)
  return 1
}

emit_spec_read() {
  local abs="$AI_WORKFLOWS_ROOT/$1"
  if [[ -f "$abs" ]]; then
    echo "READ=$abs"
  else
    echo "ERR spec_missing path=$abs"
    echo "MSG=sync scholar-agent shared specs"
    exit 1
  fi
}

# Surface the local paper corpus at planning time. Design and review work often
# needs literature that already exists in the MinerU cache; without this hint an
# agent has no way to know the corpus exists and falls back to fetching arXiv.
#
# Computed into PAPER_CORPUS_HINT *before* any gate output is written, then
# emitted in the same burst as the other lines. Doing the scan inline would
# introduce a pause between writes, so a consumer that exits early (`| grep -q`)
# could close the pipe mid-sequence and make the gate die with SIGPIPE (141)
# under `set -o pipefail`. Silent when the corpus is unconfigured or empty.
compute_paper_corpus_hint() {
  PAPER_CORPUS_HINT=""
  local paths_file="$AI_WORKFLOWS_ROOT/shared/paths.local.yaml"
  [[ -f "$paths_file" ]] || return 0
  local cache_dir
  cache_dir=$(sed -n 's/^[[:space:]]*mineru_cache:[[:space:]]*//p' "$paths_file" | head -1 | tr -d '"')
  [[ -n "$cache_dir" ]] || return 0
  cache_dir="${cache_dir/#\~/$HOME}"
  [[ -d "$cache_dir" ]] || return 0
  local count
  count=$(find "$cache_dir" -maxdepth 2 -name full.md 2>/dev/null | wc -l | tr -d ' ')
  [[ "$count" -gt 0 ]] || return 0
  PAPER_CORPUS_HINT="PAPERS=$count local MinerU caches
PAPERS_CMD=python3 ~/scholar-agent/scripts/literature/zotero_cli.py paper-status <ref>"
}

emit_paper_corpus_hint() {
  [[ -n "${PAPER_CORPUS_HINT:-}" ]] && printf '%s\n' "$PAPER_CORPUS_HINT"
  return 0
}

cmd_gate() {
  local intent="research" root_hint="." force_trace=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --intent) intent="$2"; shift 2 ;;
      --root) root_hint="$2"; shift 2 ;;
      --force-trace) force_trace=true; shift ;;
      *) echo "ERR unknown_arg arg=$1"; exit 1 ;;
    esac
  done
  [[ "$intent" != "research" && "$intent" != "trace" ]] && { echo "ERR bad_intent intent=$intent"; exit 1; }

  # Precompute before emitting, so the corpus scan cannot interleave with
  # output writes (see compute_paper_corpus_hint).
  compute_paper_corpus_hint

  local research_root project_root
  research_root=$(resolve_root "$root_hint")
  project_root=$(project_root_of "$research_root")

  if [[ "$intent" == "trace" ]] && ! detect_ros2_repo "$project_root" "$force_trace"; then
    echo "ERR not_ros2"
    echo "FALLBACK intent=research"
    emit_spec_read "shared/specs/research-ai.md"
    echo "MSG=non-ROS2: use research or grep not trace"
    return 0
  fi

  if [[ "$intent" == "trace" ]]; then
    echo "OK intent=trace ros2=true"
    emit_spec_read "shared/specs/research-ai.md"
    emit_spec_read "shared/specs/research-trace-core.md"
    local report_abs="$AI_WORKFLOWS_ROOT/shared/specs/research-trace-report.md"
    [[ -f "$report_abs" ]] && echo "READ_AT_L4=$report_abs" || {
      echo "ERR spec_missing path=$report_abs"; echo "MSG=Missing shared spec; sync scholar-agent repo"; exit 1; }
    echo "TOOL_L4=bash ~/scholar-agent/scripts/skills/trace-ros-topology.sh"
  else
    local ros2_flag=false
    detect_ros2_repo "$project_root" false && ros2_flag=true
    echo "OK intent=research ros2=$ros2_flag"
    emit_spec_read "shared/specs/research-ai.md"
  fi
  emit_paper_corpus_hint
}

# ─── cmd_query ───────────────────────────────────────────────────────────────

cmd_query() {
  local keyword="${1:?Usage: research.sh query <keyword> [--root PATH]}"
  shift
  local root
  root=$(parse_root_flag "$@")

  local best_id="" best_status="" best_stale="" best_created="" best_source="" best_priority=0
  local shopt_saved
  shopt_saved=$(shopt -p nullglob) && shopt -s nullglob

  for meta in "$root"/sessions/*/meta.yaml; do
    local id target status stale created source priority=0
    id=$(meta_field "$meta" id)
    target=$(meta_field "$meta" target)
    [[ -z "$id" || -z "$target" ]] && continue
    status=$(meta_field "$meta" status)
    stale=$(meta_field "$meta" stale)
    created=$(meta_field "$meta" created)
    source=$(meta_field "$meta" source)
    if [[ "$target" == "$keyword" ]]; then priority=3
    elif [[ "${target,,}" == "${keyword,,}" ]]; then priority=2
    elif echo "$target" | grep -qi "$keyword"; then priority=1
    fi
    if [[ $priority -gt 0 && $priority -ge $best_priority ]]; then
      best_id="$id" best_status="$status" best_stale="$stale"
      best_created="$created" best_source="$source" best_priority=$priority
    fi
  done
  eval "$shopt_saved"

  [[ -z "$best_id" ]] && { echo "MISS"; return 0; }

  # Re-verify staleness on fresh sessions; persist the flip in meta.
  local session_dir="$root/sessions/$best_id"
  if [[ "$best_stale" == "false" ]] && is_stale "$session_dir"; then
    best_stale="true"
    sed -i "s/^stale: .*/stale: true/" "$session_dir/meta.yaml" 2>/dev/null || true
  fi

  local age=0
  [[ -n "$best_created" ]] && age=$(compute_age_days "$best_created")
  echo "HIT session=$best_id status=$best_status stale=$best_stale age=${age}d source=$best_source"

  local files
  files=$(python3 -c "import sys,yaml;print(' '.join((yaml.safe_load(open(sys.argv[1])).get('files') or [])[:5]))" "$session_dir/meta.yaml" 2>/dev/null)
  [[ -n "$files" ]] && echo "FILES $files"

  if [[ "$best_status" == "incomplete" ]]; then
    local layers max_layer
    layers=$(python3 -c "import sys,yaml;print(','.join(map(str,yaml.safe_load(open(sys.argv[1])).get('layers_completed') or [])))" "$session_dir/meta.yaml" 2>/dev/null) || true
    if [[ -n "$layers" ]]; then
      max_layer=$(echo "$layers" | tr ',' '\n' | sort -n | tail -1)
      echo "RESUME_FROM=L$((max_layer + 1))"
    else
      echo "RESUME_FROM=L1"
    fi
  elif [[ "$best_stale" == "true" ]]; then
    echo "HINT use_as_reference"
  elif [[ "$best_status" == "complete" ]]; then
    echo "RESUME_FROM=done CHAIN=$session_dir/chain.md"
  fi
}

# ─── cmd_start ───────────────────────────────────────────────────────────────

cmd_start() {
  local target="${1:?Usage: research.sh start <target> --type T --source S [--root PATH]}"
  shift
  local type="" source="" root_flag=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --type) type="$2"; shift 2 ;;
      --source) source="$2"; shift 2 ;;
      --root) root_flag="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  [[ -z "$type" ]] && { echo "ERR --type required" >&2; exit 1; }
  [[ -z "$source" ]] && { echo "ERR --source required" >&2; exit 1; }

  local root
  root=$(resolve_root "${root_flag:-.}")
  ensure_dir "$root/sessions"

  local slug session_id counter=2
  slug=$(slugify "$target")
  session_id="$(date_prefix)_${slug}"
  while [[ -d "$root/sessions/$session_id" ]]; do
    session_id="$(date_prefix)_${slug}_${counter}"
    counter=$((counter + 1))
  done

  local session_dir="$root/sessions/$session_id"
  mkdir -p "$session_dir"

  local now git_ref proot
  now=$(iso_now)
  proot=$(project_root_of "$root")
  git_ref=$(cd "$proot" && git rev-parse --short HEAD 2>/dev/null || echo "null")

  cat > "$session_dir/meta.yaml" <<EOF
id: "$session_id"
target: "$target"
type: $type
source: $source
created: "$now"
completed: null
status: incomplete
stale: false
root: "$proot"
git_ref: "$git_ref"
layers_completed: []
files: []
EOF

  cat > "$session_dir/chain.md" <<EOF
# $target

- session: $session_id
- created: $now
- type: $type
EOF

  auto_register_project "$proot"
  echo "SESSION=$session_id PATH=$session_dir"
}

# ─── cmd_append ──────────────────────────────────────────────────────────────

cmd_append() {
  local session_id="${1:?Usage: research.sh append <SID> --layer N [--root PATH]}"
  shift
  local layer="" root_flag=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --layer) layer="$2"; shift 2 ;;
      --root) root_flag="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  [[ -z "$layer" ]] && { echo "ERR --layer required" >&2; exit 1; }

  local root
  root=$(resolve_root "${root_flag:-.}")
  local session_dir="$root/sessions/$session_id"
  [[ ! -d "$session_dir" ]] && { echo "ERR session not found: $session_id" >&2; exit 1; }

  local json
  json=$(cat)
  echo "$json" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null \
    || { echo "ERR invalid JSON on stdin" >&2; exit 1; }

  # Extract fields and render as markdown; flatten embedded newlines.
  local block
  block=$(python3 -c "
import sys, json
d = json.load(sys.stdin)
def flat(s): return ' '.join(str(s).split())
print()
print('## L%s — %s' % (d.get('layer', '$layer'), flat(d.get('action', ''))))
files = d.get('files') or []
if files: print('- files: ' + ', '.join(files))
if d.get('insight'): print('- insight: ' + flat(d['insight']))
if d.get('summary'): print('- summary: ' + flat(d['summary']))
" <<< "$json")
  printf '%s\n' "$block" >> "$session_dir/chain.md"

  # Update meta: layers_completed + merged file list (single source of truth)
  python3 -c '
import sys, yaml, json
meta_path, layer = sys.argv[1], int(sys.argv[2])
payload = json.loads(sys.argv[3])
meta = yaml.safe_load(open(meta_path)) or {}
done = meta.get("layers_completed") or []
if layer not in done:
    done.append(layer)
meta["layers_completed"] = sorted(done)
files = meta.get("files") or []
for f in (payload.get("files") or []):
    if f not in files:
        files.append(f)
meta["files"] = files
yaml.safe_dump(meta, open(meta_path, "w"), sort_keys=False, allow_unicode=True)
' "$session_dir/meta.yaml" "$layer" "$json"

  echo "OK layers=[$(python3 -c "import sys,yaml;print(','.join(map(str,yaml.safe_load(open(sys.argv[1])).get('layers_completed') or [])))" "$session_dir/meta.yaml")]"
}

# ─── cmd_save_report ─────────────────────────────────────────────────────────

cmd_save_report() {
  local session_id="${1:?Usage: research.sh save-report <SID> [--root PATH]}"
  shift
  local root_flag=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root_flag="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  local root
  root=$(resolve_root "${root_flag:-.}")
  local session_dir="$root/sessions/$session_id"
  [[ ! -d "$session_dir" ]] && { echo "ERR session not found: $session_id" >&2; exit 1; }
  {
    echo ""
    echo "## Report"
    echo ""
    cat
  } >> "$session_dir/chain.md"
  echo "OK file=chain.md"
}

# ─── cmd_complete ────────────────────────────────────────────────────────────

cmd_complete() {
  local session_id="${1:?Usage: research.sh complete <SID> [--root PATH]}"
  shift
  local root_flag=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root_flag="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  local root
  root=$(resolve_root "${root_flag:-.}")
  local meta="$root/sessions/$session_id/meta.yaml"
  [[ ! -f "$meta" ]] && { echo "ERR session not found: $session_id" >&2; exit 1; }
  local now
  now=$(iso_now)
  sed -i "s/^status: .*/status: complete/" "$meta"
  sed -i "s/^completed: .*/completed: \"$now\"/" "$meta"
  echo "OK status=complete"
}

# ─── cmd_read_chain ──────────────────────────────────────────────────────────

cmd_read_chain() {
  local session_id="${1:?Usage: research.sh read-chain <SID> [--summary|--full] [--project NAME|--root PATH]}"
  shift
  local root="" project="" mode="path"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root="$2"; shift 2 ;;
      --project) project="$2"; shift 2 ;;
      --summary) mode="summary"; shift ;;
      --full) mode="full"; shift ;;
      *) shift ;;
    esac
  done
  if [[ -n "$project" && -z "$root" ]]; then
    [[ ! -f "$REPO_PATH_INDEX" ]] && { echo "ERR no repo-path-index" >&2; exit 1; }
    root=$(grep "^${project}:" "$REPO_PATH_INDEX" | head -1 | sed 's/[^:]*: *//' | sed "s|^~|$HOME|")
    [[ -z "$root" ]] && { echo "ERR project '$project' not found in repo-path-index" >&2; exit 1; }
  fi
  local chain
  chain="$(resolve_root "${root:-.}")/sessions/$session_id/chain.md"
  [[ ! -f "$chain" ]] && { echo "ERR chain.md not found for session $session_id" >&2; exit 1; }

  case "$mode" in
    summary)
      echo "CHAIN=$chain"
      grep '^## L' "$chain" | head -20 || true
      ;;
    full)
      echo "CHAIN=$chain"
      cat "$chain"
      ;;
    path)
      echo "CHAIN=$chain"
      ;;
  esac
}

# ─── cmd_list ────────────────────────────────────────────────────────────────

list_in_root() {
  local root="$1" project_name="$2"
  local shopt_saved
  shopt_saved=$(shopt -p nullglob) && shopt -s nullglob
  for meta in "$root"/sessions/*/meta.yaml; do
    local id status stale created source age
    id=$(meta_field "$meta" id)
    status=$(meta_field "$meta" status)
    [[ -z "$id" || -z "$status" ]] && continue
    stale=$(meta_field "$meta" stale)
    created=$(meta_field "$meta" created)
    source=$(meta_field "$meta" source)
    age=0; [[ -n "$created" ]] && age=$(compute_age_days "$created")
    printf "%s\t%s\t%s\t%s\t%s\t%sd\n" \
      "$project_name" "$id" "${source:-?}" "$status" \
      "$([[ "$stale" == "true" ]] && echo stale || echo fresh)" "$age"
  done
  eval "$shopt_saved"
}

cmd_list() {
  local root_flag="" all=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root_flag="$2"; shift 2 ;;
      --all) all=true; shift ;;
      *) shift ;;
    esac
  done
  if [[ "$all" == "true" ]]; then
    [[ ! -f "$REPO_PATH_INDEX" ]] && { echo "ERR no repo-path-index" >&2; exit 1; }
    local line name path
    while IFS= read -r line; do
      [[ -z "$line" || "$line" == "#"* ]] && continue
      name="${line%%:*}"
      path="${line#*:}"
      path=$(echo "$path" | sed 's/^ *//;s/ *$//' | sed "s|^~|$HOME|")
      [[ -d "$path" ]] && list_in_root "$(resolve_root "$path")" "$name"
    done < "$REPO_PATH_INDEX"
  else
    list_in_root "$(resolve_root "$root_flag")" "local"
  fi
}

# ─── cmd_search_all ──────────────────────────────────────────────────────────

cmd_search_all() {
  local keyword="" since="" source_filter=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --since) since="$2"; shift 2 ;;
      --source) source_filter="$2"; shift 2 ;;
      *) [[ -z "$keyword" ]] && keyword="$1"; shift ;;
    esac
  done
  [[ ! -f "$REPO_PATH_INDEX" ]] && { echo "ERR no repo-path-index found at $REPO_PATH_INDEX" >&2; exit 1; }

  local total=0 fresh=0 stale_count=0 results=""
  local line name path
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == "#"* ]] && continue
    name="${line%%:*}"
    path="${line#*:}"
    path=$(echo "$path" | sed 's/^ *//;s/ *$//' | sed "s|^~|$HOME|")
    [[ -d "$path" ]] || continue

    local root
    root=$(resolve_root "$path")
    local shopt_saved
    shopt_saved=$(shopt -p nullglob) && shopt -s nullglob
    for meta in "$root"/sessions/*/meta.yaml; do
      local id target status stale created source age
      id=$(meta_field "$meta" id)
      target=$(meta_field "$meta" target)
      [[ -z "$id" || -z "$target" ]] && continue
      status=$(meta_field "$meta" status)
      stale=$(meta_field "$meta" stale)
      created=$(meta_field "$meta" created)
      source=$(meta_field "$meta" source)
      if [[ -n "$keyword" ]] && ! echo "$target $id" | grep -qi "$keyword"; then continue; fi
      [[ -n "$source_filter" && "$source" != "$source_filter" ]] && continue
      age=$(compute_age_days "$created")
      if [[ -n "$since" ]]; then
        [[ $age -gt ${since%d} ]] && continue
      fi
      local slabel="fresh"
      [[ "$stale" == "true" ]] && slabel="stale"
      results+="$name\t$id\t$source\t$status\t$slabel\t${age}d\n"
      total=$((total + 1))
      if [[ "$slabel" == "stale" ]]; then stale_count=$((stale_count + 1)); else fresh=$((fresh + 1)); fi
    done
    eval "$shopt_saved"
  done < "$REPO_PATH_INDEX"

  if [[ $total -eq 0 ]]; then
    echo "MISS"
    return 0
  fi
  echo -e "$results" | column -t 2>/dev/null || echo -e "$results"
  echo "TOTAL=$total FRESH=$fresh STALE=$stale_count"
}

# ─── cmd_check_stale ─────────────────────────────────────────────────────────

check_stale_in_root() {
  local root="$1"
  local shopt_saved
  shopt_saved=$(shopt -p nullglob) && shopt -s nullglob
  for session_dir in "$root"/sessions/*/; do
    [[ -f "$session_dir/meta.yaml" ]] || continue
    local id status
    id=$(meta_field "$session_dir/meta.yaml" id)
    status=$(meta_field "$session_dir/meta.yaml" status)
    [[ -z "$id" ]] && continue
    if is_stale "$session_dir"; then
      [[ "$status" == "complete" ]] && sed -i "s/^stale: .*/stale: true/" "$session_dir/meta.yaml" 2>/dev/null || true
      echo "$id STALE source_changed"
    else
      echo "$id FRESH"
    fi
  done
  eval "$shopt_saved"
}

cmd_check_stale() {
  local root_flag="" all=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root_flag="$2"; shift 2 ;;
      --all) all=true; shift ;;
      *) shift ;;
    esac
  done
  if [[ "$all" == "true" ]]; then
    [[ ! -f "$REPO_PATH_INDEX" ]] && { echo "ERR no repo-path-index" >&2; exit 1; }
    local line path
    while IFS= read -r line; do
      [[ -z "$line" || "$line" == "#"* ]] && continue
      path="${line#*:}"
      path=$(echo "$path" | sed 's/^ *//;s/ *$//' | sed "s|^~|$HOME|")
      [[ -d "$path" ]] && check_stale_in_root "$(resolve_root "$path")"
    done < "$REPO_PATH_INDEX"
  else
    check_stale_in_root "$(resolve_root "$root_flag")"
  fi
}

# ─── cmd_gc ──────────────────────────────────────────────────────────────────

gc_in_root() {
  local root="$1" max_age="$2"
  local archive_dir="$root/.archive"
  local shopt_saved
  shopt_saved=$(shopt -p nullglob) && shopt -s nullglob
  for session_dir in "$root"/sessions/*/; do
    [[ -f "$session_dir/meta.yaml" ]] || continue
    local status stale created age id size
    status=$(meta_field "$session_dir/meta.yaml" status)
    stale=$(meta_field "$session_dir/meta.yaml" stale)
    created=$(meta_field "$session_dir/meta.yaml" created)
    [[ "$status" != "complete" && "$stale" != "true" && "$status" != "superseded" ]] && continue
    age=$(compute_age_days "$created")
    [[ $age -lt $max_age ]] && continue
    ensure_dir "$archive_dir"
    id=$(basename "$session_dir")
    size=$(du -sk "$session_dir" 2>/dev/null | awk '{print $1}')
    mv "$session_dir" "$archive_dir/"
    echo "ARCHIVED $id (${size}KB)"
  done
  eval "$shopt_saved"
}

cmd_gc() {
  local max_age=30 root_flag="" all=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --max-age) max_age="$2"; shift 2 ;;
      --root) root_flag="$2"; shift 2 ;;
      --all) all=true; shift ;;
      *) shift ;;
    esac
  done
  if [[ "$all" == "true" ]]; then
    [[ ! -f "$REPO_PATH_INDEX" ]] && { echo "ERR no repo-path-index" >&2; exit 1; }
    local line path
    while IFS= read -r line; do
      [[ -z "$line" || "$line" == "#"* ]] && continue
      path="${line#*:}"
      path=$(echo "$path" | sed 's/^ *//;s/ *$//' | sed "s|^~|$HOME|")
      [[ -d "$path" ]] && gc_in_root "$(resolve_root "$path")" "$max_age"
    done < "$REPO_PATH_INDEX"
  else
    gc_in_root "$(resolve_root "$root_flag")" "$max_age"
  fi
}

# ─── Dispatch ────────────────────────────────────────────────────────────────

case "${1:-}" in
  gate)          shift; cmd_gate "$@" ;;
  query)         shift; cmd_query "$@" ;;
  start)         shift; cmd_start "$@" ;;
  append)        shift; cmd_append "$@" ;;
  save-report)   shift; cmd_save_report "$@" ;;
  complete)      shift; cmd_complete "$@" ;;
  read-chain)    shift; cmd_read_chain "$@" ;;
  list)          shift; cmd_list "$@" ;;
  search-all)    shift; cmd_search_all "$@" ;;
  check-stale)   shift; cmd_check_stale "$@" ;;
  gc)            shift; cmd_gc "$@" ;;
  -h|--help)
    echo "Usage: research.sh <gate|query|start|append|save-report|complete|read-chain|list|search-all|check-stale|gc>"
    echo "  gate --intent research|trace [--root PATH] [--force-trace]"
    echo "  read-chain <SID> [--summary|--full] [--project NAME|--root PATH]"
    echo "Schema: shared/protocols/trace-artifacts-schema.md"
    ;;
  *)
    echo "Usage: research.sh <gate|query|start|append|save-report|complete|read-chain|list|search-all|check-stale|gc>" >&2
    exit 1
    ;;
esac

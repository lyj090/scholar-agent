#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# audit-allowlist.sh — discover Claude config/allowlist files + detect/clean
#                      redundant allowlist rules (system-level).
# ─────────────────────────────────────────────────────────────────────────────
# Companion to permission-prompt-stats.sh (prompt counting) and
# sync-allowlist.sh (shared∪local merge). This script answers a different
# question: "which allowlist rules are redundant and can be dropped?"
#
# Redundancy = either
#   · exact duplicate within one file, or
#   · a parent glob that already covers a child rule
#       e.g.  Bash(ps *)        ⊇  Bash(ps aux), Bash(ps -ef *)
#             Bash(python3 *)   ⊇  Bash(python3 -c *)
#             Bash(git commit *)⊇  Bash(git commit -m *)
# Coverage is decided by turning each rule into an anchored regex
# (escape meta, `*`→`.*`) and testing whether it matches another rule
# verbatim. `Bash(docker ps)` and `Bash(docker ps *)` are kept BOTH
# (the latter needs an argument), matching real allow-matching semantics.
#
# Two allowlist schemas are recognized automatically:
#   · .allowedTools          → system files (settings.json, allowlist-common.json)
#   · .permissions.allow     → project files (.claude/settings*.json)
#
# Usage:
#   tree  [--root DIR]...        depth-3 discovery of Claude config + allowlist files
#   audit [--root DIR]...        report redundancy across all discovered files
#   fix   [--shared|--local|--all] [--apply]
#                                remove redundant rules (default: dry-run, --all)
#                                writes a backup before mutating; jq-validates output
#   cross [--apply]              cross-file maintenance shared↔local:
#                                [1] prune local-only rules covered by a shared
#                                    parent (--apply removes them from settings.json),
#                                [2] suggest generic local extras to promote to shared
#                                    (suggest-only; machine-specific rules filtered out).
#   project [--root DIR] [--apply]
#                                clean project-level allowlists (.claude/settings*.json,
#                                .permissions.allow): drop in-file redundancy + rules
#                                already covered by the merged system-level baseline
#                                (safe — Claude unions scopes). Preserves deny/ask.
#   promote [--apply]            extract generic local-only rules up into shared
#                                (machine-specific rules filtered out). --apply appends
#                                to shared, removes from local, backs up + jq-validates.
#
# No new deps: bash + jq (+ optional reuse of lib/ui.sh for colors).
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SHARED_FILE="$REPO_ROOT/shared/config/allowlist-common.json"
CLAUDE_HOME="${HOME}/.claude"
SETTINGS="$CLAUDE_HOME/settings.json"
COUNTS_FILE="$CLAUDE_HOME/permission-prompt-counts.json"
BACKUP_DIR="$CLAUDE_HOME/backups"
REPORT_DIR="$REPO_ROOT/.ai/reports"

# Default discovery roots: global config home + $WORKSPACE_ROOT (defaults to $HOME).
# Override with WORKSPACE_ROOT=/path/to/your/projects to scan a specific tree.
DEFAULT_ROOTS=("$CLAUDE_HOME" "${WORKSPACE_ROOT:-$HOME}")

command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }

# ── colors (reuse lib/ui.sh if present, else local fallback) ─────────────────
if [[ -f "$REPO_ROOT/scripts/lib/ui.sh" ]]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/lib/ui.sh"
  G="$C_GREEN"; Y="$C_YELLOW"; B="$C_BOLD"; D="$C_DIM"; C="$C_CYAN"; R="$C_RESET"
elif [[ -t 1 ]]; then
  G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; D=$'\033[2m'; C=$'\033[36m'; R=$'\033[0m'
else
  G=''; Y=''; B=''; D=''; C=''; R=''
fi

usage() { sed -n '2,46p' "$0" | sed 's|^# \?||'; exit "${1:-0}"; }

# ── jq library: glob→regex + redundancy analysis ────────────────────────────
# find_redundant: input = array of rule strings →
#   output = array of {entry, covered_by:[...]} for every removable rule.
# A rule B is removable if some A (A!=B) covers it; for mutually-covering
# (equivalent) pairs the lexicographically larger one is dropped so one survives.
read -r -d '' JQ_LIB <<'JQ' || true
# Escape regex metacharacters in a literal segment.
def _esc: gsub("(?<c>[.^$()|\\[\\]{}+?\\\\])"; "\\\(.c)");
# Glob → anchored regex: split on '*', escape each piece, rejoin with '.*'.
def glob_to_regex:
  (split("*") | map(_esc) | join(".*")) | "^" + . + "$";

def find_redundant:
  (map({s: ., re: glob_to_regex})) as $items
  | [ $items[]
      | . as $b
      | ( $items
          | map( select(
              .s as $as | .re as $are
              | ($as != $b.s)
              and ($b.s | test($are))
              and ( (($as | test($b.re)) | not) or ($as > $b.s) )
            ) | .s )
        ) as $covs
      | select(($covs | length) > 0)
      | { entry: $b.s, covered_by: $covs }
    ];

# exact_dups: input array → array of strings that appear more than once
def exact_dups:
  group_by(.) | map(select(length > 1) | .[0]);
JQ

# Detect which jq path holds the allowlist in a given file. Echoes the jq
# expression (".allowedTools" or ".permissions.allow") or empty if none.
detect_schema() {
  local f="$1"
  jq -r '
    if (.allowedTools? // empty) != null then ".allowedTools"
    elif (.permissions?.allow? // empty) != null then ".permissions.allow"
    else "" end
  ' "$f" 2>/dev/null || true
}

# ── discover allowlist-bearing files under roots (maxdepth 3) ────────────────
# Prints TSV lines: <file>\t<jqpath>\t<count>
discover_files() {
  local -a roots=("$@")
  [[ ${#roots[@]} -eq 0 ]] && roots=("${DEFAULT_ROOTS[@]}")

  # Always include the canonical system files explicitly.
  local -a seeds=("$SETTINGS" "$SHARED_FILE")

  local -A seen=()
  local f rp path cnt
  for f in "${seeds[@]}"; do
    [[ -f "$f" ]] || continue
    rp="$(realpath "$f" 2>/dev/null || echo "$f")"
    [[ -n "${seen[$rp]:-}" ]] && continue
    seen[$rp]=1
    path="$(detect_schema "$f")"
    [[ -z "$path" ]] && continue
    cnt="$(jq "$path | length" "$f" 2>/dev/null || echo 0)"
    printf '%s\t%s\t%s\n' "$f" "$path" "$cnt"
  done

  local root
  for root in "${roots[@]}"; do
    [[ -d "$root" ]] || continue
    while IFS= read -r f; do
      rp="$(realpath "$f" 2>/dev/null || echo "$f")"
      [[ -n "${seen[$rp]:-}" ]] && continue
      seen[$rp]=1
      path="$(detect_schema "$f")"
      [[ -z "$path" ]] && continue
      cnt="$(jq "$path | length" "$f" 2>/dev/null || echo 0)"
      printf '%s\t%s\t%s\n' "$f" "$path" "$cnt"
    done < <(find "$root" -maxdepth 3 \
                  \( -name 'settings.json' -o -name 'settings.local.json' -o -name 'allowlist-common.json' \) \
                  -type f 2>/dev/null)
  done
}

# ── tree ─────────────────────────────────────────────────────────────────────
cmd_tree() {
  local -a roots=()
  while [[ $# -gt 0 ]]; do
    case "$1" in --root) roots+=("$2"); shift 2 ;; *) usage 2 ;; esac
  done

  echo "${B}Claude config tree (depth ≤3):${R} ${D}$CLAUDE_HOME${R}"
  if [[ -d "$CLAUDE_HOME" ]]; then
    # Top-level config files + curated dirs only; skip noisy runtime trees
    # (sessions, tasks, projects, runtime, shell-snapshots, file-history, …).
    find "$CLAUDE_HOME" -maxdepth 3 \
         \( -name '*.json' -o -name 'CLAUDE.md' -o -name '*.sh' \) -type f 2>/dev/null \
      | grep -Ev "/(sessions|tasks|projects|runtime|shell-snapshots|file-history|paste-cache|session-env|stats-cache)/" \
      | sort \
      | sed "s|^$CLAUDE_HOME|.|" \
      | awk -v d="$D" -v r="$R" '{ printf "  %s%s%s\n", d, $0, r }'
  fi

  echo
  echo "${B}Allowlist-bearing files discovered:${R}"
  local any=false
  while IFS=$'\t' read -r f path cnt; do
    any=true
    printf "  %s%4s%s  %s%s%s  %s(%s)%s\n" "$Y" "$cnt" "$R" "$C" "$f" "$R" "$D" "$path" "$R"
  done < <(discover_files "${roots[@]}")
  [[ "$any" == false ]] && echo "  ${D}none found${R}"
  return 0
}

# ── audit ──────────────────────────────────────────────────────────────────
# Emits the full plain-text report to stdout (caller routes >60 lines to file).
build_report() {
  local -a roots=("$@")
  local files_tsv; files_tsv="$(discover_files "${roots[@]}")"

  echo "# Allowlist audit — $(date -Iseconds 2>/dev/null || date)"
  echo
  echo "## Files"
  echo
  printf '%s\n' "$files_tsv" | awk -F'\t' '{ printf "- %s  (%s, %s rules)\n", $1, $2, $3 }'
  echo

  # Per-file redundancy.
  echo "## Per-file redundancy (parent glob covers child / exact dup)"
  echo
  local f path cnt arr dups redun n_dups n_redun
  local total_redun=0
  while IFS=$'\t' read -r f path cnt; do
    arr="$(jq -c "$path // []" "$f" 2>/dev/null || echo '[]')"
    dups="$(printf '%s' "$arr"   | jq -c "$JQ_LIB exact_dups" 2>/dev/null || echo '[]')"
    redun="$(printf '%s' "$arr"  | jq -c "$JQ_LIB find_redundant" 2>/dev/null || echo '[]')"
    n_dups="$(printf '%s' "$dups"   | jq 'length')"
    n_redun="$(printf '%s' "$redun" | jq 'length')"
    (( total_redun += n_redun ))
    if [[ "$n_dups" -eq 0 && "$n_redun" -eq 0 ]]; then continue; fi
    echo "### $f"
    if [[ "$n_dups" -gt 0 ]]; then
      echo "- exact duplicates ($n_dups):"
      printf '%s' "$dups" | jq -r '.[] | "    · " + .'
    fi
    if [[ "$n_redun" -gt 0 ]]; then
      echo "- covered-by-parent ($n_redun):"
      printf '%s' "$redun" | jq -r '.[] | "    · " + .entry + "   ⊆  " + (.covered_by | join(", "))'
    fi
    echo
  done <<<"$files_tsv"
  [[ "$total_redun" -eq 0 ]] && echo "(no per-file redundancy found)" && echo

  # Cross-file: shared ∩ local (governance — should live in shared only).
  echo "## Cross-file overlap (shared ∩ local)"
  echo
  if [[ -f "$SHARED_FILE" && -f "$SETTINGS" ]]; then
    local overlap
    overlap="$(jq -n \
      --argjson s "$(jq '.allowedTools // []' "$SHARED_FILE")" \
      --argjson l "$(jq '.allowedTools // []' "$SETTINGS")" \
      '$s - ($s - $l)' 2>/dev/null || echo '[]')"
    local n_ov; n_ov="$(printf '%s' "$overlap" | jq 'length')"
    if [[ "$n_ov" -gt 0 ]]; then
      echo "$n_ov rules exist in BOTH shared and local settings.json."
      echo "sync-allowlist.sh treats these as shared-managed; keep them in shared only:"
      printf '%s' "$overlap" | jq -r '.[] | "    · " + .'
    else
      echo "(no overlap)"
    fi
  else
    echo "(shared or local settings missing)"
  fi
  echo

  # counts.json entries already covered by merged allowlist (safe to prune).
  echo "## permission-prompt-counts.json — already covered (prunable)"
  echo
  if [[ -f "$COUNTS_FILE" ]]; then
    local merged covered_keys n_ck
    merged="$(jq -n \
      --argjson s "$(jq '.allowedTools // []' "$SHARED_FILE" 2>/dev/null || echo '[]')" \
      --argjson l "$(jq '.allowedTools // []' "$SETTINGS" 2>/dev/null || echo '[]')" \
      '($s + $l) | unique')"
    # Read counts via --slurpfile so its (possibly \n-laden) keys never round-trip
    # through a shell variable, which would mangle escapes.
    covered_keys="$(jq -n \
      --argjson allow "$merged" \
      --slurpfile counts "$COUNTS_FILE" \
      "$JQ_LIB"'
       ($allow | map({s: ., re: glob_to_regex})) as $rules
       | ($counts[0] | keys)
       | map(. as $k | select(any($rules[]; . as $r | ($r.s == $k) or ($k | test($r.re)))))' \
      2>/dev/null || echo '[]')"
    n_ck="$(printf '%s' "$covered_keys" | jq 'length' 2>/dev/null || echo 0)"
    if [[ "$n_ck" -gt 0 ]]; then
      echo "$n_ck counted patterns are already allow-listed (these no longer prompt):"
      printf '%s' "$covered_keys" | jq -r '.[] | "    · " + .'
      echo
      echo "prune with: permission-prompt-stats.sh remove --pattern '<pattern>'"
    else
      echo "(none)"
    fi
  else
    echo "(no counts file)"
  fi
}

cmd_audit() {
  local -a roots=()
  while [[ $# -gt 0 ]]; do
    case "$1" in --root) roots+=("$2"); shift 2 ;; *) usage 2 ;; esac
  done

  local report; report="$(build_report "${roots[@]}")"
  local lines; lines="$(printf '%s\n' "$report" | wc -l)"

  if [[ "$lines" -gt 60 ]]; then
    mkdir -p "$REPORT_DIR"
    local out="$REPORT_DIR/allowlist-audit-$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now).md"
    printf '%s\n' "$report" > "$out"
    echo "${B}Allowlist audit${R} (${lines} lines) → ${C}$out${R}"
    # 5-line chat summary
    printf '%s\n' "$report" | grep -E '^(- |[0-9]+ rules|[0-9]+ counted|### )' | head -4 \
      | sed "s|^|  ${D}|; s|$|${R}|"
    echo "  ${D}full report in file above${R}"
  else
    printf '%s\n' "$report"
  fi
}

# ── fix ──────────────────────────────────────────────────────────────────────
# Remove redundant (covered + exact-dup) rules from a single file in place.
# Args: <file> <jqpath>. Honors $DO_APPLY. Writes backup + jq-validates.
fix_file() {
  local f="$1" path="$2"
  [[ -f "$f" ]] || { echo "  ${D}skip (missing): $f${R}"; return; }

  local arr removable n_rm
  arr="$(jq -c "$path // []" "$f")"
  removable="$(printf '%s' "$arr" | jq -c "$JQ_LIB [find_redundant[].entry] + exact_dups | unique")"
  n_rm="$(printf '%s' "$removable" | jq 'length')"

  if [[ "$n_rm" -eq 0 ]]; then
    echo "  ${G}clean${R} $f ${D}(no redundancy)${R}"
    return
  fi

  echo "  ${Y}$n_rm redundant${R} in $f:"
  printf '%s' "$removable" | jq -r '.[] | "    - " + .'

  if [[ "$DO_APPLY" != true ]]; then
    echo "  ${D}(dry-run; re-run with --apply to remove)${R}"
    return
  fi

  # backup
  mkdir -p "$BACKUP_DIR"
  local stamp base bak
  stamp="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
  base="$(basename "$f")"
  bak="$BACKUP_DIR/${base}.${stamp}.bak"
  cp -p "$f" "$bak"

  # rewrite: order-preserving dedup (collapse exact dups to first occurrence),
  # then drop covered-by-parent entries. Original ordering is preserved so the
  # diff stays minimal — no alphabetical reshuffle.
  local tmp; tmp="$(mktemp "$f.XXXXXX")"
  jq "$JQ_LIB"'
    '"$path"' = (
      ('"$path"' // []) as $orig
      | ($orig | reduce .[] as $x ([]; if any(.[]; . == $x) then . else . + [$x] end)) as $dedup
      | ($dedup | [find_redundant[].entry]) as $cov
      | $dedup - $cov
    )
  ' "$f" > "$tmp"
  if ! jq empty "$tmp" >/dev/null 2>&1; then
    rm -f "$tmp"; echo "  ${Y}invalid JSON, aborted (backup kept: $bak)${R}" >&2; return 1
  fi
  mv -f "$tmp" "$f"
  echo "  ${G}fixed${R} $f ${D}(backup: $bak)${R}"
}

# ── cross ─────────────────────────────────────────────────────────────────────
# Cross-file maintenance between shared (baseline) and local (settings.json):
#   1. prune    — remove local-only rules already covered by a shared parent
#                 glob. Safe: shared re-supplies them on the next deploy, so
#                 the local copy is pure redundancy.
#   2. promote  — SUGGEST local-only rules generic enough to move into shared.
#                 Never writes to shared automatically; machine-specific rules
#                 (absolute paths, localhost, known host-only tools) are filtered
#                 out and listed separately as "keep local".
# Honors $DO_APPLY (only the prune step ever mutates a file, and only local).
LOCAL_ONLY_REGEX='/home/|localhost|127\.0\.0\.1|:[0-9]{2,5}|fcitx|kitty|google-chrome|gsettings|gnome|/tmp/|~/'

cmd_cross() {
  DO_APPLY=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --apply) DO_APPLY=true; shift ;;
      *) usage 2 ;;
    esac
  done

  [[ -f "$SHARED_FILE" ]] || { err "no shared file: $SHARED_FILE"; return 1; }
  [[ -f "$SETTINGS" ]]    || { err "no local settings: $SETTINGS"; return 1; }

  local shared local_all extras
  shared="$(jq -c '.allowedTools // []' "$SHARED_FILE")"
  local_all="$(jq -c '.allowedTools // []' "$SETTINGS")"
  extras="$(jq -cn --argjson s "$shared" --argjson l "$local_all" '$l - $s')"

  [[ "$DO_APPLY" == true ]] \
    && echo "${B}cross (apply)${R}" \
    || echo "${B}cross (dry-run)${R} ${D}— add --apply to prune local${R}"
  echo "  ${D}local-only extras: $(printf '%s' "$extras" | jq 'length')${R}"

  # ── 1. prune: local-only covered by a shared parent ────────────────────────
  # find_redundant over (shared + extras); keep only entries that are extras
  # (shared rules covering each other are handled by `fix --shared`).
  local covered
  covered="$(jq -n --argjson shared "$shared" --argjson extras "$extras" "$JQ_LIB"'
    (($shared + $extras) | find_redundant | map(.entry)) as $red
    | $extras | map(select(. as $e | $red | index($e)))
  ')"
  local n_cov; n_cov="$(printf '%s' "$covered" | jq 'length')"

  echo
  echo "  ${B}[1] local-only rules already covered by a shared parent:${R}"
  if [[ "$n_cov" -eq 0 ]]; then
    echo "      ${D}none — nothing to prune${R}"
  else
    printf '%s' "$covered" | jq -r '.[] | "      - " + .'
    if [[ "$DO_APPLY" == true ]]; then
      mkdir -p "$BACKUP_DIR"
      local stamp bak tmp
      stamp="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
      bak="$BACKUP_DIR/settings.json.${stamp}.bak"
      cp -p "$SETTINGS" "$bak"
      tmp="$(mktemp "$SETTINGS.XXXXXX")"
      jq --argjson rm "$covered" '
        .allowedTools = ((.allowedTools // []) - $rm)
      ' "$SETTINGS" > "$tmp"
      if jq empty "$tmp" >/dev/null 2>&1; then
        mv -f "$tmp" "$SETTINGS"
        echo "      ${G}pruned $n_cov from local${R} ${D}(backup: $bak)${R}"
      else
        rm -f "$tmp"; err "invalid JSON, aborted (backup kept: $bak)"; return 1
      fi
    else
      echo "      ${D}(dry-run; --apply to remove from settings.json)${R}"
    fi
  fi

  # recompute extras after a possible prune
  local_all="$(jq -c '.allowedTools // []' "$SETTINGS")"
  extras="$(jq -cn --argjson s "$shared" --argjson l "$local_all" '$l - $s')"

  # ── 2. promote suggestions (never auto-writes shared) ──────────────────────
  local promote keep
  promote="$(printf '%s' "$extras" | jq -c --arg re "$LOCAL_ONLY_REGEX" '
    map(select(test($re) | not))')"
  keep="$(printf '%s' "$extras" | jq -c --arg re "$LOCAL_ONLY_REGEX" '
    map(select(test($re)))')"

  echo
  echo "  ${B}[2] promote candidates${R} ${D}(generic → consider moving to shared):${R}"
  if [[ "$(printf '%s' "$promote" | jq 'length')" -eq 0 ]]; then
    echo "      ${D}none${R}"
  else
    printf '%s' "$promote" | jq -r '.[]' | while IFS= read -r p; do
      echo "      ${Y}?${R} $p"
      echo "        ${D}permission-prompt-stats.sh apply --scope shared --pattern '$p'${R}"
    done
    echo "      ${D}(suggestions only — review before promoting; shared is git-tracked & cross-device)${R}"
  fi

  echo
  echo "  ${B}[3] keep local${R} ${D}(machine-specific — never promote):${R}"
  if [[ "$(printf '%s' "$keep" | jq 'length')" -eq 0 ]]; then
    echo "      ${D}none${R}"
  else
    printf '%s' "$keep" | jq -r '.[] | "      · " + .'
  fi
  return 0
}

cmd_fix() {
  local scope="all"
  DO_APPLY=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --shared) scope="shared"; shift ;;
      --local)  scope="local";  shift ;;
      --all)    scope="all";    shift ;;
      --apply)  DO_APPLY=true;  shift ;;
      *) usage 2 ;;
    esac
  done

  [[ "$DO_APPLY" == true ]] \
    && echo "${B}fix (apply)${R}" \
    || echo "${B}fix (dry-run)${R} ${D}— add --apply to write${R}"

  case "$scope" in
    shared) fix_file "$SHARED_FILE" ".allowedTools" ;;
    local)  fix_file "$SETTINGS"    ".allowedTools" ;;
    all)
      fix_file "$SETTINGS"    ".allowedTools"
      fix_file "$SHARED_FILE" ".allowedTools"
      ;;
  esac

  if [[ "$DO_APPLY" == true && "$scope" != "local" ]]; then
    echo "  ${D}note: shared change is git-tracked — commit shared/config/allowlist-common.json to sync devices.${R}"
  fi
}

# ── project ────────────────────────────────────────────────────────────────
# Maintain project-level allowlists (.claude/settings*.json, .permissions.allow).
# For each discovered project file, remove:
#   · in-file redundancy (parent-glob-covers-child + exact dups), and
#   · rules already covered by the merged system-level allowlist
#     (shared ∪ ~/.claude/settings.json) — safe because Claude Code
#     UNIONs all scopes, so a system rule keeps allowing the command.
# Only .permissions.allow is touched; .permissions.deny/.ask and all other keys
# are preserved. Default dry-run; --apply backs up (with project name in the
# filename) + jq-validates before writing. Order-preserving.
PROJECT_ROOTS=("${WORKSPACE_ROOT:-$HOME}")

cmd_project() {
  local -a roots=()
  DO_APPLY=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root)  roots+=("$2"); shift 2 ;;
      --apply) DO_APPLY=true;  shift ;;
      *) usage 2 ;;
    esac
  done
  [[ ${#roots[@]} -eq 0 ]] && roots=("${PROJECT_ROOTS[@]}")

  # Merged system-level allowlist (shared ∪ local settings.json).
  local sys
  sys="$(jq -n \
    --argjson s "$(jq '.allowedTools // []' "$SHARED_FILE" 2>/dev/null || echo '[]')" \
    --argjson l "$(jq '.allowedTools // []' "$SETTINGS"    2>/dev/null || echo '[]')" \
    '($s + $l) | unique')"

  [[ "$DO_APPLY" == true ]] \
    && echo "${B}project (apply)${R}" \
    || echo "${B}project (dry-run)${R} ${D}— add --apply to write${R}"
  echo "  ${D}system-level baseline: $(printf '%s' "$sys" | jq 'length') rules${R}"

  # Discover project files (.permissions.allow schema), maxdepth 3, realpath-dedup.
  local -A seen=()
  local f rp any=false
  while IFS= read -r f; do
    rp="$(realpath "$f" 2>/dev/null || echo "$f")"
    [[ -n "${seen[$rp]:-}" ]] && continue
    seen[$rp]=1
    jq -e '.permissions.allow? // empty | type == "array"' "$f" >/dev/null 2>&1 || continue
    any=true
    _project_fix_one "$f" "$sys"
  done < <(
    for root in "${roots[@]}"; do
      [[ -d "$root" ]] && find "$root" -maxdepth 3 \
        \( -name 'settings.json' -o -name 'settings.local.json' \) \
        -path '*.claude*' -type f 2>/dev/null
    done
  )
  [[ "$any" == false ]] && echo "  ${D}no project-level allowlist files found${R}"
  return 0
}

# _project_fix_one <file> <sys_json>
_project_fix_one() {
  local f="$1" sys="$2"
  local short; short="${f/#$HOME/\~}"

  # removable = (in-file redundant) ∪ (covered by system baseline). Computed
  # entirely inside jq from files/argjson so backslash-laden rules are safe.
  local removable n_rm
  removable="$(jq -n --slurpfile d <(jq '.permissions.allow // []' "$f") --argjson sys "$sys" "$JQ_LIB"'
    ($d[0]) as $arr
    | ($sys | map({s: ., re: glob_to_regex})) as $srules
    | ([ $arr | find_redundant[].entry ]) as $selfred
    | ($arr | exact_dups) as $dups
    | ($arr | map(select(. as $x | any($srules[]; . as $r | ($r.s == $x) or ($x | test($r.re)))))) as $bycov
    | ($selfred + $dups + $bycov) | unique
  ')"
  n_rm="$(printf '%s' "$removable" | jq 'length' 2>/dev/null || echo 0)"

  if [[ "$n_rm" -eq 0 ]]; then
    echo "  ${G}clean${R} $short ${D}(no redundancy)${R}"
    return
  fi

  echo "  ${Y}$n_rm redundant${R} $short:"
  printf '%s' "$removable" | jq -r '.[] | "    - " + .'

  if [[ "$DO_APPLY" != true ]]; then
    echo "    ${D}(dry-run; --apply to remove)${R}"
    return
  fi

  mkdir -p "$BACKUP_DIR"
  local stamp proj bak tmp
  stamp="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
  proj="$(basename "$(dirname "$(dirname "$f")")")"
  bak="$BACKUP_DIR/${proj}.$(basename "$f").${stamp}.bak"
  cp -p "$f" "$bak"

  tmp="$(mktemp "$f.XXXXXX")"
  # Order-preserving: collapse exact dups, then subtract the removable set.
  jq --argjson rm "$removable" '
    .permissions.allow = (
      (.permissions.allow // []) as $orig
      | ($orig | reduce .[] as $x ([]; if any(.[]; . == $x) then . else . + [$x] end))
      | . - $rm
    )
  ' "$f" > "$tmp"
  if jq empty "$tmp" >/dev/null 2>&1; then
    mv -f "$tmp" "$f"
    echo "    ${G}fixed${R} ${D}(backup: ${bak/#$HOME/\~})${R}"
  else
    rm -f "$tmp"; err "invalid JSON, aborted (backup kept: $bak)"; return 1
  fi
}

# ── promote ────────────────────────────────────────────────────────────────
# Extract generic local-only rules (in ~/.claude/settings.json but not
# in shared) up into shared/config/allowlist-common.json. Machine-specific rules
# (abs paths, localhost, ports, fcitx5/kitty/...) are filtered out via
# $LOCAL_ONLY_REGEX and listed as "keep local". Default dry-run lists the
# promotable set + ready-to-run commands; --apply appends them to shared
# (git-tracked) after jq-validation.
cmd_promote() {
  DO_APPLY=false
  while [[ $# -gt 0 ]]; do
    case "$1" in --apply) DO_APPLY=true; shift ;; *) usage 2 ;; esac
  done

  [[ -f "$SHARED_FILE" && -f "$SETTINGS" ]] || { err "need both shared and local settings"; return 1; }

  local shared local_all extras promote keep
  shared="$(jq -c '.allowedTools // []' "$SHARED_FILE")"
  local_all="$(jq -c '.allowedTools // []' "$SETTINGS")"
  extras="$(jq -cn --argjson s "$shared" --argjson l "$local_all" '$l - $s')"
  promote="$(printf '%s' "$extras" | jq -c --arg re "$LOCAL_ONLY_REGEX" 'map(select(test($re) | not))')"
  keep="$(printf '%s' "$extras" | jq -c --arg re "$LOCAL_ONLY_REGEX" 'map(select(test($re)))')"

  [[ "$DO_APPLY" == true ]] \
    && echo "${B}promote (apply)${R}" \
    || echo "${B}promote (dry-run)${R} ${D}— add --apply to write shared${R}"

  local n_p; n_p="$(printf '%s' "$promote" | jq 'length')"
  echo
  echo "  ${B}promotable → shared${R} ${D}($n_p generic local-only rules):${R}"
  if [[ "$n_p" -eq 0 ]]; then
    echo "    ${D}none${R}"
  else
    printf '%s' "$promote" | jq -r '.[] | "    + " + .'
  fi

  echo
  echo "  ${B}keep local${R} ${D}(machine-specific — never promoted):${R}"
  printf '%s' "$keep" | jq -r '.[] | "    · " + .' 2>/dev/null
  [[ "$(printf '%s' "$keep" | jq 'length')" -eq 0 ]] && echo "    ${D}none${R}"

  if [[ "$DO_APPLY" != true || "$n_p" -eq 0 ]]; then
    [[ "$n_p" -gt 0 ]] && echo && echo "    ${D}(dry-run; --apply to append these to shared, then commit)${R}"
    return 0
  fi

  # apply: append promotable to shared (unique, order-preserving), then drop
  # them from local so the same rule no longer lives in both scopes.
  local tmp
  tmp="$(mktemp "$SHARED_FILE.XXXXXX")"
  jq --argjson add "$promote" '
    .allowedTools = (
      (.allowedTools // []) + ($add - (.allowedTools // []))
    )
  ' "$SHARED_FILE" > "$tmp"
  if jq empty "$tmp" >/dev/null 2>&1; then
    mv -f "$tmp" "$SHARED_FILE"
    echo
    echo "  ${G}promoted $n_p rules → shared${R}"
  else
    rm -f "$tmp"; err "invalid shared JSON, aborted"; return 1
  fi

  # remove promoted rules from local settings (they'll re-arrive via shared on deploy)
  mkdir -p "$BACKUP_DIR"
  local stamp bak ltmp
  stamp="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
  bak="$BACKUP_DIR/settings.json.${stamp}.bak"
  cp -p "$SETTINGS" "$bak"
  ltmp="$(mktemp "$SETTINGS.XXXXXX")"
  jq --argjson rm "$promote" '.allowedTools = ((.allowedTools // []) - $rm)' "$SETTINGS" > "$ltmp"
  if jq empty "$ltmp" >/dev/null 2>&1; then
    mv -f "$ltmp" "$SETTINGS"
    echo "  ${G}removed from local${R} ${D}(backup: ${bak/#$HOME/\~})${R}"
  else
    rm -f "$ltmp"; err "invalid local JSON, aborted (shared already updated)"; return 1
  fi
  echo "  ${D}note: commit shared/config/allowlist-common.json to sync to other devices.${R}"
}

# ── dispatch ─────────────────────────────────────────────────────────────────
case "${1:-}" in
  tree)    shift; cmd_tree "$@" ;;
  audit)   shift; cmd_audit "$@" ;;
  fix)     shift; cmd_fix "$@" ;;
  cross)   shift; cmd_cross "$@" ;;
  project) shift; cmd_project "$@" ;;
  promote) shift; cmd_promote "$@" ;;
  -h|--help|help) usage 0 ;;
  "")      cmd_audit ;;
  *) usage 2 ;;
esac

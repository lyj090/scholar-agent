#!/usr/bin/env bash
# bootstrap_skills.sh — shared symlink deploy loop for platform bootstrap scripts
#
# Requires: ui.sh, skill_groups.sh, bootstrap_skill_filter.sh sourced first.

bootstrap_resolve_deploy_groups() {
  local platform="${1:-default}"
  if [[ ${#DEPLOY_GROUPS[@]} -eq 0 ]]; then
    DEPLOY_GROUPS=()
    while IFS= read -r g; do
      [[ -n "$g" ]] && DEPLOY_GROUPS+=("$g")
    done < <(resolve_deploy_groups "$platform" "${DEPLOY_NO_LITERATURE:-false}" "${DEPLOY_FORCE_LITERATURE:-false}")
  fi
}

bootstrap_collect_skill_dirs() {
  local src_dir="$1"
  SKILL_DIRS=()
  while IFS= read -r -d '' d; do
    SKILL_DIRS+=("$d")
  done < <(find "$src_dir" -maxdepth 1 -mindepth 1 -type d -print0 2>/dev/null)
}

# Remove stale symlinks in dst_dir that point into src_dir but are not in expected basenames.
bootstrap_prune_managed_symlinks() {
  local agent="$1"
  local dst_dir="$2"
  local src_dir="$3"
  shift 3
  local -a expected=("$@")

  [[ "${DEPLOY_DRY_RUN:-}" == "1" ]] && return 0

  shopt -s nullglob
  for existing in "$dst_dir"/*; do
    [[ -L "$existing" ]] || continue
    local base target keep=false
    base="$(basename "$existing")"
    target="$(readlink -f "$existing" 2>/dev/null || readlink "$existing")"
    [[ "$target" == "$src_dir"/* ]] || continue
    for exp in "${expected[@]}"; do
      if [[ "$base" == "$exp" ]]; then
        keep=true
        break
      fi
    done
    if [[ "$keep" == false ]]; then
      rm -f "$existing"
      record_action "$agent" "${base%.md}" "PRUNE"
    fi
  done
  shopt -u nullglob
}

# Remove only links/files that can be proven to be managed by this source tree.
# User-created files, directories, and links to other trees are preserved.
bootstrap_undeploy_skills() {
  local agent="$1"
  local src_dir="$2"
  local dst_dir="$3"
  local link_mode="$4"
  local removed=0 preserved=0
  # Resolve both sides: readlink -f collapses symlinked repo path components
  # (e.g. a symlinked workspace dir -> ~), so comparing against the literal src_dir never matched.
  local resolved_src
  resolved_src="$(readlink -f "$src_dir" 2>/dev/null || echo "$src_dir")"

  if [[ -d "$dst_dir" ]]; then
    shopt -s nullglob
    for existing in "$dst_dir"/*; do
      [[ -L "$existing" ]] || continue
      local target
      target="$(readlink -f "$existing" 2>/dev/null || readlink "$existing")"
      [[ "$target" == "$resolved_src"/* ]] || continue
      if [[ "${DEPLOY_DRY_RUN:-}" == "1" ]]; then
        info "would remove $existing"
      else
        rm -f "$existing"
      fi
      record_action "$agent" "$(basename "$existing")" "REMOVE"
      removed=$((removed + 1))
    done
    shopt -u nullglob
  fi

  # Older deploys may have copied Claude command files instead of linking them.
  # Remove those only when their bytes still equal the current managed source.
  if [[ "$link_mode" == "file" && -d "$dst_dir" && -d "$src_dir" ]]; then
    shopt -s nullglob
    for skill_dir in "$src_dir"/*/; do
      local skill_name source_path dest_path
      skill_name="$(basename "$skill_dir")"
      source_path="$skill_dir/$skill_name.md"
      dest_path="$dst_dir/$skill_name.md"
      [[ -f "$source_path" && -f "$dest_path" && ! -L "$dest_path" ]] || continue
      if cmp -s "$source_path" "$dest_path"; then
        if [[ "${DEPLOY_DRY_RUN:-}" == "1" ]]; then
          info "would remove copied managed file $dest_path"
        else
          rm -f "$dest_path"
        fi
        record_action "$agent" "$skill_name" "REMOVE"
        removed=$((removed + 1))
      else
        preserved=$((preserved + 1))
      fi
    done
    shopt -u nullglob
  fi

  summary_removed_line "$agent" "$removed" "$preserved"
}

# Deploy skills as symlinks.
# Usage: bootstrap_deploy_skills <agent> <src_dir> <dst_dir> <link_mode> [group...]
#   link_mode: dir  — ln skill_dir -> dst/name
#              file — ln skill_dir/name.md -> dst/name.md
bootstrap_deploy_skills() {
  local agent="$1"
  local src_dir="$2"
  local dst_dir="$3"
  local link_mode="$4"
  shift 4
  local -a groups=("$@")

  bootstrap_collect_skill_dirs "$src_dir"
  local total=${#SKILL_DIRS[@]}
  local linked=0
  local i=0
  local -a expected=()

  if [[ "${DEPLOY_DRY_RUN:-}" != "1" ]]; then
    mkdir -p "$dst_dir"
  fi
  bootstrap_reset_literature_skipped

  for skill_dir in "${SKILL_DIRS[@]}"; do
    i=$((i + 1))
    local skill_name source_path dest_name
    skill_name="$(basename "$skill_dir")"
    progress_bar "$i" "$total" "$skill_name"

    if [[ "$link_mode" == "file" ]]; then
      source_path="$skill_dir/$skill_name.md"
      dest_name="$skill_name.md"
      [[ -f "$source_path" ]] || continue
    else
      source_path="$skill_dir/SKILL.md"
      dest_name="$skill_name"
      [[ -f "$source_path" ]] || continue
    fi

    if ! bootstrap_should_link_skill "$skill_name" "${groups[@]}"; then
      record_action "$agent" "$skill_name" "SKIP"
      continue
    fi

    if [[ "${DEPLOY_DRY_RUN:-}" != "1" ]]; then
      if [[ "$link_mode" == "file" ]]; then
        ln -sfn "$source_path" "$dst_dir/$dest_name"
      else
        ln -sfn "$skill_dir" "$dst_dir/$dest_name"
      fi
    fi
    expected+=("$dest_name")
    record_action "$agent" "$skill_name" "OK"
    linked=$((linked + 1))
  done

  bootstrap_prune_managed_symlinks "$agent" "$dst_dir" "$src_dir" "${expected[@]}"

  progress_done
  summary_line "$agent" "$linked" "$total"
  bootstrap_print_literature_skips "$agent"
}

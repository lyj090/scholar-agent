#!/usr/bin/env bash
set -euo pipefail
# Generate platform-specific skill files from skills/ single source.
# Usage:
#   bash scripts/deploy/generate.sh              # generate all skills in skills/
#   bash scripts/deploy/generate.sh research     # generate one specific skill
#
# Each skill in skills/<name>/ needs:
#   - SKILL.md       (required) — the skill content
#   - meta.yaml      (optional) — platform adaptation metadata
#
# meta.yaml fields:
#   description:       one-line description (used in platform metadata)
#   invocation:        explicit|natural; derives platform invocation policies
#   context_cost:      low|medium|high maintenance label for audit output
#   script_first:      true|false maintenance label for runtime routing checks
#   display_name:      user-facing Codex skill name
#   short_description: short Codex UI blurb
#   codex_prompt:      default prompt for Codex agent
#
# Per-platform invocation mapping (single `invocation` source of truth):
#   claude  explicit → frontmatter disable-model-invocation: true
#   codex   explicit → agents/openai.yaml policy.allow_implicit_invocation: false
#   dsh     explicit → frontmatter disable-model-invocation: true (same shape as Claude)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SKILLS_SRC="$REPO_ROOT/skills"

if [[ ! -d "$SKILLS_SRC" ]]; then
  echo "generate: no skills/ directory found, nothing to do."
  exit 0
fi

write_skill_frontmatter_md() {
  local out="$1" skill_name="$2" description="$3" src_md="$4"
  if [[ -n "$description" ]]; then
    printf -- '---\nname: %s\ndescription: "%s"\n---\n\n' "$skill_name" "$(yaml_escape "$description")" > "$out"
    cat "$src_md" >> "$out"
  else
    cp "$src_md" "$out"
  fi
}

yaml_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

meta_value() {
  local meta="$1" key="$2"
  [[ -f "$meta" ]] || return 0
  grep "^${key}:" "$meta" 2>/dev/null \
    | head -n 1 \
    | sed "s/^${key}: *//; s/^\"//; s/\"$//" || true
}

generate_skill() {
  local skill_dir="$1"
  local skill_name="$(basename "$skill_dir")"
  local src_md="$skill_dir/SKILL.md"
  local meta="$skill_dir/meta.yaml"

  if [[ ! -f "$src_md" ]]; then
    echo "skip: $skill_name (no SKILL.md)"
    return 0
  fi

  # Parse meta.yaml (simple grep-based, no yq dependency)
  local description="" display_name="" short_description="" codex_prompt=""
  local invocation="" legacy_allow_implicit="" legacy_claude_fm=""
  if [[ -f "$meta" ]]; then
    description=$(meta_value "$meta" "description")
    invocation=$(meta_value "$meta" "invocation")
    display_name=$(meta_value "$meta" "display_name")
    short_description=$(meta_value "$meta" "short_description")
    codex_prompt=$(meta_value "$meta" "codex_prompt")
    legacy_allow_implicit=$(meta_value "$meta" "allow_implicit_invocation")
    legacy_claude_fm=$(meta_value "$meta" "claude_frontmatter")
  fi

  # invocation is the single source of truth. Keep legacy fields as a
  # migration fallback for locally added skills.
  if [[ -z "$invocation" ]]; then
    if [[ "$legacy_allow_implicit" == "false" ]]; then
      invocation="explicit"
    else
      invocation="natural"
    fi
  fi
  local allow_implicit=true claude_fm=""
  if [[ "$invocation" == "explicit" ]]; then
    allow_implicit=false
    claude_fm="disable-model-invocation: true"
  elif [[ "$invocation" != "natural" ]]; then
    echo "error: $skill_name has invalid invocation=$invocation (expected explicit|natural)" >&2
    return 1
  fi
  [[ -n "$legacy_claude_fm" ]] && claude_fm="$legacy_claude_fm"

  # --- Claude: claude-skills/<name>/<name>.md ---
  local claude_dir="$REPO_ROOT/claude-skills/$skill_name"
  mkdir -p "$claude_dir"
  local claude_out="$claude_dir/$skill_name.md"
  if [[ -n "$claude_fm" ]]; then
    printf -- '---\n%s\n---\n\n' "$claude_fm" > "$claude_out"
    cat "$src_md" >> "$claude_out"
  else
    cp "$src_md" "$claude_out"
  fi

  # --- Cursor / Grok: <platform>-skills/<name>/SKILL.md (same frontmatter shape) ---
  local cursor_dir="$REPO_ROOT/cursor-skills/$skill_name"
  mkdir -p "$cursor_dir"
  write_skill_frontmatter_md "$cursor_dir/SKILL.md" "$skill_name" "$description" "$src_md"

  # Grok uses the same SKILL.md frontmatter shape as Cursor
  # (name + description); body is the skill-gate stub.
  local grok_dir="$REPO_ROOT/grok-skills/$skill_name"
  mkdir -p "$grok_dir"
  write_skill_frontmatter_md "$grok_dir/SKILL.md" "$skill_name" "$description" "$src_md"

  # --- DeepSeek Harness: dsh-skills/<name>/SKILL.md ---
  # DSH discovers <root>/<name>/SKILL.md and requires YAML frontmatter with
  # `name` + `description`; a file without it is warned about and ignored, so
  # this target must never fall back to a bare copy of the source body.
  # Invocation maps to the same field Claude uses. An `explicit` skill is then
  # hidden from the model-facing catalog and reachable only through a
  # `/skill-name` gesture (or the GUI picker), matching its declared intent.
  local dsh_dir="$REPO_ROOT/dsh-skills/$skill_name"
  mkdir -p "$dsh_dir"
  local dsh_out="$dsh_dir/SKILL.md"
  local dsh_desc="${description:-$skill_name skill}"
  {
    printf -- '---\nname: %s\ndescription: "%s"\n' "$skill_name" "$(yaml_escape "$dsh_desc")"
    [[ "$invocation" == "explicit" ]] && printf -- 'disable-model-invocation: true\n'
    printf -- '---\n\n'
    cat "$src_md"
  } > "$dsh_out"

  # --- Codex: codex-skills/<name>/SKILL.md + agents/openai.yaml ---
  local codex_dir="$REPO_ROOT/codex-skills/$skill_name"
  mkdir -p "$codex_dir/agents"
  local codex_out="$codex_dir/SKILL.md"
  local codex_desc="${description:-$skill_name skill}"
  printf -- '---\nname: %s\ndescription: "%s"\n---\n\n' "$skill_name" "$(yaml_escape "$codex_desc")" > "$codex_out"
  cat "$src_md" >> "$codex_out"

  # Generate Codex agent metadata. String values are quoted per openai.yaml rules.
  if [[ -z "$display_name" ]]; then
    display_name=$(echo "$skill_name" | sed 's/-/ /g; s/\b\(.\)/\u\1/g')
  fi
  [[ -n "$short_description" ]] || short_description="${description:-$skill_name skill}"
  [[ -n "$codex_prompt" ]] || codex_prompt="Use \$$skill_name for this workflow."
  [[ -n "$allow_implicit" ]] || allow_implicit=true
  cat > "$codex_dir/agents/openai.yaml" <<YAML
version: 1
interface:
  display_name: "$(yaml_escape "$display_name")"
  short_description: "$(yaml_escape "$short_description")"
  default_prompt: "$(yaml_escape "$codex_prompt")"
policy:
  allow_implicit_invocation: ${allow_implicit}
YAML

  echo "generated: $skill_name → claude + cursor + codex + grok + dsh"
}

# --- Main ---
if [[ $# -gt 0 ]]; then
  target="$SKILLS_SRC/$1"
  if [[ ! -d "$target" ]]; then
    echo "error: skills/$1 not found" >&2
    exit 1
  fi
  generate_skill "$target"
else
  for skill_dir in "$SKILLS_SRC"/*/; do
    [[ -d "$skill_dir" ]] && generate_skill "$skill_dir"
  done
  # Prune stubs of skills that no longer exist in skills/ (prevent orphan drift).
  for plat_dir in "$REPO_ROOT"/claude-skills "$REPO_ROOT"/cursor-skills \
                  "$REPO_ROOT"/codex-skills \
                  "$REPO_ROOT"/grok-skills "$REPO_ROOT"/dsh-skills; do
    [[ -d "$plat_dir" ]] || continue
    for stub in "$plat_dir"/*/; do
      [[ -d "$stub" ]] || continue
      name="$(basename "$stub")"
      if [[ ! -d "$SKILLS_SRC/$name" ]]; then
        rm -rf "$stub"
        echo "pruned stale stub: ${plat_dir##*/}/$name"
      fi
    done
  done
fi

echo "generate: done"

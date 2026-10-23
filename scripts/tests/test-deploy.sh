#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1" >&2; exit 1; }

echo "=== deploy/undeploy tests ==="

target="$TMP/project"
bash "$ROOT/scripts/deploy/bootstrap-codex-skills.sh" --target "$target" --group core >/dev/null
[[ -L "$target/skills/research" ]] || fail "managed skill link was not deployed"
printf 'keep me\n' > "$target/skills/user-file"
bash "$ROOT/scripts/deploy/bootstrap-codex-skills.sh" --undeploy --target "$target" >/dev/null
[[ ! -e "$target/skills/research" && -f "$target/skills/user-file" ]] \
  || fail "undeploy removed the wrong skill or user file"
pass "managed skill links removed; user file preserved"

ln -s "$ROOT/codex-skills/removed-from-source" "$target/skills/stale-skill"
bash "$ROOT/scripts/deploy/bootstrap-codex-skills.sh" --undeploy --target "$target" >/dev/null
[[ ! -e "$target/skills/stale-skill" ]] || fail "broken managed link was not removed"
pass "broken managed skill link removed"

source "$ROOT/scripts/lib/marker-deploy.sh"
managed="$TMP/managed.md"
config="$TMP/config.md"
printf 'managed\n' > "$managed"
printf 'user content\n' > "$config"
marker_deploy "$managed" "$config"
DEPLOY_DRY_RUN=1 marker_undeploy "$config" >/dev/null
grep -qx 'user content' "$config" || fail "marker dry-run changed user content"
marker_undeploy "$config"
grep -qx 'user content' "$config" || fail "marker undeploy dropped user content"
! grep -q '^managed$' "$config" || fail "marker undeploy left managed content"
pass "managed marker block removed; user content preserved"

managed_only="$TMP/managed-only.md"
marker_deploy "$managed" "$managed_only"
marker_undeploy "$managed_only"
[[ ! -e "$managed_only" ]] || fail "managed-only config was not removed"
pass "managed-only config removed"

cursor_target="$TMP/cursor/rules"
CURSOR_RULES_DST="$cursor_target" bash "$ROOT/scripts/deploy/bootstrap-cursor-rules.sh" >/dev/null
[[ -f "$cursor_target/karpathy-guidelines.mdc" ]] || fail "Cursor rule was not deployed"
CURSOR_RULES_DST="$cursor_target" bash "$ROOT/scripts/deploy/bootstrap-cursor-rules.sh" --undeploy >/dev/null
[[ ! -e "$cursor_target/karpathy-guidelines.mdc" ]] || fail "Cursor rule was not removed"
pass "Cursor managed rules removed"

# DSH deploys to user scope only and must never touch a user-authored skill.
dsh_home="$TMP/dsh/skills"
DSH_SKILLS_HOME="$dsh_home" bash "$ROOT/scripts/deploy/bootstrap-dsh-skills.sh" --group core >/dev/null
[[ -L "$dsh_home/research" ]] || fail "DSH skill link was not deployed"
[[ "$(readlink -f "$dsh_home/research/SKILL.md")" == "$ROOT/dsh-skills/research/SKILL.md" ]] \
  || fail "DSH skill link does not resolve to the generated SKILL.md"
[[ ! -e "$dsh_home/zotero-paper-to-obsidian" ]] || fail "DSH deployed a non-selected group"
mkdir -p "$dsh_home/user-skill"
printf -- '---\nname: user-skill\ndescription: "mine"\n---\n\nbody\n' > "$dsh_home/user-skill/SKILL.md"
DSH_SKILLS_HOME="$dsh_home" bash "$ROOT/scripts/deploy/bootstrap-dsh-skills.sh" --undeploy >/dev/null
[[ ! -e "$dsh_home/research" && -f "$dsh_home/user-skill/SKILL.md" ]] \
  || fail "DSH undeploy removed the wrong skill or a user skill"
pass "DSH managed links removed; user skill preserved"

# A DSH skill link must expose parseable frontmatter, since dsh silently
# ignores a SKILL.md without name/description.
head -n 1 "$ROOT/dsh-skills/research/SKILL.md" | grep -qx -- '---' \
  || fail "dsh-skills/research/SKILL.md missing frontmatter"
grep -q '^disable-model-invocation: true$' "$ROOT/dsh-skills/research/SKILL.md" \
  || fail "explicit skill did not map to disable-model-invocation"
pass "DSH frontmatter and invocation mapping present"

# DSH loads its user-global instruction baseline from $DSH_HOME/AGENTS.md, which
# is not on the path of any other platform's global-config target. Without an
# explicit row in the Global Config block the global rules silently skip every
# DSH session, so pin both the deploy and the undeploy side of that target.
dsh_global_src="$ROOT/global-config/dsh-global.md"
[[ -f "$dsh_global_src" ]] || fail "global-config/dsh-global.md missing"
grep -q 'DSH_GLOBAL_DST="${DSH_HOME:-$HOME/.dsh}/AGENTS.md"' "$ROOT/deploy.sh" \
  || fail "deploy.sh has no DSH user-global instruction target"
grep -q 'marker_deploy "$DSH_GLOBAL_SRC" "$DSH_GLOBAL_DST"' "$ROOT/deploy.sh" \
  || fail "deploy.sh does not marker_deploy the DSH global instructions"
grep -q 'marker_undeploy "${DSH_HOME:-$HOME/.dsh}/AGENTS.md"' "$ROOT/deploy.sh" \
  || fail "deploy.sh does not undeploy the DSH global instructions"
pass "DSH global instruction target wired for deploy and undeploy"

# ── --target must be self-contained ──────────────────────────────────────────
# Regression: `deploy.sh --all --target <scratch>` used to repoint the user's
# real ~/scholar-agent symlink at the scratch tree, and write DSH skill links
# into $HOME/.dsh/skills. A test deploy therefore hijacked (and a matching
# --undeploy would have deleted) the user's live setup.
pass "--target is self-contained"

scratch="$TMP/scratch"
mkdir -p "$scratch"
FAKE_HOME="$TMP/fakehome"
mkdir -p "$FAKE_HOME/.dsh/skills" "$FAKE_HOME/.codex" "$FAKE_HOME/.claude" "$FAKE_HOME/.grok" "$FAKE_HOME/.cursor"

before_link=""
[[ -L "$FAKE_HOME/scholar-agent" ]] && before_link="$(readlink "$FAKE_HOME/scholar-agent")"

HOME="$FAKE_HOME" DSH_HOME="$FAKE_HOME/.dsh" \
  bash "$ROOT/deploy.sh" --all --target "$scratch" >/dev/null 2>&1 || true

# The home entry point must not have been created or repointed by a --target run.
if [[ -L "$FAKE_HOME/scholar-agent" ]]; then
  [[ "$(readlink "$FAKE_HOME/scholar-agent")" == "$before_link" ]] \
    || fail "--target repointed \$HOME/scholar-agent"
fi
pass "--target did not repoint the home entry symlink"

# DSH skills must land inside the target, never in the user-level directory.
if [[ -d "$FAKE_HOME/.dsh/skills" ]]; then
  stale="$(find "$FAKE_HOME/.dsh/skills" -maxdepth 1 -type l 2>/dev/null | wc -l)"
  [[ "$stale" -eq 0 ]] || fail "--target wrote DSH skill links into the user home"
fi
pass "--target kept DSH skills out of the user home"

echo "✅ All deploy/undeploy tests passed"

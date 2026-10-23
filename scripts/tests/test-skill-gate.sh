#!/usr/bin/env bash
set -euo pipefail
# test-skill-gate.sh - compact gate + research gate smoke tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$SCRIPT_DIR/../skills"
GATE="$SKILLS_DIR/skill-gate.sh"
RESEARCH="$SKILLS_DIR/research.sh"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# The tests exercise THIS checkout. DOTFILES_ROOT is a deployment override for
# pointing skills at another tree; honouring it here made the suite depend on a
# developer's environment — it resolved to a stale path and every gate answered
# "not configured", which then aborted the script under `set -e`.
DOTFILES="$REPO_ROOT"
FAIL=0

pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

echo "=== skill-gate.sh tests ==="

# T1 removed skills stay removed (gate must reject them)
for removed in plan-execute session-review; do
  if "$GATE" "$removed" --root "$DOTFILES" >/dev/null 2>&1; then
    fail "T1 $removed gate should reject removed skill"
  else
    pass "T1 $removed gate rejects removed skill"
  fi
done

# T2/T3 removed script-first skills stay removed
for removed in git-commit-prefix token-audit plan-execute session-review; do
  if "$GATE" "$removed" --root "$DOTFILES" >/dev/null 2>&1; then
    fail "T2 $removed gate should reject removed skill"
  else
    pass "T2 $removed gate rejects removed skill"
  fi
done

# T5 research delegates (reuse research gate)
out=$("$GATE" research --root "$DOTFILES" --intent research 2>/dev/null || true)
case "$out" in
  *READ=*research-ai.md*) pass "T5 research delegates to research gate" ;;
  *) fail "T5 research gate" ;;
esac

# T6 unknown skill
out=$("$GATE" not-a-real-skill 2>&1 || true)
case "$out" in
  *ERR*unknown_skill*) pass "T6 unknown skill rejected" ;;
  *) fail "T6 unknown skill" ;;
esac

# T7 syntax regression
if bash -n "$GATE" && bash -n "$SKILLS_DIR/research.sh"; then
  pass "T7 gate scripts syntax ok"
else
  fail "T7 syntax regression"
fi

# T8 removed: git-commit-prefix script deleted; commit flow is native git now.

# T9-T12 research gate coverage.
if "$RESEARCH" gate --intent trace --root "$DOTFILES" 2>/dev/null | grep -q 'ERR not_ros2'; then
  pass "T9 trace on non-ROS2 rejects"
else
  fail "T9 expected ERR not_ros2"
fi

count=$("$RESEARCH" gate --intent research --root "$DOTFILES" | grep -c '^READ=' || true)
if [[ "$count" -eq 1 ]]; then
  pass "T10 research emits one READ line"
else
  fail "T10 expected 1 READ line, got $count"
fi

# T10b gate output must survive a consumer that exits early. Regression guard:
# the corpus hint used to run `find` between output writes, so `| grep -q`
# closing the pipe mid-sequence killed the gate with SIGPIPE (141) under
# `set -o pipefail`.
if ( set -euo pipefail
     "$RESEARCH" gate --intent research --root "$DOTFILES" | grep -q '^OK ' ); then
  pass "T10b gate survives early-exiting consumer (no SIGPIPE)"
else
  fail "T10b gate died when piped to an early-exiting consumer"
fi

research_tmp=$(mktemp -d)
printf '<depend>rclcpp</depend>\n' > "$research_tmp/package.xml"
if "$RESEARCH" gate --intent trace --root "$research_tmp" | grep -q 'research-trace-core'; then
  pass "T11 fake ROS2 trace includes trace core"
else
  fail "T11 expected research-trace-core"
fi
rm -rf "$research_tmp"

qout=$("$RESEARCH" query deploy --root "$DOTFILES" 2>/dev/null || true)
case "$qout" in
  HIT*|MISS*) pass "T12 research query works" ;;
  *) fail "T12 research query expected HIT or MISS" ;;
esac

# T12b every routed skill must respect the compact-output budget. Regression:
# paper-lookup emitted 8 lines with one RUN_* per verb, which failed
# test-skill-consistency.py and left `agent.sh check` red.
for skill in research paper-lookup arxiv-to-library zotero-paper-to-obsidian \
             zotero-inbox-to-library obsidian-zotero-tag-governance; do
  # `|| true` matters: literature gates exit 1 with an actionable ERR when the
  # machine has no paths.local. That is a correct answer, not a test failure —
  # without this, `set -e` aborted the whole script on such a machine.
  n=$("$GATE" "$skill" --root "$DOTFILES" 2>/dev/null | wc -l || true)
  if [[ "$n" -le 6 ]]; then
    pass "T12b $skill gate emits $n lines (<=6)"
  else
    fail "T12b $skill gate emits $n lines (>6)"
  fi
done

# T12c paper-lookup must keep every capability discoverable despite the budget.
pl_out=$("$GATE" paper-lookup --root "$DOTFILES" 2>/dev/null || true)
for verb in paper-status mineru-parse mineru-push "write status"; do
  case "$pl_out" in
    *"$verb"*) pass "T12c paper-lookup gate still mentions '$verb'" ;;
    *) fail "T12c paper-lookup gate dropped '$verb'" ;;
  esac
done

# T13 was Codex custom-agent deployment; that set was removed.
cursor_tmp=$(mktemp -d)
skills_tmp=$(mktemp -d)
trap 'rm -rf "$research_tmp" "$cursor_tmp" "$skills_tmp"' EXIT

# T14 Cursor global-rule deployment must honor the shared dry-run contract.
cursor_bootstrap="$REPO_ROOT/scripts/deploy/bootstrap-cursor-rules.sh"
cursor_dry_out=$(CURSOR_RULES_DST="$cursor_tmp/rules" DEPLOY_DRY_RUN=1 bash "$cursor_bootstrap")
if [[ ! -e "$cursor_tmp/rules" ]] && echo "$cursor_dry_out" | grep -q 'Dry-run complete'; then
  pass "T14 Cursor rules dry-run has no side effects"
else
  fail "T14 Cursor rules dry-run changed destination"
fi

# T15 Cursor bootstrap owns its pre-made rule without marker duplication.
cursor_real_out=$(CURSOR_RULES_DST="$cursor_tmp/real" bash "$cursor_bootstrap")
if cmp -s "$REPO_ROOT/global-config/cursor-rules/karpathy-guidelines.mdc" \
  "$cursor_tmp/real/karpathy-guidelines.mdc"; then
  pass "T15 Cursor rule matches its source"
else
  fail "T15 Cursor rule was duplicated or changed"
fi

# T16 Shared skill deployment must not create a target directory in dry-run.
skill_bootstrap="$REPO_ROOT/scripts/deploy/bootstrap-codex-skills.sh"
# Two things are asserted, and the second has a subtlety: ui.sh maps CI=true to
# the silent level, which suppresses the summary line. On a runner the output is
# therefore legitimately empty while being populated locally. DEPLOY_QUIET is
# checked before the CI sentinel, so asking for it explicitly makes the summary
# deterministic — and the side effect is what this test actually guards.
skill_dry_out=$(env -u CI DEPLOY_QUIET=1 DEPLOY_DRY_RUN=1 CODEX_HOME="$skills_tmp/home" bash "$skill_bootstrap" --target "$skills_tmp/project" 2>&1 || true)
if [[ ! -e "$skills_tmp/project/skills" ]] && echo "$skill_dry_out" | grep -q 'Codex:'; then
  pass "T16 skill deployment dry-run has no side effects"
else
  if [[ -e "$skills_tmp/project/skills" ]]; then
    fail "T16 dry-run created $skills_tmp/project/skills (contents: $(ls "$skills_tmp/project/skills" 2>/dev/null | tr '\n' ' '))"
  else
    fail "T16 quiet bootstrap did not print its summary (got: ${skill_dry_out:0:200})"
  fi
fi

echo ""
if [[ $FAIL -eq 0 ]]; then
  echo "✅ All skill-gate tests passed"
else
  echo "❌ Some skill-gate tests failed"
  exit 1
fi

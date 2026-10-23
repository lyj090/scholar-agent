#!/usr/bin/env bash
# Build the public tree from the private working branch.
#
# `main` is the development branch: full history, development logs, generated
# platform trees. `public` is a single squashed commit with no shared ancestor,
# containing only what is fit to publish.
#
#   bash scripts/deploy/build-public.sh --check     # report what would differ
#   bash scripts/deploy/build-public.sh             # rewrite the public branch
#   bash scripts/deploy/build-public.sh --push      # ...and push it
#
# The exclusion list is the whole point: it is the one place that decides what
# never leaves this machine. Everything else is copied verbatim from the working
# tree, so `public` cannot drift from `main` by a forgotten file.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

PUBLIC_BRANCH="public"
PUBLIC_REMOTE="public"
CHECK=false
PUSH=false

for arg in "$@"; do
  case "$arg" in
    --check) CHECK=true ;;
    --push)  PUSH=true ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# ── Exclusions ───────────────────────────────────────────────────────────────
# Paths (file or directory) that must never appear in the public tree.
# Keep this list short and justify every entry: an exclusion is a promise that
# the material is not needed to use or understand the project.
EXCLUDE=(
  # Development history: names a third-party plugin, its source paths, and one
  # machine's library. Kept privately as a record; not useful to a reader.
  "docs/devlog"

  # Machine-local runtime state and scratch output.
  ".ai"
  ".pytest_cache"
  ".ruff_cache"
)

# Patterns, matched against the whole relative path.
EXCLUDE_GLOBS=(
  "*.log"           # run logs, one machine's activity
  "*.local.yaml"    # machine-local config (also gitignored)
  "*.local.json"
  "*.local.yml"
  ".zotero_env"     # credential-bearing
  "shared/repo-path-index*"
)

say() { printf '%s\n' "$*"; }
die() { printf 'ERR %s\n' "$*" >&2; exit 1; }

git rev-parse --verify --quiet "$PUBLIC_BRANCH" >/dev/null \
  || die "branch $PUBLIC_BRANCH does not exist"

# Refuse to build from a dirty tree: the snapshot would silently include
# half-finished work, and the resulting commit is hard to relate to anything.
if [[ -n "$(git status --porcelain)" ]]; then
  die "working tree is dirty; commit or stash first"
fi

# ── Collect the file list ────────────────────────────────────────────────────
# Tracked files only. Untracked-but-ignored values (local config, logs) are
# excluded by construction, and anything else untracked is a mistake.
mapfile -t ALL < <(git ls-tree -r --name-only HEAD | sort)

selected=()
excluded=()
for path in "${ALL[@]}"; do
  drop=false
  for dir in "${EXCLUDE[@]}"; do
    [[ "$path" == "$dir" || "$path" == "$dir"/* ]] && drop=true
  done
  for glob in "${EXCLUDE_GLOBS[@]}"; do
    # shellcheck disable=SC2053  # intentional glob match
    [[ "$path" == $glob ]] && drop=true
  done
  if [[ "$drop" == true ]]; then excluded+=("$path"); else selected+=("$path"); fi
done

say "files: ${#selected[@]} included, ${#excluded[@]} excluded"
if [[ ${#excluded[@]} -gt 0 ]]; then
  say "excluded:"
  printf '  %s\n' "${excluded[@]}"
fi

if [[ "$CHECK" == true ]]; then
  # Compare against what the public branch currently contains.
  mapfile -t CURRENT < <(git ls-tree -r --name-only "$PUBLIC_BRANCH" | sort)
  say ""
  say "on $PUBLIC_BRANCH now: ${#CURRENT[@]} files"
  only_public=$(comm -13 <(printf '%s\n' "${selected[@]}" | sort) <(printf '%s\n' "${CURRENT[@]}" | sort) || true)
  only_local=$(comm -23 <(printf '%s\n' "${selected[@]}" | sort) <(printf '%s\n' "${CURRENT[@]}" | sort) || true)
  if [[ -n "$only_public" ]]; then
    say "present on $PUBLIC_BRANCH but not selected (would be removed):"
    printf '  %s\n' $only_public
  fi
  if [[ -n "$only_local" ]]; then
    say "selected but missing from $PUBLIC_BRANCH (would be added):"
    printf '  %s\n' $only_local
  fi
  [[ -z "$only_public" && -z "$only_local" ]] && say "tree contents match"
  say ""
  say "content differences are not shown; run without --check to rebuild"
  exit 0
fi

# ── Build the snapshot ───────────────────────────────────────────────────────
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Preserve the executable bit. `git show > file` loses it, which silently
# produced a public tree where deploy.sh and run.sh could not be run — the two
# files a visitor is most likely to try first.
for path in "${selected[@]}"; do
  mkdir -p "$tmp/$(dirname "$path")"
  git show "HEAD:$path" > "$tmp/$path"
  mode="$(git ls-tree HEAD -- "$path" | awk '{print $1}')"
  [[ "$mode" == "100755" ]] && chmod +x "$tmp/$path"
done

# Build the orphan commit in a scratch worktree, never in the working tree.
#
# This used to `git checkout --orphan` in place and `rm -rf` everything at the
# top level. Besides being unrecoverable if interrupted, that deleted the
# generated `*-skills/` trees — which are symlink targets for the skills already
# deployed into ~/.claude, ~/.codex, ~/.cursor, ~/.grok and ~/.dsh. Building the
# public branch therefore blanked every agent's skill catalog until the next
# `generate.sh`. A scratch worktree leaves the checkout, the generated trees and
# the refreshed deploy targets untouched.
scratch="$(mktemp -d)"
cleanup() { git worktree remove --force "$scratch" >/dev/null 2>&1 || rm -rf "$scratch"; rm -rf "$tmp"; }
trap cleanup EXIT

git worktree add --quiet --detach "$scratch" >/dev/null 2>&1
(
  cd "$scratch"
  git checkout --quiet --orphan "public-new"
  git rm -rq --cached . >/dev/null 2>&1 || true
  find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
  cp -a "$tmp/." .
  git add -A
  git -c user.name="$(git config user.name)" \
      -c user.email="$(git config user.email)" \
      commit --quiet -m "Scholar Agent — local-first literature workflows for AI coding agents"
)
new_sha="$(
  git -C "$scratch" rev-parse HEAD
)"
git branch -f "$PUBLIC_BRANCH" "$new_sha"

say ""
say "rebuilt $PUBLIC_BRANCH at $new_sha (${#selected[@]} files, 1 commit)"

if [[ "$PUSH" == true ]]; then
  git push --force "$PUBLIC_REMOTE" "$PUBLIC_BRANCH:$PUBLIC_BRANCH"
  say "pushed to $PUBLIC_REMOTE"
else
  say ""
  say "review, then push with:"
  say "  git push --force $PUBLIC_REMOTE $PUBLIC_BRANCH:$PUBLIC_BRANCH"
fi

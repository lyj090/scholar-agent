#!/usr/bin/env bash
set -euo pipefail
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh — Unified deploy entry for Codex / Cursor / Claude / Grok / DSH skills
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
SCRIPTS_DIR="$REPO_ROOT/scripts"
DEPLOY_DIR="$SCRIPTS_DIR/deploy"
DEPLOY_START_EPOCH="$(date +%s)"
export CODEX_CONFIG_HOME="${CODEX_CONFIG_HOME:-$REPO_ROOT}"
export DEPLOY_REPORT="$(mktemp)"
trap 'rm -f "$DEPLOY_REPORT"' EXIT

# ─── Colors & UI ─────────────────────────────────────────────────────────────
# ui.sh provides: header, banner_ai_workflows, progress_bar, summary_line,
# UI_LEVEL (full|quiet|silent), and auto-detect via $CLAUDE_AGENT / $CI / TTY.

source "$SCRIPTS_DIR/lib/ui.sh"

# ─── Args ────────────────────────────────────────────────────────────────────

# ─── Helper functions ────────────────────────────────────────────────────────

detect_shell() {
  local current_shell
  current_shell=$(basename "${SHELL:-bash}")
  if [[ "$current_shell" == "zsh" || -f "$HOME/.zshrc" ]]; then
    echo "zsh"
  else
    echo "bash"
  fi
}

get_shell_rc() {
  local shell_name="$1"
  [[ "$shell_name" == "zsh" ]] && echo "$HOME/.zshrc" || echo "$HOME/.bashrc"
}

check_tool() {
  command -v "$1" >/dev/null 2>&1
}

has_dir() {
  [[ -d "$1" ]]
}

# ─── Panel helpers (used by the banner, pre-flight prompt and summary) ───────
# Defined early so they are available at every call site; they read RUN_*/DRY_RUN
# at call time, which is always after argument parsing.

platform_chips() {
  local -a chips=()
  [[ "$RUN_CODEX"  == true ]] && chips+=("${C_GREEN}Codex${C_RESET}")
  [[ "$RUN_CURSOR" == true ]] && chips+=("${C_CYAN}Cursor${C_RESET}")
  [[ "$RUN_CLAUDE" == true ]] && chips+=("${C_YELLOW}Claude${C_RESET}")
  [[ "$RUN_GROK"   == true ]] && chips+=("${C_CYAN}Grok${C_RESET}")
  [[ "$RUN_DSH"    == true ]] && chips+=("${C_BLUE}DSH${C_RESET}")
  if [[ ${#chips[@]} -eq 0 ]]; then
    printf "${C_DIM}none detected${C_RESET}"
    return 0
  fi
  local out="" c
  for c in "${chips[@]}"; do out+="${C_BOLD}${c}${C_RESET}  "; done
  printf '%b' "${out%  }"
}

group_summary() {
  if [[ ${#USER_EXPLICIT_GROUPS[@]} -gt 0 ]]; then
    printf '%s' "${USER_EXPLICIT_GROUPS[*]}"
  elif [[ "$DEPLOY_FORCE_LITERATURE" == true ]]; then
    printf 'core + literature'
  elif [[ "$DEPLOY_NO_LITERATURE" == true ]]; then
    printf 'core'
  else
    printf 'core (+ literature if ready)'
  fi
}

mode_summary() {
  if [[ "$DRY_RUN" == true ]]; then
    printf "${C_YELLOW}dry-run${C_RESET}"
  elif [[ "$UNDEPLOY" == true ]]; then
    printf "${C_RED}undeploy${C_RESET}"
  else
    printf "${C_GREEN}deploy${C_RESET}"
  fi
}

# Draw a "│  key   value" row inside a panel.
panel_row() {
  printf "  ${C_DIM}│${C_RESET}  ${C_DIM}%-11s${C_RESET} %b\n" "$1" "$2"
}

# ─── Args ────────────────────────────────────────────────────────────────────

RUN_CODEX=false
RUN_CURSOR=false
RUN_CLAUDE=false
RUN_GROK=false
RUN_DSH=false
# The `*-skills/` trees are build output and are not tracked, so generating
# them is the default: a fresh clone must be deployable without a prior step.
RUN_GENERATE=true
# Whether --generate/--no-generate was actually typed. The default is "generate",
# but --undeploy must not trip the conflict guard just because a default is on.
GENERATE_EXPLICIT=false
UNDEPLOY=false
RUN_SHELL_ENV=false
# Opt-in: the permission hook records the text of commands that trigger a
# prompt. Recording a user's shell history is not a default.
RUN_PERMISSION_HOOK=false
AUTO_DETECT=false
INTERACTIVE=false
LIST_GROUPS=false
DRY_RUN=false
AUTO_CONFIRM=false
CLI_QUIET=""
CLI_SILENT=""
TARGET_DIR=""
PLATFORM_SELECTED=false
CODEX_ARGS=()
CURSOR_ARGS=()
CLAUDE_ARGS=()
GROK_ARGS=()
DSH_ARGS=()
GROUP_ARGS=()
USER_EXPLICIT_GROUPS=()
DEPLOY_NO_LITERATURE=false
DEPLOY_FORCE_LITERATURE=false

usage() {
  cat <<EOF
${C_BOLD}Usage:${C_RESET} bash deploy.sh [OPTIONS]

  ${C_DIM}(no args)${C_RESET}             Auto-detect and deploy installed platforms
  -g, --generate        Generate platform-specific skills from skills/ single-source before deploy
  --all                 Force deploy to all platforms (Codex+Cursor+Claude+Grok+DSH)
  --codex               Only Codex (~/.codex/skills)
  --cursor              Only Cursor (~/.cursor/skills-cursor)
  --claude              Only Claude slash commands
  --grok                Only Grok skills (~/.grok/skills)
  --dsh                 Only DeepSeek Harness skills (~/.dsh/skills)
  --undeploy            Remove only managed skills, configs, and links
  --group <name>        Deploy only skills in specified group (repeatable)
                        Groups: core, literature
  -i, --interactive     Interactively select which groups to deploy
  --list-groups         Show available groups and their skills, then exit
  --dry-run             Preview what would be deployed without making changes
  --target <dir>        Deploy to specific repository path (overrides global/home)
  --no-generate         Skip regenerating *-skills/ (they are not tracked, so
                        their absence is normally an error, not a shortcut)
  --shell-env           Include the machine-specific Shell Environment section
                        (off by default: it describes one host, not yours)
  --no-shell-env        Skip it (default)
  --permission-hook     Also install the permission-counter hook (records the
                        text of commands that trigger a permission prompt)
  --no-literature       Never auto-deploy literature group (even if paths ready)
  --zotero              Force include literature group (alias: --group literature)
  --no-zotero           Alias for --no-literature
  -q, --quiet           Hide banner & per-skill log; keep section headers + summary
                        (auto-enabled when stdout is piped)
      --silent          Errors only, then a single-line OK/FAIL at the end
      --agent           Alias for --silent (auto-enabled when \$CLAUDE_AGENT or \$CI is set)
  -y, --yes             Skip the pre-deploy Enter-to-confirm prompt
  -h, --help            Show this help

${C_DIM}Groups (from shared/config/skill-groups.yaml):
  core        — research
  literature  — zotero-paper-to-obsidian, zotero-inbox-to-library,
                obsidian-zotero-tag-governance

Default (no --group): core + literature; literature auto-skips skills whose
  paths (obsidian vault / zotero cache) are not ready on this machine.
  See README.md § Enabling the literature flow

${C_DIM}Env: CODEX_CONFIG_HOME, CODEX_HOME, CURSOR_SKILLS_HOME, CLAUDE_COMMANDS_HOME, GROK_SKILLS_HOME, DSH_HOME, DSH_SKILLS_HOME${C_RESET}
EOF
}

if [[ $# -eq 0 ]]; then
  AUTO_DETECT=true
else
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -g|--generate)  RUN_GENERATE=true; GENERATE_EXPLICIT=true; shift ;;
      --no-generate)  RUN_GENERATE=false; GENERATE_EXPLICIT=true; shift ;;
      --all)          RUN_CODEX=true; RUN_CURSOR=true; RUN_CLAUDE=true; RUN_GROK=true; RUN_DSH=true; PLATFORM_SELECTED=true; shift ;;
      --codex)        RUN_CODEX=true; PLATFORM_SELECTED=true; shift ;;
      --cursor)       RUN_CURSOR=true; PLATFORM_SELECTED=true; shift ;;
      --claude)       RUN_CLAUDE=true; PLATFORM_SELECTED=true; shift ;;
      --grok)         RUN_GROK=true; PLATFORM_SELECTED=true; shift ;;
      --dsh)          RUN_DSH=true; PLATFORM_SELECTED=true; shift ;;
      --shell-env)    RUN_SHELL_ENV=true; shift ;;
      --permission-hook) RUN_PERMISSION_HOOK=true; shift ;;
      --no-shell-env) RUN_SHELL_ENV=false; shift ;;
      --target)       TARGET_DIR="$2"; shift 2 ;;
      --group)        USER_EXPLICIT_GROUPS+=("$2"); GROUP_ARGS+=("--group" "$2")
                      [[ "$2" == "literature" ]] && DEPLOY_FORCE_LITERATURE=true
                      shift 2 ;;
      -i|--interactive) INTERACTIVE=true; shift ;;
      --list-groups)  LIST_GROUPS=true; shift ;;
      --dry-run)      DRY_RUN=true; shift ;;
      --undeploy|--unploy|undeploy) UNDEPLOY=true; shift ;;
      --no-literature) DEPLOY_NO_LITERATURE=true; shift ;;
      --no-zotero)    DEPLOY_NO_LITERATURE=true; shift ;;
      --zotero)       DEPLOY_FORCE_LITERATURE=true; shift ;;
      -q|--quiet)     CLI_QUIET=1; shift ;;
      --silent|--agent) CLI_SILENT=1; shift ;;
      -y|--yes)       AUTO_CONFIRM=true; shift ;;
      -h|--help)      usage; exit 0 ;;
      *)              err "Unknown argument: $1"; echo "See: bash $0 --help" >&2; exit 1 ;;
    esac
  done
fi

if [[ "$UNDEPLOY" == true ]]; then
  # Only an *explicit* --generate conflicts; generation is the default now, and
  # an undeploy that refuses to run because of a default is a broken command.
  if [[ "$GENERATE_EXPLICIT" == true && "$RUN_GENERATE" == true ]] \
     || [[ "$INTERACTIVE" == true || "$LIST_GROUPS" == true ]]; then
    err "--undeploy cannot be combined with --generate, --interactive, or --list-groups"
    exit 2
  fi
  RUN_GENERATE=false
  if [[ "$PLATFORM_SELECTED" == false ]]; then
    RUN_CODEX=true; RUN_CURSOR=true; RUN_CLAUDE=true; RUN_GROK=true; RUN_DSH=true
  fi
fi

# CLI flags > env vars > auto-detect. Bootstrap children inherit via export.
apply_ui_overrides "$CLI_QUIET" "$CLI_SILENT"
if ui_is_quiet;  then export DEPLOY_QUIET=1; fi
if ui_is_silent; then export DEPLOY_SILENT=1; fi

# ─── Handle --list-groups ───────────────────────────────────────────────────

source "$SCRIPTS_DIR/lib/skill_groups.sh"

if [[ "$LIST_GROUPS" == true ]]; then
  printf "\n${C_BOLD}Available workflow groups:${C_RESET}\n\n"
  print_groups_table
  printf "\n  ${C_DIM}Default: core (literature when paths ready)${C_RESET}\n"
  printf "  ${C_DIM}Usage:   bash deploy.sh --group core --group literature${C_RESET}\n"
  printf "  ${C_DIM}         bash deploy.sh --interactive${C_RESET}\n"
  printf "  ${C_DIM}Literature setup: README.md § Enabling the literature flow${C_RESET}\n\n"
  exit 0
fi

# ─── Handle --interactive ───────────────────────────────────────────────────

if [[ "$INTERACTIVE" == true ]]; then
  if [[ ! -t 0 ]]; then
    err "Interactive mode requires a terminal (stdin is not a tty)"
    err "Use --group <name> for non-interactive group selection"
    exit 1
  fi
  interactive_select_groups
  if [[ ${#INTERACTIVE_GROUPS[@]} -eq 0 ]]; then
    warn "No groups selected. Nothing to deploy."
    exit 0
  fi
  for g in "${INTERACTIVE_GROUPS[@]}"; do
    GROUP_ARGS+=("--group" "$g")
    USER_EXPLICIT_GROUPS+=("$g")
    [[ "$g" == "literature" ]] && DEPLOY_FORCE_LITERATURE=true
  done
  # Auto-detect platforms when interactive
  AUTO_DETECT=true
fi

# ─── Handle --dry-run (set env var for bootstrap scripts) ───────────────────

if [[ "$DRY_RUN" == true ]]; then
  export DEPLOY_DRY_RUN=1
fi

if [ -z "$TARGET_DIR" ]; then
  if [[ "$AUTO_DETECT" == true ]]; then
    has_dir "$HOME/.codex" && RUN_CODEX=true
    has_dir "$HOME/.cursor" && RUN_CURSOR=true
    (has_dir "$HOME/.claude" || has_dir "$HOME/.claude-internal") && RUN_CLAUDE=true
    (has_dir "$HOME/.grok" || check_tool grok) && RUN_GROK=true
    (has_dir "${DSH_HOME:-$HOME/.dsh}" || check_tool dsh) && RUN_DSH=true
  fi
else
  # Resolve target to absolute path
  TARGET_DIR="$(cd "$TARGET_DIR" 2>/dev/null && pwd || echo "$TARGET_DIR")"
  CODEX_ARGS+=("--target" "$TARGET_DIR")
  CURSOR_ARGS+=("--target" "$TARGET_DIR/.cursor/rules")
  CLAUDE_ARGS+=("--target" "$TARGET_DIR")
  GROK_ARGS+=("--target" "$TARGET_DIR")
  # DSH normally writes only to the user root ($DSH_HOME/skills). Under
  # --target that would write outside the directory the caller named: a test
  # deploy into a scratch tree silently repointed the user's real skill links at
  # it, and the matching --undeploy would then remove them. DSH_SKILLS_HOME is
  # honoured by the DSH bootstrap, so point it inside the target and keep the
  # deployment self-contained.
  export DSH_SKILLS_HOME="$TARGET_DIR/.dsh/skills"
  # Remove --global if deploying to a specific target
fi

# ─── Deploy skills ───────────────────────────────────────────────────────────

banner_ai_workflows
if ui_is_full; then
  printf "\n  ${C_DIM}repo${C_RESET}  ${C_BOLD}%s${C_RESET}\n" "$REPO_ROOT"
  printf "  ${C_DIM}mode${C_RESET}  %b  ${C_DIM}│  groups${C_RESET}  ${C_BOLD}%s${C_RESET}\n" \
    "$(mode_summary)" "$(group_summary)"
  if [[ "$DRY_RUN" == true ]]; then
    printf "\n  ${C_BOLD}${C_YELLOW}⚡ DRY-RUN${C_RESET} ${C_DIM}— preview only, nothing will be written${C_RESET}\n"
  fi
fi

# ─── Pre-deploy confirmation ────────────────────────────────────────────────
# Pause once before touching the filesystem. Enter (default = Y) confirms;
# typing 'n' aborts. Auto-skipped under -y, --dry-run, --quiet, --silent,
# or when stdin is not a TTY (piped / CI).

confirm_or_abort() {
  if [[ "$AUTO_CONFIRM" == true ]] \
     || [[ "$DRY_RUN" == true ]] \
     || ! ui_is_full \
     || [[ ! -t 0 ]]; then
    return 0
  fi

  local tw rule_top rule_bot
  tw="$(ui_term_width)"
  # Top: "  ┌─ Pre-flight <rule_top>"  → 16 + rule_top
  # Bottom: "  └<rule_bot>"              →  3 + rule_bot  (keep both equal)
  rule_top="$(printf '─%.0s' $(seq 1 $(( tw - 16 ))))"
  rule_bot="$(printf '─%.0s' $(seq 1 $(( tw - 3 ))))"

  printf "\n  ${C_DIM}┌─${C_RESET} ${C_BOLD}Pre-flight${C_RESET} ${C_DIM}%s${C_RESET}\n" "$rule_top"
  panel_row "platforms" "$(platform_chips)"
  panel_row "groups"    "${C_BOLD}$(group_summary)${C_RESET}"
  panel_row "source"    "${C_DIM}${REPO_ROOT}${C_RESET}"
  panel_row "mode"      "$(mode_summary)"
  if [[ -n "$TARGET_DIR" ]]; then
    panel_row "target"  "${C_DIM}${TARGET_DIR}${C_RESET}"
  fi
  printf "  ${C_DIM}└%s${C_RESET}\n" "$rule_bot"

  printf "\n  ${C_BOLD}${C_GREEN}↩ Enter${C_RESET}${C_DIM} to continue${C_RESET}   ${C_BOLD}${C_RED}n${C_RESET}${C_DIM} to abort${C_RESET}   ${C_GREEN}❯${C_RESET} "
  local reply
  read -r reply
  case "$reply" in
    ""|y|Y|yes|Yes|YES) return 0 ;;
    n|N|no|No|NO)        err "Aborted by user."; exit 130 ;;
    *)                   err "Unrecognized input '$reply'; aborting for safety."; exit 130 ;;
  esac
}

undeploy_home_symlink() {
  local link="$HOME/scholar-agent"
  local target="$REPO_ROOT"
  [[ -L "$link" ]] || return 0
  [[ "$(readlink -f "$link" 2>/dev/null || readlink "$link")" == "$(readlink -f "$target")" ]] || return 0

  if [[ "$DRY_RUN" == true ]]; then
    info "would remove $link"
  else
    rm -f "$link"
    ok "removed $link"
  fi
}

run_undeploy() {
  header "🧹 UNDEPLOY"

  if [[ "$RUN_CODEX" == true ]]; then
    bash "$DEPLOY_DIR/bootstrap-codex-skills.sh" --undeploy "${CODEX_ARGS[@]}"
  fi

  if [[ "$RUN_CURSOR" == true ]]; then
    bash "$DEPLOY_DIR/bootstrap-cursor-skills.sh" --undeploy "${CURSOR_ARGS[@]}"
    if [[ -z "$TARGET_DIR" ]]; then
      bash "$DEPLOY_DIR/bootstrap-cursor-rules.sh" --undeploy
    fi
  fi

  if [[ "$RUN_CLAUDE" == true ]]; then
    bash "$DEPLOY_DIR/bootstrap-claude-skills.sh" --undeploy "${CLAUDE_ARGS[@]}"
  fi


  if [[ "$RUN_GROK" == true ]]; then
    bash "$DEPLOY_DIR/bootstrap-grok-skills.sh" --undeploy "${GROK_ARGS[@]}"
  fi

  if [[ "$RUN_DSH" == true ]]; then
    bash "$DEPLOY_DIR/bootstrap-dsh-skills.sh" --undeploy "${DSH_ARGS[@]}"
  fi

  # Project-target undeploy only removes project skill links. Global config is
  # touched only when no --target was supplied.
  if [[ -z "$TARGET_DIR" ]]; then
    source "$SCRIPTS_DIR/lib/marker-deploy.sh"
    [[ "$RUN_CLAUDE" == true ]] && marker_undeploy "$HOME/.claude/CLAUDE.md"
    [[ "$RUN_CLAUDE" == true ]] && marker_undeploy "$HOME/.claude-internal/CLAUDE.md"
    [[ "$RUN_DSH" == true ]] && marker_undeploy "${DSH_HOME:-$HOME/.dsh}/AGENTS.md"
    undeploy_home_symlink
  fi

  if ui_is_silent; then
    echo "undeploy: OK"
  else
    info "managed deployment removed; user files and modified targets were preserved"
  fi
}

confirm_or_abort

if [[ "$UNDEPLOY" == true ]]; then
  run_undeploy
  exit 0
fi

if [[ "$RUN_GENERATE" == true ]]; then
  if [[ "$DRY_RUN" == true ]]; then
    ui_is_silent || printf "  ${C_DIM}[dry-run] Would execute: bash %s/generate.sh${C_RESET}\n" "$DEPLOY_DIR"
  else
    ui_is_silent || printf "  ${C_BOLD}Generating skills from skills/ single source...${C_RESET}\n"
    bash "$DEPLOY_DIR/generate.sh"
  fi
fi

export DEPLOY_NO_LITERATURE DEPLOY_FORCE_LITERATURE

deploy_includes_literature() {
  if [[ "$DEPLOY_NO_LITERATURE" == true ]]; then
    return 1
  fi
  if [[ "$DEPLOY_FORCE_LITERATURE" == true ]]; then
    return 0
  fi
  local g
  for g in "${USER_EXPLICIT_GROUPS[@]}"; do
    [[ "$g" == "literature" ]] && return 0
  done
  # Auto literature: any platform with default groups
  if [[ ${#USER_EXPLICIT_GROUPS[@]} -eq 0 ]]; then
    is_auto_group_ready "literature" && return 0
  fi
  return 1
}

# ─── Ensure ~/scholar-agent symlink ──────────────────────────────────────────
# All skills hardcode `~/scholar-agent/...` paths. If the repo lives elsewhere
# (e.g. ~/projects/scholar-agent on a new machine), those paths 404 unless we expose
# the canonical entry point via a symlink. This is idempotent — running deploy
# from any clone location will repoint the link to that clone.
ensure_home_symlink() {
  local target="$REPO_ROOT"
  local link="$HOME/scholar-agent"

  # Already correct (either as a symlink or as the original directory) → nothing to do.
  if [[ -e "$link" && "$(readlink -f "$link")" == "$(readlink -f "$target")" ]]; then
    return 0
  fi

  if [[ "$DRY_RUN" == true ]]; then
    if [[ -e "$link" || -L "$link" ]]; then
      info "would repoint $link → $target"
    else
      info "would create $link → $target"
    fi
    return 0
  fi

  # Refuse to clobber a real directory — that would be the user's actual data.
  if [[ -d "$link" && ! -L "$link" ]]; then
    err "$link is a real directory, not a symlink — refusing to overwrite."
    err "Move or delete it manually, then re-run deploy."
    exit 1
  fi

  ln -sfn "$target" "$link"
  ok "$link → $target"
}

# Only when deploying to the real user scope. `--target` means "put everything in
# this directory instead of $HOME" — repointing the global entry point at a
# scratch directory would silently break every skill that hardcodes
# ~/scholar-agent, and a later `--undeploy` from that directory would then
# remove the user's real deployment.
if [[ -z "$TARGET_DIR" ]]; then
  ensure_home_symlink
fi

# Sync the cross-device allowlist baseline (shared/config/allowlist-common.json) into
# ~/.claude/settings.json and preserve any local-only rules. The PermissionRequest
# hook is installed only with --permission-hook: it records command text, so it
# must not be a silent default. See shared/allowlist-common.md.
if [[ "$RUN_CLAUDE" == true ]] && has_dir "$HOME/.claude"; then
  HOOK_ARG=""
  [[ "$RUN_PERMISSION_HOOK" == true ]] && HOOK_ARG="--with-hook"
  if [[ "$DRY_RUN" == true ]]; then
    bash "$SCRIPTS_DIR/allowlist/sync-allowlist.sh" --dry-run $HOOK_ARG || warn "sync-allowlist dry-run failed (non-fatal)"
  else
    bash "$SCRIPTS_DIR/allowlist/sync-allowlist.sh" $HOOK_ARG || warn "sync-allowlist failed (non-fatal)"
  fi
  # Non-fatal redundancy reminder: count system + project rules that could be
  # pruned, so allowlists don't silently bloat. Run `audit-allowlist.sh fix` /
  # `project` / `promote` to clean. Hint only — never mutates during deploy.
  if [[ "${UI_LEVEL:-full}" != "silent" ]]; then
    sys_red="$(bash "$SCRIPTS_DIR/allowlist/audit-allowlist.sh" fix 2>/dev/null | grep -cE '^    - ' || true)"
    proj_red="$(bash "$SCRIPTS_DIR/allowlist/audit-allowlist.sh" project 2>/dev/null | grep -cE '^    - ' || true)"
    if [[ "${sys_red:-0}" -gt 0 || "${proj_red:-0}" -gt 0 ]]; then
      warn "allowlist redundancy: ~$sys_red system + ~$proj_red project rules prunable — run: audit-allowlist.sh fix --apply / project --apply"
    fi
  fi
fi

if [[ "$RUN_CODEX" == true ]]; then
  header "🧠 Codex" "$C_GREEN"
  bash "$DEPLOY_DIR/bootstrap-codex-skills.sh" "${CODEX_ARGS[@]}" "${GROUP_ARGS[@]}"
fi

if [[ "$RUN_CURSOR" == true ]]; then
  header "🖱️ Cursor" "$C_CYAN"
  bash "$DEPLOY_DIR/bootstrap-cursor-skills.sh" "${CURSOR_ARGS[@]}" "${GROUP_ARGS[@]}"
  # Deploy global rules (.mdc) to ~/.cursor/rules/
  bash "$DEPLOY_DIR/bootstrap-cursor-rules.sh"
fi

if [[ "$RUN_CLAUDE" == true ]]; then
  header "🎭 Claude Code" "$C_YELLOW"
  bash "$DEPLOY_DIR/bootstrap-claude-skills.sh" "${CLAUDE_ARGS[@]}" "${GROUP_ARGS[@]}"
fi


if [[ "$RUN_GROK" == true ]]; then
  header "⚡ Grok" "$C_CYAN"
  bash "$DEPLOY_DIR/bootstrap-grok-skills.sh" "${GROK_ARGS[@]}" "${GROUP_ARGS[@]}"
fi

if [[ "$RUN_DSH" == true ]]; then
  header "🐋 DeepSeek Harness" "$C_BLUE"
  bash "$DEPLOY_DIR/bootstrap-dsh-skills.sh" "${DSH_ARGS[@]}" "${GROUP_ARGS[@]}"
fi

# ─── Global config ───────────────────────────────────────────────────────────

if [[ "$DRY_RUN" == true ]]; then
  header "Global Config (skipped: --dry-run)"
  info "Dry-run mode: skipping global config sync"
else
  header "Global Config"
  source "$SCRIPTS_DIR/lib/marker-deploy.sh"

  # Global Claude instructions land in the single public ~/.claude/CLAUDE.md.
  # claude-internal is no longer a deploy target: its former managed content
  # (CLAUDE.md block, slash-command symlinks, allowlist, hook) is removed by
  # `deploy.sh --undeploy`, leaving any hand-written internal notes untouched.
  # The shell-environment section is opt-in (--shell-env): it describes one
  # machine's aliases and shell, so merging it by default writes someone else's
  # environment into the reader's global instructions.
  GLOBAL_SRC="$REPO_ROOT/global-config/claude-global.md"
  GLOBAL_DST="$HOME/.claude/CLAUDE.md"
  if [[ -f "$GLOBAL_SRC" ]] && { [[ "$AUTO_DETECT" == false ]] || has_dir "$(dirname "$GLOBAL_DST")"; }; then
    if [[ "$RUN_SHELL_ENV" == true ]]; then
      SHELL_ENV_SRC="$REPO_ROOT/global-config/shell-env.md"
      if [[ -f "$SHELL_ENV_SRC" ]]; then
        USER_SHELL=$(detect_shell)
        SHELL_RC=$(get_shell_rc "$USER_SHELL")

        MERGED_TMP="$(mktemp)"
        cat "$GLOBAL_SRC" > "$MERGED_TMP"
        printf '\n' >> "$MERGED_TMP"
        # Dynamically patch shell name and config path
        sed "s/zsh/$USER_SHELL/g; s|~/.zshrc|$SHELL_RC|g" "$SHELL_ENV_SRC" >> "$MERGED_TMP"

        marker_deploy "$MERGED_TMP" "$GLOBAL_DST"
        rm "$MERGED_TMP"
        ok "global CLAUDE.md ← claude-global + shell-env ($USER_SHELL)"
      else
        marker_deploy "$GLOBAL_SRC" "$GLOBAL_DST"
        warn "global CLAUDE.md ← claude-global (shell-env not found)"
      fi
    else
      marker_deploy "$GLOBAL_SRC" "$GLOBAL_DST"
      ok "global CLAUDE.md ← $(basename "$GLOBAL_SRC")"
    fi
  fi

  # DSH reads its user-global baseline from $DSH_HOME/AGENTS.md, which lies on
  # none of the paths above — without this row the global rules reach Claude Code
  # but silently skip every DSH session.
  DSH_GLOBAL_SRC="$REPO_ROOT/global-config/dsh-global.md"
  DSH_GLOBAL_DST="${DSH_HOME:-$HOME/.dsh}/AGENTS.md"
  if [[ -f "$DSH_GLOBAL_SRC" ]] && { [[ "$AUTO_DETECT" == false ]] || has_dir "$(dirname "$DSH_GLOBAL_DST")"; }; then
    marker_deploy "$DSH_GLOBAL_SRC" "$DSH_GLOBAL_DST"
    ok "global DSH AGENTS.md ← $(basename "$DSH_GLOBAL_SRC")"
  fi

  # Cursor rules are owned by bootstrap-cursor-rules.sh above. Keeping one
  # owner avoids appending the same pre-made MDC file a second time here.
fi

# ─── Path validation ─────────────────────────────────────────────────────────

if [[ "$DRY_RUN" == true ]]; then
  header "Path Validation (skipped: --dry-run)"
  info "Dry-run mode: skipping path validation"
else
  header "Path Validation"

  # Never materialize a config file as a side effect of deploying: a `cp` of the
  # template parses to an empty mapping, which makes every later check pass while
  # nothing is configured. Its absence is not an error either — the file is an
  # optional override, and a machine whose vault and Zotero are auto-discovered
  # needs none. Readiness is decided by the resolver, below.

  if deploy_includes_literature; then
    if python3 "$DEPLOY_DIR/check_paths.py" --strict-literature; then
      ok "路径校验通过 (literature)"
    else
      warn "literature 路径未完全就绪 — 部分 skill 可能已 SKIP"
      printf "  ${C_DIM}python3 scripts/deploy/check_paths.py --skill zotero-paper-to-obsidian --setup-hints${C_RESET}\n"
    fi
  else
    if python3 "$DEPLOY_DIR/check_paths.py" --strict-core; then
      ok "路径校验通过 (core)"
    else
      # Report, never repair. Silently rewriting an unconfigured machine's
      # paths (the old `setup_paths.py --apply --yes --force`) hides a real
      # problem and mutates the tree during what should be a read-only check.
      warn "obsidian.vault 未配置 — core skills 可用，literature 需配置 paths"
      printf "  ${C_DIM}python3 scripts/deploy/setup_paths.py --apply${C_RESET}\n"
      printf "  ${C_DIM}README.md § Enabling the literature flow${C_RESET}\n"
    fi
    if [[ "$DEPLOY_NO_LITERATURE" != true ]] && ! is_auto_group_ready "literature"; then
      print_literature_setup_hint
    fi
  fi
fi

# ─── Summary ─────────────────────────────────────────────────────────────────

# Silent mode: collapse the whole summary into one line and exit early.
if ui_is_silent; then
  if [[ -f "${DEPLOY_REPORT:-}" ]]; then
    python3 - "${DEPLOY_REPORT}" <<'PYTHON'
import sys, collections
report = sys.argv[1]
counts = collections.defaultdict(lambda: [0, 0])  # agent -> [linked, total]
with open(report) as f:
    for line in f:
        parts = line.strip().split('|')
        if len(parts) != 3: continue
        agent, _skill, status = parts
        counts[agent][1] += 1
        if status == "OK":
            counts[agent][0] += 1
parts = [f"{a} {l}/{t}" for a, (l, t) in sorted(counts.items()) if t > 0]
print("deploy: OK (" + ", ".join(parts) + ")" if parts else "deploy: OK")
PYTHON
    rm "$DEPLOY_REPORT"
  else
    echo "deploy: OK"
  fi
  exit 0
fi

header "🏁 DEPLOYMENT SUMMARY" "$C_GREEN"

# Render the skill × platform matrix. Prints nothing when no skill was touched,
# so a no-op run never shows an empty table.
DEPLOY_TABLE=""
if [[ -f "${DEPLOY_REPORT:-}" ]]; then
  DEPLOY_TABLE="$(python3 - "${DEPLOY_REPORT}" <<'PYTHON'
import sys

report_file = sys.argv[1]
agents = ["Codex", "Cursor", "Claude", "Grok", "DSH"]
skills = {}

with open(report_file, "r") as f:
    for line in f:
        parts = line.strip().split("|")
        if len(parts) == 3:
            agent, skill, status = parts
            skills.setdefault(skill, {a: "-" for a in agents})
            skills[skill][agent] = "OK" if status == "OK" else "--"

if not skills:
    sys.exit(0)

tty = sys.stdout.isatty()
G = "\033[32m" if tty else ""
D = "\033[2m" if tty else ""
R = "\033[0m" if tty else ""

skw = max(12, min(32, max(len(s) for s in skills) + 2))
widths = [skw] + [8] * len(agents)


def rule(l, m, r):
    return l + m.join("─" * (w + 2) for w in widths) + r


def row(cells):
    # "│ cell │ cell │" — one leading mark, then " cell │" per column.
    return "│" + "".join(f" {c:^{w}} │" for c, w in zip(cells, widths))


def fit(s, w):
    return s if len(s) <= w else s[: w - 1] + "…"


print(rule("┌", "┬", "┐"))
print(row(["Skill"] + agents))
print(rule("├", "┼", "┤"))
for skill in sorted(skills):
    cells = [fit(skill, skw)]
    for a in agents:
        ok = skills[skill][a] == "OK"
        ch = "✔" if ok else "·"
        # Centre the plain glyph first, then colourise — otherwise the ANSI
        # bytes would count toward the padding and break column alignment.
        cells.append(f"{ch:^{8}}".replace(ch, f"{G}{ch}{R}" if ok else f"{D}{ch}{R}"))
    print(row(cells))
print(rule("└", "┴", "┘"))
print(f"  {D}✔ linked   · skipped / excluded{R}")
PYTHON
)"
  rm -f "$DEPLOY_REPORT"
fi

if [[ -n "$DEPLOY_TABLE" ]]; then
  printf '%s\n' "$DEPLOY_TABLE"
else
  printf "  ${C_DIM}No skill links were created or removed in this run.${C_RESET}\n"
fi

[[ "$AUTO_DETECT" == true ]] && printf "\n${C_DIM}  (platforms auto-detected)${C_RESET}\n"

printf "\n"
kv_row "CODEX"  "$(status_pill "$RUN_CODEX"  "$C_GREEN")"
kv_row "CURSOR" "$(status_pill "$RUN_CURSOR" "$C_CYAN")"
kv_row "CLAUDE" "$(status_pill "$RUN_CLAUDE" "$C_YELLOW")"
kv_row "GROK"   "$(status_pill "$RUN_GROK"   "$C_CYAN")"
kv_row "DSH"    "$(status_pill "$RUN_DSH"    "$C_BLUE")"

DEPLOY_ELAPSED=$(( $(date +%s) - DEPLOY_START_EPOCH ))
printf "\n${C_DIM}  Completed in %ss${C_RESET}\n" "$DEPLOY_ELAPSED"

printf "\n${C_BOLD}  Next steps${C_RESET}\n"
printf "  ${C_DIM}%-16s${C_RESET} %s\n" "Configuration" "shared/paths.local.yaml"
printf "  ${C_DIM}%-16s${C_RESET} %s\n" "Setup paths" "python3 scripts/deploy/setup_paths.py --apply"
printf "  ${C_DIM}%-16s${C_RESET} %s\n" "Validate skill" "python3 scripts/deploy/check_paths.py --skill <name>"
printf "  ${C_DIM}%-16s${C_RESET} %s\n" "Literature" "README.md § Enabling the literature flow"
printf "\n"

#!/usr/bin/env bash
set -euo pipefail
# skill-gate.sh — Unified runtime gate for scholar-agent skills
# Compact stdout: ≤6 KEY=value lines (READ= RUN= ERR= MSG= FIX= OK=)
#
# Usage:
#   skill-gate.sh <skill-name> [--root PATH] [--intent research|trace] [--verbose]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
CHECK_PATHS="$SCRIPTS_DIR/deploy/check_paths.py"
RESEARCH="$SCRIPT_DIR/research.sh"
LITERATURE_DIR="$SCRIPTS_DIR/literature"
# shellcheck source=../lib/skill-out.sh
source "$SCRIPTS_DIR/lib/skill-out.sh"

SKILL="${1:-}"
shift || true

ROOT="."
INTENT="research"
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --intent) INTENT="$2"; shift 2 ;;
    --verbose) VERBOSE=true; shift ;;
    -h|--help)
      echo "Usage: skill-gate.sh <skill> [--root PATH] [--intent research|trace] [--verbose]"
      echo "Output: OK= ERR= READ= RUN= READ_AT_* MSG= FIX= (≤${SKILL_OUT_MAX_LINES} lines)"
      exit 0
      ;;
    *) skill_out_err "unknown_arg" "arg=$1"; exit 1 ;;
  esac
done

[[ -n "$SKILL" ]] || { skill_out_err "missing_skill"; echo "MSG=skill-gate.sh <name> [--root .]"; exit 1; }

emit_read() {
  local rel="$1"
  local abs="$REPO_ROOT/$rel"
  if [[ -f "$abs" ]]; then
    echo "READ=$abs"
  else
    echo "ERR spec_missing path=$abs"
    echo "MSG=sync scholar-agent shared specs"
    exit 1
  fi
}

emit_read_verbose() {
  if [[ "$VERBOSE" == true ]]; then
    emit_read "$1"
  fi
}

gate_literature() {
  local spec_rel="$1" run_line="${2:-}"
  local path_out
  if ! path_out=$(python3 "$CHECK_PATHS" --skill "$SKILL" --compact 2>/dev/null); then
    echo "$path_out"
    exit 1
  fi
  echo "$path_out"
  [[ -n "$run_line" ]] && echo "RUN=$run_line"
  # The spec is announced, never dumped. Progressive disclosure only works if
  # the agent can see that a spec exists — a deferral it cannot discover is a
  # dead end, so the compact default names the file and --verbose emits the
  # absolute READ= path.
  if [[ "$VERBOSE" == true ]]; then
    emit_read "$spec_rel"
  else
    echo "SPEC=$spec_rel   # read when the RUN= output is not enough"
  fi
}

case "$SKILL" in
  research)
    exec bash "$RESEARCH" gate --root "$ROOT" --intent "$INTENT"
    ;;

  paper-lookup)
    # Core skill: available on every machine, no literature paths required.
    # The preflight itself degrades gracefully when the corpus is absent, so
    # this gate never blocks on path checks.
    #
    # Budget: <=6 lines total (SKILL_OUT_MAX_LINES, enforced by
    # test-skill-consistency.py). The verbs are grouped into a read line and a
    # write line so that every capability stays discoverable — T12c asserts none
    # of them silently disappear — without spending a line per verb.
    #
    # Placeholders are double-quoted: the agent copies these lines verbatim into
    # a shell, and an unquoted `<REF>` is a *redirect*. `<topic` (no closing >)
    # silently truncates a file of that name to zero bytes.
    echo "OK skill=paper-lookup"
    echo "RUN=python3 $LITERATURE_DIR/zotero_cli.py paper-status \"<REF>\""
    echo "RUN_READ=python3 $LITERATURE_DIR/zotero_cli.py mineru {outline|read \"<REF>\" --section N|grep \"<topic>\"}"
    echo "RUN_FIX=python3 $LITERATURE_DIR/zotero_cli.py {mineru-parse \"<REF>\"|mineru-push \"<REF>\"|write status}"
    [[ "$VERBOSE" == true ]] && emit_read "shared/protocols/literature-preflight.md"
    echo "MSG=LOCAL_READY -> read local, do not fetch"
    ;;

  arxiv-to-library)
    gate_literature "shared/specs/arxiv-to-library-ai.md" \
      "python3 $LITERATURE_DIR/arxiv_fetch.py find --help"
    echo "RUN_WRITE=python3 $LITERATURE_DIR/zotero_cli.py write status"
    echo "MSG=preview then --yes; everything is revertible via 'write undo'"
    ;;

  zotero-paper-to-obsidian)
    gate_literature "shared/specs/zotero-paper-to-obsidian-ai.md" \
      "python3 $LITERATURE_DIR/zotero_cli.py mineru resolve <REF> --json"
    # Step 3 is where the note gets written, which is not what the caller is
    # doing now (step 1 is resolving a cache). Emitting an absolute path for it
    # at gate time invites reading the writing rules before there is anything to
    # write, which is exactly the eager load this gate exists to prevent.
    [[ "$VERBOSE" == true ]] && echo "READ_AT_STEP3=$REPO_ROOT/shared/specs/zotero-paper-to-obsidian-sop-ai.md"
    echo "MSG=resolve exact cache; verify every figure against the PDF page"
    ;;

  zotero-inbox-to-library)
    gate_literature "shared/specs/zotero-inbox-to-library-ai.md" \
      "python3 $LITERATURE_DIR/zotero_cli.py prepare-triage --json"
    echo "MSG=review the table, confirm, then 'write triage --plan'"
    ;;

  obsidian-zotero-tag-governance)
    gate_literature "shared/specs/obsidian-zotero-tag-governance-ai.md" \
      "python3 $LITERATURE_DIR/zotero_cli.py audit-tags --json"
    tax_abs="$REPO_ROOT/shared/config/tag-taxonomy-rules.yaml"
    [[ "$VERBOSE" == true && -f "$tax_abs" ]] && echo "READ_WITH=$tax_abs"
    echo "MSG=plan -> review -> apply --plan; unknown tags block"
    ;;

  *)
    echo "ERR unknown_skill skill=$SKILL"
    echo "MSG=skill-gate.sh --help"
    exit 1
    ;;
esac

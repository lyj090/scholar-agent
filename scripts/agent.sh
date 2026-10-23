#!/usr/bin/env bash
# Compact, repeatable entry point for repository-agent operations.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: bash scripts/agent.sh <command> [args]

Commands:
  list                         Show skill groups from shared config
  gate <skill> [gate args]     Resolve the next reads/runs for a skill
  audit [audit args]           Audit local agent instruction files
  check                        Run lint and portability checks into a report
  paths [check_paths args]     Check configured machine-local paths
  doctor                       Report whether this install is healthy (writes nothing)
  setup-paths [args]           Discover or apply machine-local paths
  deploy [deploy args]         Forward to the repository deploy entry
  undeploy [deploy args]       Remove managed deployment artifacts only
EOF
}

command_name="${1:-help}"
shift || true

case "$command_name" in
  help|-h|--help)
    usage
    ;;
  list)
    source "$SCRIPT_DIR/lib/ui.sh"
    source "$SCRIPT_DIR/lib/skill_groups.sh"
    print_groups_table
    ;;
  gate)
    [[ $# -gt 0 ]] || { echo "ERR missing skill" >&2; exit 2; }
    exec bash "$SCRIPT_DIR/skills/skill-gate.sh" "$@"
    ;;
  audit)
    exec python3 "$SCRIPT_DIR/agent-audit.py" "$@"
    ;;
  check)
    report_dir="$REPO_ROOT/.ai/reports"
    mkdir -p "$report_dir"
    report="$report_dir/agent-check-$(date +%Y%m%d-%H%M%S).log"
    status=0
    if ! bash "$SCRIPT_DIR/deploy/lint-skills.sh" >"$report" 2>&1; then
      status=1
    fi
    if ! bash "$SCRIPT_DIR/check-portability.sh" verify >>"$report" 2>&1; then
      status=1
    fi
    if [[ "$status" == "0" ]]; then
      printf 'CHECK=OK\nREPORT=%s\n' "$report"
    else
      printf 'CHECK=FAIL\nREPORT=%s\n' "$report"
      tail -5 "$report" >&2
      exit 1
    fi
    ;;
  paths)
    exec python3 "$SCRIPT_DIR/deploy/check_paths.py" "$@"
    ;;
  doctor)
    exec python3 "$SCRIPT_DIR/deploy/doctor.py" "$@"
    ;;
  setup-paths)
    exec python3 "$SCRIPT_DIR/deploy/setup_paths.py" "$@"
    ;;
  deploy)
    exec bash "$REPO_ROOT/deploy.sh" "$@"
    ;;
  undeploy)
    exec bash "$REPO_ROOT/deploy.sh" --undeploy "$@"
    ;;
  *)
    echo "ERR unknown command: $command_name" >&2
    usage >&2
    exit 2
    ;;
esac

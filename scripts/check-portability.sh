#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# check-portability.sh — 跨设备部署自检与清理
# ─────────────────────────────────────────────────────────────────────────────
# 仓库里残留的硬编码 `${USER_HOME}/...` 在 $HOME ≠ ${USER_HOME} 的机器上会让
# Claude Code 权限 glob 静默失效 → 反复弹权限提示。本脚本：
#
#   check    扫描 git-tracked 文件，按 CRITICAL / COSMETIC / UNMANAGED 分类报告
#   fix      把内置清单里的 `${USER_HOME}/` 全部替换为 `~/`
#   verify   等价 check + 若 CRITICAL 项仍有残留则 exit 1（CI 用）
#
# 分类规则：
#   CRITICAL  — Claude Code 权限 glob，运行时立即失效
#               （当前为空：项目级 `.claude/settings.json` 已移除，
#                 权限由用户级 `~/.claude/settings.json` 提供）
#   COSMETIC  — 文档 / 脚本注释里的示例值，不影响行为但污染示例
#               · scripts/skills/trace-ros-topology.sh   (usage 示例)
#               · shared/protocols/trace-artifacts-schema.md (schema 示例)
#   UNMANAGED — 其它 tracked 文件里出现 `${USER_HOME}`，提示作者审阅是否纳入清单
#
# 已经妥善处理、不在范围内：
#   · 200+ 处 ~/scholar-agent/... 引用：deploy.sh:ensure_home_symlink() 兜底
#   · paths.local.yaml / repo-path-index.local.yaml：gitignored，本机单源
#   · .ai/、.claude/settings.local.json：gitignored
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

USER_HOME="${HOME%/}"

REPO_ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel 2>/dev/null \
  || { echo "not in a git repo" >&2; exit 2; })"
cd "$REPO_ROOT"

# No CRITICAL files by default. `.claude/settings.json` used to be listed here,
# but a project-level Claude allowlist is optional: the system-level
# `~/.claude/settings.json` is what grants permissions, and a repository does not
# need to ship its own. Delete an entry rather than leaving a rule for a file that
# no longer exists.
CRITICAL_FILES=()

COSMETIC_FILES=(
  "scripts/skills/trace-ros-topology.sh"
  "shared/protocols/trace-artifacts-schema.md"
)

# colors (only when stdout is a TTY)
if [[ -t 1 ]]; then
  C_RED=$'\033[31m' C_YEL=$'\033[33m' C_GRN=$'\033[32m'
  C_DIM=$'\033[2m'  C_BLD=$'\033[1m'  C_RST=$'\033[0m'
else
  C_RED='' C_YEL='' C_GRN='' C_DIM='' C_BLD='' C_RST=''
fi

scan_file() {
  local f="$1"
  if [[ ! -f "$f" ]]; then
    echo "  ${C_DIM}(missing: $f)${C_RST}"
    return
  fi
  local n
  n=$(grep -Fc "$USER_HOME" "$f" 2>/dev/null || true)
  n=${n:-0}
  if [[ "$n" -gt 0 ]]; then
    echo "  $f: ${C_BLD}$n${C_RST} hits"
    grep -FnH "$USER_HOME" "$f" 2>/dev/null | sed "s|^|    ${C_DIM}|; s|\$|${C_RST}|"
  else
    echo "  $f: ${C_GRN}clean${C_RST}"
  fi
}

list_unmanaged() {
  local managed_re
  managed_re=$(printf '|^%s$' "${CRITICAL_FILES[@]}" "${COSMETIC_FILES[@]}")
  managed_re="${managed_re:1}"
  # exclude .ai/ (gitignored process artifacts) and .gitignore (meta only)
  git grep -Fl "$USER_HOME" -- ':(top)' \
      ':!.ai/**' ':!.gitignore' 2>/dev/null \
    | grep -Ev "${managed_re}" || true
}

count_residual() {
  local total=0 f n
  for f in "$@"; do
    [[ -f "$f" ]] || continue
    n=$(grep -Fc "$USER_HOME" "$f" 2>/dev/null || true)
    n=${n:-0}
    total=$((total + n))
  done
  echo "$total"
}

cmd_check() {
  echo "${C_BLD}== CRITICAL (runtime impact) ==${C_RST}"
  for f in "${CRITICAL_FILES[@]}"; do scan_file "$f"; done
  echo
  echo "${C_BLD}== COSMETIC (docs/usage examples) ==${C_RST}"
  for f in "${COSMETIC_FILES[@]}"; do scan_file "$f"; done
  echo
  echo "${C_BLD}== UNMANAGED (tracked files outside the rule list) ==${C_RST}"
  local extras
  extras=$(list_unmanaged)
  if [[ -z "$extras" ]]; then
    echo "  ${C_GRN}(none)${C_RST}"
  else
    echo "$extras" | while read -r f; do
      echo "  ${C_YEL}$f${C_RST}"
    done
    echo
    echo "  ${C_DIM}→ Audit each file above; add to CRITICAL_FILES or"
    echo "    COSMETIC_FILES in $0 if it should be cleaned.${C_RST}"
  fi
}

cmd_fix() {
  local touched=0 f
  for f in "${CRITICAL_FILES[@]}" "${COSMETIC_FILES[@]}"; do
    if [[ -f "$f" ]] && grep -Fq "$USER_HOME/" "$f"; then
      python3 - "$f" "$USER_HOME/" <<'PYFIX'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace(sys.argv[2], "~/"))
PYFIX
      echo "  ${C_GRN}fixed${C_RST}: $f"
      touched=$((touched + 1))
    fi
  done
  echo
  if [[ "$touched" -eq 0 ]]; then
    echo "${C_GRN}Nothing to fix.${C_RST}"
  else
    echo "Files touched: ${C_BLD}$touched${C_RST}"
    echo "Run '$0 verify' to confirm."
  fi
}

cmd_verify() {
  cmd_check
  echo
  local critical_residual
  critical_residual=$(count_residual "${CRITICAL_FILES[@]}")
  if [[ "$critical_residual" -gt 0 ]]; then
    echo "${C_RED}FAIL${C_RST}: CRITICAL files still contain ${USER_HOME}/ ($critical_residual hits)" >&2
    exit 1
  fi
  echo "${C_GRN}OK${C_RST}: no CRITICAL hits."
}

case "${1:-check}" in
  check)  cmd_check ;;
  fix)    cmd_fix ;;
  verify) cmd_verify ;;
  -h|--help|help)
    sed -n '2,30p' "$0" | sed 's|^# \?||'
    ;;
  *) echo "Usage: $0 [check|fix|verify]" >&2; exit 2 ;;
esac

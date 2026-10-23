#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# permission-counter.sh — Claude Code PermissionRequest hook
# ─────────────────────────────────────────────────────────────────────────────
# 每次 Claude 因 Bash 命令触发权限弹窗时，本 hook 由 Claude Code 调用：
#   · stdin = JSON: { tool_name, tool_input.command, ... }
#   · 我们把命令归一化成 `Bash(<pattern>)` 形态的 key，计数 +1
#   · 退出 0 不返回 decision，让用户继续走原本的 allow-once / always / deny 流
#
# 计数文件：~/.claude/permission-prompt-counts.json
#   schema: { "Bash(<pattern>)": { "count": N, "last_seen": "ISO8601",
#                                  "examples": ["raw cmd 1", "raw cmd 2", ...] } }
#
# 为了不阻塞用户，所有错误都吞掉 → exit 0。
# ─────────────────────────────────────────────────────────────────────────────

set -u
# 不用 -e：任何错误都不阻塞 Claude

COUNTS_FILE="${HOME}/.claude/permission-prompt-counts.json"
mkdir -p "$(dirname "$COUNTS_FILE")" 2>/dev/null || true

INPUT="$(cat 2>/dev/null || true)"
[[ -z "$INPUT" ]] && exit 0

# 只处理 Bash（其它工具如 Read/Write 由 Read(...)/Write(...) 形式管理，归一化复杂度高，先不管）
TOOL_NAME="$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)"
[[ "$TOOL_NAME" != "Bash" ]] && exit 0

CMD="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)"
[[ -z "$CMD" ]] && exit 0

# ── 归一化 ───────────────────────────────────────────────────────────────────
# 把 "git status -sb" → "Bash(git status *)"
# 把 "git -C /a/b log" → "Bash(git -C * log *)"
# 把 "bash /home/x/foo.sh args" → "Bash(bash /home/x/foo.sh *)"
# 把 "grep -rn pattern path" → "Bash(grep *)"
# 把 "ls -la /tmp" → "Bash(ls *)"
normalize() {
  local cmd="$1"
  # 按行扫描，跳过空行和纯注释行，找到第一条有意义的命令
  local line first=""
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"   # ltrim
    [[ -z "$line" || "$line" == \#* ]] && continue
    first="$line"; break
  done <<<"$cmd"
  cmd="${first:-}"
  [[ -z "$cmd" ]] && { printf 'Bash(*)'; return; }

  # 折叠多空格
  cmd="$(printf '%s' "$cmd" | tr -s '[:space:]' ' ')"

  # 拆 token
  read -r -a tok <<<"$cmd"
  local n=${#tok[@]}
  (( n == 0 )) && { printf 'Bash(*)'; return; }

  local first_tok="${tok[0]}"
  case "$first_tok" in
    bash|sh|zsh|python|python3|node)
      if (( n >= 2 )); then
        printf 'Bash(%s %s *)' "$first_tok" "${tok[1]}"
      else
        printf 'Bash(%s *)' "$first_tok"
      fi
      ;;
    git)
      if (( n >= 4 )) && [[ "${tok[1]}" == "-C" ]]; then
        printf 'Bash(git -C * %s *)' "${tok[3]}"
      elif (( n >= 2 )); then
        printf 'Bash(git %s *)' "${tok[1]}"
      else
        printf 'Bash(git *)'
      fi
      ;;
    *)
      printf 'Bash(%s *)' "$first_tok"
      ;;
  esac
}

PATTERN="$(normalize "$CMD")"
NOW="$(date -Iseconds 2>/dev/null || date)"

# ── 原子地更新 JSON ──────────────────────────────────────────────────────────
[[ -f "$COUNTS_FILE" ]] || echo '{}' > "$COUNTS_FILE"

TMP="$(mktemp "${COUNTS_FILE}.XXXXXX" 2>/dev/null)" || exit 0
jq --arg p "$PATTERN" --arg t "$NOW" --arg c "$CMD" '
  .[$p] = (.[$p] // {count:0, last_seen:"", examples:[]}) |
  .[$p].count = (.[$p].count + 1) |
  .[$p].last_seen = $t |
  .[$p].examples = (
    (.[$p].examples + [$c])
    | unique
    | (if length > 5 then .[(length-5):] else . end)
  )
' "$COUNTS_FILE" > "$TMP" 2>/dev/null && mv -f "$TMP" "$COUNTS_FILE" || rm -f "$TMP"

exit 0

#!/usr/bin/env bash
# marker-deploy.sh — Replace content between marker tags in target file.
# Source this file, then call: marker_deploy <source_file> <target_file>
#
# Behavior:
#   - Target doesn't exist → create file with markers wrapping source content
#   - Target exists but has no markers → append markers + source at end
#   - Target has markers → replace content between markers, preserve everything else
#   - Target is a symlink → remove symlink first, then treat as "doesn't exist"

BEGIN_MARKER="<!-- BEGIN AI-WORKFLOWS MANAGED -->"
END_MARKER="<!-- END AI-WORKFLOWS MANAGED -->"

marker_deploy() {
  local src="$1" dst="$2"

  if [[ ! -f "$src" ]]; then
    echo "marker-deploy: error: source not found: $src" >&2
    return 1
  fi

  mkdir -p "$(dirname "$dst")"

  # If target is a symlink (legacy), remove it so we create a real file
  if [[ -L "$dst" ]]; then
    rm "$dst"
  fi

  if [[ ! -f "$dst" ]]; then
    # Target doesn't exist: create with markers
    {
      echo "$BEGIN_MARKER"
      cat "$src"
      echo "$END_MARKER"
    } > "$dst"
    return 0
  fi

  if grep -qF "$BEGIN_MARKER" "$dst"; then
    # Has markers: replace content between them
    python3 - "$src" "$dst" "$BEGIN_MARKER" "$END_MARKER" <<'PYTHON'
import sys

src_path, dst_path, begin_marker, end_marker = sys.argv[1:5]

with open(dst_path, 'r') as f:
    text = f.read()

with open(src_path, 'r') as f:
    new_content = f.read()

begin_idx = text.index(begin_marker) + len(begin_marker)
end_idx = text.index(end_marker)

# Ensure newline boundaries
result = text[:begin_idx] + "\n" + new_content.rstrip("\n") + "\n" + text[end_idx:]

with open(dst_path, 'w') as f:
    f.write(result)
PYTHON
  else
    # No markers: append at end
    {
      echo ""
      echo "$BEGIN_MARKER"
      cat "$src"
      echo "$END_MARKER"
    } >> "$dst"
  fi
}

# Remove only the block owned by marker_deploy. If the file contains no other
# content, remove the file itself; otherwise preserve the user's content.
marker_undeploy() {
  local dst="$1"

  [[ -e "$dst" ]] || return 0
  if [[ -L "$dst" ]]; then
    echo "marker-deploy: skip symlink: $dst" >&2
    return 0
  fi
  grep -qF "$BEGIN_MARKER" "$dst" || return 0
  grep -qF "$END_MARKER" "$dst" || {
    echo "marker-deploy: malformed markers in: $dst" >&2
    return 1
  }

  if [[ "${DEPLOY_DRY_RUN:-}" == "1" ]]; then
    echo "marker-deploy: would remove managed block: $dst"
    return 0
  fi

  python3 - "$dst" "$BEGIN_MARKER" "$END_MARKER" <<'PYTHON'
import os
import sys

dst_path, begin_marker, end_marker = sys.argv[1:]
with open(dst_path, "r", encoding="utf-8") as handle:
    text = handle.read()

if text.count(begin_marker) != 1 or text.count(end_marker) != 1:
    raise SystemExit(f"marker-deploy: malformed markers in: {dst_path}")

begin = text.index(begin_marker)
end = text.index(end_marker, begin + len(begin_marker))
if end < begin:
    raise SystemExit(f"marker-deploy: marker order invalid in: {dst_path}")

before = text[:begin].rstrip("\n")
after = text[end + len(end_marker):].lstrip("\n")
parts = [part for part in (before, after) if part]
if not parts:
    os.unlink(dst_path)
else:
    with open(dst_path, "w", encoding="utf-8") as handle:
        handle.write("\n\n".join(parts) + "\n")
PYTHON
}

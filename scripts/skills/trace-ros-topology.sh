#!/usr/bin/env bash
set -euo pipefail
# ─────────────────────────────────────────────────────────────────────────────
# trace-ros-topology.sh — Extract ROS2 topic/param topology from source + config
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   trace-ros-topology.sh <keyword> [search_root]
#
# Examples:
#   trace-ros-topology.sh /teleop_commands .
#   trace-ros-topology.sh J_WAIST_M1 ~/robot_control/ros2_packages
#   trace-ros-topology.sh force_setpoint ./example_node2
#
# Output: structured summary of topic publishers, subscribers, param declarations,
#         config YAML values, and cross-node connections.
# ─────────────────────────────────────────────────────────────────────────────

KEYWORD="${1:?Usage: $0 <keyword> [search_root]}"
ROOT="${2:-.}"

# Skip build artifacts
PRUNE="-path */build -prune -o -path */install -prune -o -path */.git -prune -o -path */__pycache__ -prune -o"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  ROS Topology Trace: ${KEYWORD}"
echo "║  Root: ${ROOT}"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

# ─── 1. Config/YAML references ──────────────────────────────────────────────
echo "┌─ [1] YAML Config References ────────────────────────────────────────┐"
find "$ROOT" $PRUNE -name "*.yaml" -print0 2>/dev/null | \
  xargs -0 grep -ln "$KEYWORD" 2>/dev/null | while read -r f; do
    echo "  File: $f"
    grep -n "$KEYWORD" "$f" | sed 's/^/    /'
done
echo "└─────────────────────────────────────────────────────────────────────┘"
echo ""

# ─── 2. ROS Topic Publishers ────────────────────────────────────────────────
echo "┌─ [2] Topic Publishers (create_publisher / advertise) ───────────────┐"
find "$ROOT" $PRUNE \( -name "*.cpp" -o -name "*.hpp" -o -name "*.py" \) -print0 2>/dev/null | \
  xargs -0 grep -ln "$KEYWORD" 2>/dev/null | while read -r f; do
    matches=$(grep -n "create_publisher\|advertise\|Publisher" "$f" 2>/dev/null | grep -i "$(echo $KEYWORD | sed 's|/||g')" || true)
    if [ -n "$matches" ]; then
      echo "  File: $f"
      echo "$matches" | sed 's/^/    /'
    fi
done
echo "└─────────────────────────────────────────────────────────────────────┘"
echo ""

# ─── 3. ROS Topic Subscribers ───────────────────────────────────────────────
echo "┌─ [3] Topic Subscribers (create_subscription / subscribe) ───────────┐"
find "$ROOT" $PRUNE \( -name "*.cpp" -o -name "*.hpp" -o -name "*.py" \) -print0 2>/dev/null | \
  xargs -0 grep -ln "$KEYWORD" 2>/dev/null | while read -r f; do
    matches=$(grep -n "create_subscription\|subscribe\|Subscription\|Subscriber" "$f" 2>/dev/null | grep -i "$(echo $KEYWORD | sed 's|/||g')" || true)
    if [ -n "$matches" ]; then
      echo "  File: $f"
      echo "$matches" | sed 's/^/    /'
    fi
done
echo "└─────────────────────────────────────────────────────────────────────┘"
echo ""

# ─── 4. Parameter Declarations ──────────────────────────────────────────────
echo "┌─ [4] Parameter Declarations (declare_parameter / get_parameter) ────┐"
find "$ROOT" $PRUNE \( -name "*.cpp" -o -name "*.hpp" -o -name "*.py" \) -print0 2>/dev/null | \
  xargs -0 grep -n "declare_parameter\|get_parameter" 2>/dev/null | \
  grep -i "$KEYWORD" | head -20 | sed 's/^/  /'
echo "└─────────────────────────────────────────────────────────────────────┘"
echo ""

# ─── 5. Callback / Handler functions ────────────────────────────────────────
echo "┌─ [5] Callbacks & Handlers ──────────────────────────────────────────┐"
find "$ROOT" $PRUNE \( -name "*.cpp" -o -name "*.hpp" -o -name "*.py" \) -print0 2>/dev/null | \
  xargs -0 grep -n "Callback\|callback\|_cb\|handler" 2>/dev/null | \
  grep -i "$KEYWORD" | head -15 | sed 's/^/  /'
echo "└─────────────────────────────────────────────────────────────────────┘"
echo ""

# ─── 6. All source files referencing keyword (file list only) ────────────────
echo "┌─ [6] All Source Files Referencing '$KEYWORD' (paths only) ──────────┐"
find "$ROOT" $PRUNE \( -name "*.cpp" -o -name "*.hpp" -o -name "*.h" -o -name "*.py" \) -print0 2>/dev/null | \
  xargs -0 grep -ln "$KEYWORD" 2>/dev/null | sort | sed 's/^/  /'
echo "└─────────────────────────────────────────────────────────────────────┘"
echo ""

# ─── 7. Launch file references ──────────────────────────────────────────────
echo "┌─ [7] Launch File References ────────────────────────────────────────┐"
find "$ROOT" $PRUNE \( -name "*.launch.py" -o -name "*.launch.xml" -o -name "*.launch.yaml" \) -print0 2>/dev/null | \
  xargs -0 grep -ln "$KEYWORD" 2>/dev/null | while read -r f; do
    echo "  File: $f"
    grep -n "$KEYWORD" "$f" | sed 's/^/    /'
done
echo "└─────────────────────────────────────────────────────────────────────┘"

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "  Trace complete. Total source files: $(find "$ROOT" $PRUNE \( -name "*.cpp" -o -name "*.hpp" -o -name "*.h" -o -name "*.py" \) -print0 2>/dev/null | xargs -0 grep -ln "$KEYWORD" 2>/dev/null | wc -l)"
echo "═══════════════════════════════════════════════════════════════════════"

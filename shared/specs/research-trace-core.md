# research trace — Core Spec (L0–L3)

ROS2 control-chain tracing via `/research trace <target>`. Read this file **in full** before starting any trace (after `research.sh gate` approves trace mode).

**Persistence**: All intermediate findings are saved via `research.sh` to `.ai/research/` in the target project.

For Layer 4–5 and report template, read `~/scholar-agent/shared/specs/research-trace-report.md` **only when you reach Layer 4** (path from gate `READ_AT_L4=` line).

---

## Core Principle

> **Never read a whole file to find 3 lines.** Locate first, then surgically read.
> **Never redo work already persisted.** Check `.ai/research/` first.

---

## Invocation

User provides one of:
- A **symbol** (function, variable, joint name): `J_WAIST_M1`, `ComputeIkTracking`
- A **topic**: `/teleop_commands`, `/left/force_torque`
- A **symptom/error**: "waist slipping", "contact detection delayed"
- A **cross-node question**: "how does IK output reach the controller?"

Optional: target package or search root.

---

## Layer Strategy (MANDATORY ORDER — L0 through L3)

### Layer 0: Research Check — budget: 1 shell call

**Mandatory first step.** Run:
```bash
bash ~/scholar-agent/scripts/skills/research.sh query "<target>" --root <search_root>
```

**Parse stdout (1-3 lines):**

| First word | Meaning | Action |
|-----------|---------|--------|
| `MISS` | No prior research exists | Set `FRESH=true`, proceed to Layer 1 |
| `HIT ... stale=false ... RESUME_FROM=done` | Complete fresh trace exists | `read-chain <SID> --summary`, skip to Layer 5 (present only) |
| `HIT ... stale=false ... RESUME_FROM=L3` | Incomplete session | `read-chain <SID> --summary`, resume from indicated layer |
| `HIT ... stale=true ...` | Stale session | `read-chain <SID> --summary` as hint, rerun from L1 |

**If FRESH=true**, create a new session:
```bash
bash ~/scholar-agent/scripts/skills/research.sh start "<target>" --type trace --source research-trace --root <search_root>
```
This prints `SESSION=<id> PATH=<dir>`. Store `<id>` for all subsequent `append` calls.

**If resuming an incomplete session**, reuse the existing session ID from the HIT line.

---

### Layer 1: Locate (WHERE) — budget: 1-2 tool calls

**Goal**: Identify which files are involved. Do NOT read content yet.

**Tools**:
```bash
# Option A: helper script (preferred for ROS topology questions)
bash ~/scholar-agent/scripts/skills/trace-ros-topology.sh "<keyword>" "<search_root>"

# Option B: file-list-only grep (for symbol/function tracing)
grep -rln "<symbol>" <root>/src/ <root>/include/ <root>/config/ | sort
```

**Output**: A file list (paths only). Typically 3-15 files.

**Decision gate**: If > 20 files, narrow the keyword or add path constraints.

**Persist** (mandatory):
```bash
echo '{"file_list":["path1","path2",...],"count":N}' \
  | bash ~/scholar-agent/scripts/skills/research.sh append <SESSION_ID> --layer 1 --root <search_root>
```

### Layer 2: Precise Jump (WHAT) — budget: 2-4 tool calls

**Goal**: Get exact definition and reference locations without reading source.

**Tools (in preference order)**:
1. **LSP goToDefinition** — for functions, classes, variables
2. **LSP findReferences** — for "who calls this?"
3. **LSP incomingCalls** — for call hierarchy
4. **grep -n** (fallback) — only when LSP unavailable or cross-language

**Rules**:
- Use LSP when you know the file and line (from Layer 1 grep output)
- For cross-language (C++ ↔ Python via ROS msg): use the topology script output to bridge
- For config → code: grep param name in both YAML and source

**Output**: A list of `file:line` pairs with one-line context.

**Persist** (mandatory):
```bash
echo '{"jumps":[{"symbol":"X","def":"file:line","refs":["file:line",...]}]}' \
  | bash ~/scholar-agent/scripts/skills/research.sh append <SESSION_ID> --layer 2 --root <search_root>
```

### Layer 3: Surgical Read (HOW) — budget: 3-8 tool calls

**Goal**: Read ONLY the minimum lines needed to understand the logic.

**Rules**:
- `Read(file, offset=line-5, limit=15)` — 15 lines max per read
- NEVER read a whole file unless it's < 50 lines
- If you need more context, read in 2 adjacent chunks, not the whole file
- Prioritize: function signature + body (not includes/boilerplate)

**Anti-pattern** (FORBIDDEN):
```
❌ Read(file)              → 500+ lines into context
❌ Read(file, limit=100)   → still too much for one function
```

**Correct pattern**:
```
✅ Read(file, offset=45, limit=15)   → just the function
✅ Read(file, offset=120, limit=10)  → just the config block
```

**Persist** (mandatory):
```bash
echo '{"reads":[{"file":"path","offset":45,"limit":15,"summary":"brief description"}]}' \
  | bash ~/scholar-agent/scripts/skills/research.sh append <SESSION_ID> --layer 3 --root <search_root>
```

---

## Decision Rules (L0–L3)

### When to use LSP vs grep

| Scenario | Use |
|----------|-----|
| "Where is X defined?" | LSP goToDefinition |
| "Who calls X?" | LSP findReferences / incomingCalls |
| "What config sets X?" | grep in *.yaml + grep declare_parameter |
| Cross-language (C++ ↔ Python) | trace-ros-topology.sh + grep |

### When to STOP tracing

- You have enough hops to answer the user's question
- You've crossed > 5 nodes (probably going too deep)
- The chain loops back to a file you already traced

### When to spawn sub-agent vs trace yourself

- **Yourself**: ≤ 5 files, single-package, C++ only
- **Sub-agent (Explore)**: > 5 files, cross-package, need broad search
  - Prompt the agent with: specific question + file list from L1 + "report under 200 words"

**Next**: At Layer 4, read `~/scholar-agent/shared/specs/research-trace-report.md`.

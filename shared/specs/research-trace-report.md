# research trace — Report Spec (L4–L5)

Read this file **only when reaching Layer 4** of a trace. Layers 0–3 are in `research-trace-core.md`.

---

## Layer 4: ROS Cross-Node Bridge (CROSS-NODE) — budget: 1-2 tool calls

**Goal**: Connect publisher → subscriber across different nodes/packages.

**When to use**: When the trace crosses a ROS topic boundary (e.g., "mink_ik_node publishes /teleop_commands, who subscribes?")

**Tool**:
```bash
bash ~/scholar-agent/scripts/skills/trace-ros-topology.sh "<topic_name>" "<workspace_root>"
```

**Output parsing**: The script sections [2] and [3] give pub/sub pairs directly.

**Persist** (mandatory):
```bash
echo '{"cross_node":[{"from":"node_a","topic":"/x","to":"node_b","msg_type":"Type"}]}' \
  | bash ~/scholar-agent/scripts/skills/research.sh append <SESSION_ID> --layer 4 --root <search_root>
```

---

## Layer 5: Report (OUTPUT) — budget: 1 tool call

**Goal**: Fill in the standard report template and persist.

**Output routing rule (Context Budget)**: always persist, and put the answer in
chat with the path. Do not count lines first.

```
✅ Trace complete: <target>
Chain: <N> hops across <M> files
Key finding: <one sentence>
Full report: <path from CHAIN= output>
```

**Persist** (mandatory, regardless of size):
```bash
cat <<'REPORT' | bash ~/scholar-agent/scripts/skills/research.sh save-report <SESSION_ID> --root <search_root>
<markdown report content>
REPORT
```

Then finalize the session:
```bash
bash ~/scholar-agent/scripts/skills/research.sh complete <SESSION_ID> --root <search_root>
```

---

## Report Template

Output a markdown document following this EXACT structure:

```markdown
# Code Trace: <target description>

> Traced: <date>
> Root: <search_root>
> Trigger: <what question prompted this trace>
> Session: <SESSION_ID>

## Chain Summary (one-line per hop)

1. `<file>:<line>` — <what happens here>
2. `<file>:<line>` — <what happens here>
3. ...

## Detailed Findings

### Hop 1: <label>
- **File**: `path/to/file.cpp`
- **Lines**: 45-58
- **Role**: <brief description>
- **Key code** (≤10 lines):
  ```cpp
  <only the critical lines>
  ```

### Hop 2: <label>
...

## Cross-Node Connections

| From (publisher) | Topic | To (subscriber) | Message type |
|-----------------|-------|-----------------|--------------|
| ... | ... | ... | ... |

## Config → Runtime Mapping

| YAML param | File | Variable | Default |
|-----------|------|----------|---------|
| ... | ... | ... | ... |

## Diagnosis / Answer

<Direct answer to the user's question, referencing the chain above>
```

---

## Tool-call shape

The point of the layers is to keep each step bounded, not to hit a token number.
These are the intended calls per layer — if a layer needs many more, the scope is
wrong:

| Layer | Tool calls | What is fetched |
|-------|-----------|-----------------|
| L0: Research check | 1 | prior-session verdict from `research.sh query` |
| L1: Locate | 1-2 | file paths |
| L2: Precise | 2-4 | `file:line` pairs |
| L3: Read | 3-8 | one region per file |
| L4: Cross-node | 1-2 | script output |
| L5: Report | 1 | persist the report |

No token accounting is maintained: the number is not measurable from inside the
session, and a target that cannot be measured becomes a claim instead of a
constraint.

---

## Cross-Project Search

When the target might exist in a different project (e.g., shared ROS msg definitions):
```bash
bash ~/scholar-agent/scripts/skills/research.sh search-all "<keyword>" --source research-trace
```

If a HIT is found in another project, use `read-chain` to retrieve its report:
```bash
bash ~/scholar-agent/scripts/skills/research.sh read-chain <session_id> --project <project_name>
```

---

## Doc Output Rules

Follow the project's documentation conventions:
- Filename: `kebab-case` reflecting the trace target
- Header: `> Traced: <date>` + trigger question (when/why)
- Location: target package's `docs/` directory
- Only write doc if user explicitly requests it

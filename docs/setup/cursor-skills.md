# Cursor skills

Cursor reads `cursor-skills/<name>/SKILL.md`. That tree is **generated** by
`scripts/deploy/generate.sh` from `skills/<name>/SKILL.md` — do not edit it, and
do not keep documentation inside it (the generator owns the directory).

## Deploy

From the repository root:

```bash
bash deploy.sh --cursor      # Cursor only
bash deploy.sh --all         # every platform
```

`deploy.sh` links each skill into `~/.cursor/skills-cursor/<skill-name>/`.

## Keeping a SKILL short

Long-form material belongs in `shared/`, not in a skill file:

- Skill file: when it triggers, the mandatory steps, and links.
- `shared/specs/*-ai.md`: the full execution spec.
- `shared/protocols/`: rules that span several skills.

A skill that is already built out for another platform can stay a thin wrapper
on Cursor — a few lines plus a pointer into the shared spec.

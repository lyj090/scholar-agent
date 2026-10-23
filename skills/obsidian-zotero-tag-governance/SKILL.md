# Obsidian Zotero Tag Governance

Audit and normalize tags across Obsidian vault and Zotero.

## Flow

```bash
bash ~/scholar-agent/scripts/skills/skill-gate.sh obsidian-zotero-tag-governance
```

Run `RUN=` first. The audit script loads taxonomy rules; read `READ=`/`READ_WITH=` only when gate emits them or a custom mapping needs review.

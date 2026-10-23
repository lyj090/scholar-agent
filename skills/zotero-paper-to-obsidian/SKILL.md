# Zotero Paper → Obsidian

Write an evidence-first Obsidian note from a Zotero paper: chapter-ordered,
figures bound to the printed numbers, claims traceable to a section or page.

## Trigger

A literature note with YAML frontmatter, figures and assets for one specific
paper. Not for summarizing a paper in chat, and not for reading one (that is
`paper-lookup`).

## Flow

```bash
bash ~/scholar-agent/scripts/agent.sh gate zotero-paper-to-obsidian
```

Run what the gate emits, then read the spec it names. Two things the spec
enforces that are easy to get wrong without it:

- A caption is a *candidate* figure number, never proof. Bind it to the rendered
  PDF page before writing, and keep that work out of the note.
- Nothing personal goes in the published body. Private commentary sits below
  `<!-- private -->`, which is dropped before publication.

Publishing and migration are in
[`docs/literature/publication-workflow.md`](../../docs/literature/publication-workflow.md).

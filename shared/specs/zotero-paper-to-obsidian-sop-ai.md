# Zotero paper → Obsidian writing SOP

Read [literature-note-contract.md](literature-note-contract.md) before synthesis.
It defines depth, evidence, figure identity and project isolation in one place.

## Metadata and output

- Copy public metadata from `prepare-note --json`; keep `note_contract: 2`.
- Source/code links come first. Writers inject local shortcuts after them.
- Tags are YAML block lists; use the public taxonomy. Personal tag extensions
  are loaded by tools from private config and never supplied as paper context.
- Unknown bibliographic values are omitted, never invented.
- Filename: original paper title, sanitized by the script, with `.md`.
- Assets: use verified `figures[].md_ref`; file numbering is never evidence.
- Preserve deep chapter-by-chapter analysis, equations, figures, experiments,
  ablations, assumptions and author-stated limitations.
- End the publishable note where the paper ends. Do not generate a personal
  closing section; local analysis belongs elsewhere, below the divider.

## Private tail divider

Separate "the paper" from working notes with one line, on its own:

```md
<!-- private -->
```

Everything from it to EOF is dropped before publication, whatever its heading.
This is the boundary; a title keyword is only a legacy fallback and cannot hide
a section named like ordinary paper content. Keep review notes, reproduction
caveats, local context and project mapping below it. Do not point the public
body at a section below the divider (`见 §10` dangles once §10 is cut); inline
the referenced point. The divider is optional when the note ends with the paper.

## Write and verify

Use `write-note --content-file` or `assemble-note --yaml --sections`; keep
long section drafts on disk. All writers validate before atomic replacement.
Run `verify-note <KEY> --json` once; resolve findings rather than rereading the
whole note. Personal sections are accepted only for legacy compatibility.
Export/publish is handled by `workflow.py`, never by copying the entire vault.

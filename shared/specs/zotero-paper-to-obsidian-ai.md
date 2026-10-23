# Zotero paper → Obsidian workflow

The source contract is [literature-note-contract.md](literature-note-contract.md).
Read it when synthesizing. Keep substantial source explanations and evidence;
original/code links lead the note. Personal applicability is a separate private
document, never context for the source note.

## Resolve and prepare

```bash
python3 ~/scholar-agent/scripts/literature/workflow.py prepare "<REF>"
```

A REF may be an attachment key, cache ID, title, DOI or paper URL. Resolve an
exact attachment; for a parent book with several chapter PDFs use
`zotero_cli.py mineru resolve "<REF>" --match "Chapter N" --json`. Pass that
attachment to `prepare-note <PARENT_KEY> --attachment-key <PDF_KEY> --json`.
If no parsed cache exists, use the local-first preflight before importing or
parsing. Existing notes are preserved; refresh assets only for an intended
update, then review any changed hashes.

Prepare returns public bibliographic metadata, local bookkeeping, asset IDs,
caption candidates, `figure-candidates.json` and paths. It does not establish
printed Figure identities. Never infer a figure number from an asset filename
or fill missing numbers by sequence. Inspect rendered PDF pages and captions,
then record reviewed identities and hashes in the private figure ledger.
Use corrected alt text and references, retaining panels and supplementary IDs.

## Read incrementally

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py mineru outline "<REF>" --json
python3 ~/scholar-agent/scripts/literature/zotero_cli.py mineru read "<REF>" --section 0 --scope self
```

Read each relevant section once and continue truncated reads to completion.
Avoid reading both parent subtrees and their children. Include relevant
appendices. Use `--verbose` when complete source text is needed for synthesis;
keep section drafts on disk, rather than repeating complete notes in chat.

## Write, verify and prepare publication

```bash
python3 ~/scholar-agent/scripts/literature/workflow.py finish "<KEY>" --content-file /tmp/source-note.md
python3 ~/scholar-agent/scripts/literature/workflow.py check --note /path/to/note.md --pdf /path/to/source.pdf
python3 ~/scholar-agent/scripts/literature/workflow.py publish
```

`finish --force` replaces an intentionally updated note. For section drafts,
`assemble-note --yaml ... --sections ...` remains available and uses the same
validation. Writers inject local shortcuts after the public source section;
the exporter removes them. Add topic tags based on the paper, without copying
private relation tags. Unknown year or venue stays absent.

The local publish command derives private CI configuration, exports only
approved sources, builds fresh content and audits the complete artifact. It
returns a reviewable local site. Remote publication is a separate action.
See [publication-workflow.md](../../docs/literature/publication-workflow.md)
for setup, ledger reuse, source release and legacy migration.

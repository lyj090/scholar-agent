# Zotero → Obsidian → public literature site

One private YAML/JSON is the maintained input. Copy
`shared/config/workflow-config.example.yaml` to `shared/paths.local.yaml` or set
`PAPER_WORKFLOW_CONFIG`. Python needs PyYAML; the site needs Node 22+ and its
locked npm dependencies. `garden.repo` points to the local site checkout.
Secrets stay in environment variables or CI secrets.

## Note lifecycle

```bash
python3 scripts/literature/workflow.py prepare <REF>
python3 scripts/literature/zotero_cli.py mineru outline <REF>
python3 scripts/literature/zotero_cli.py mineru read <REF> --section <N> --scope self
python3 scripts/literature/workflow.py finish <KEY> --content-file /tmp/draft.md
python3 scripts/literature/workflow.py check --note /path/to/note.md --pdf /path/to/source.pdf
python3 scripts/literature/workflow.py publish
```

`prepare` needs an existing parsed Zotero attachment. If it is unavailable,
use `paper-status` and the local-first protocol to import/parse it before
retrying. An existing note is preserved; use `prepare-note --force --json`
only to deliberately refresh extraction assets, then review changed hashes.
`finish --force` deliberately replaces an existing note. It writes locally
without synchronizing backlinks to Zotero or writing to Notion.

The source contract is `shared/specs/literature-note-contract.md`. It preserves
deep section analysis, equations, panels, experimental conditions, ablations
and limitations. Original/code links come first. Synthesis uses source text
and public resources; personal projects and private experiments are separate
inputs to separate documents. Structural checks support the reading process;
they cannot certify scientific accuracy or depth.

## Figure identity

Asset numbering describes extracted blocks. It never defines printed figures.
Prepare returns caption candidates and keeps blocks separate. Inspect the
relevant original PDF pages once and record the private hash-bound ledger
specified in the source contract. Reuse an unchanged reviewed ledger on later
runs. Split mixed-figure crops; preserve same-figure panels and appendix IDs.
Remove parser/mapping commentary from the note only after correspondence is
resolved, preserving its scientific explanations. Unresolved reviews block
publication rather than entering a public warning section.

Re-preparing a note rewrites the figures it uses but leaves the previous files
behind, so a live `assets/<slug>/` accumulates superseded names — the same
situation that once let a note mislabel figures. `zotero_cli.py prune-assets`
reports those leftovers (and whole directories no note links to); `--apply`
deletes them. Run it after re-preparing a note.

## Legacy private tails

Older notes hide their non-paper tail by a heading keyword, which publishes any
private section titled like paper content. `zotero_cli.py mark-private` inserts
the explicit `<!-- private -->` divider above a clean trailing personal section
(leaving its content untouched), so the cut no longer depends on naming. It
rewrites only notes whose personal section is the last top-level section; notes
with personal subsections scattered through the body are reported for review,
not modified.

## Publication

Maintain `garden.include` for explicit selection or use `publish: true` plus
`note_contract: 2` on source notes in the literature partitions. Drafts are
excluded. `garden.sources` supplies verified URLs only when legacy metadata
needs them; normally keep public source links in note metadata.

`workflow.py snapshot` derives `.scripts/publishing.yaml` inside the private
vault. This file is a CI input, not a second editable configuration. `publish`
updates it automatically, exports approved notes into a fresh directory,
reencodes referenced local images to remove metadata, builds Quartz, then
checks all generated pages, index entries, links, filenames and assets. It
prints the artifact location and keeps detailed build logs beside it.
`publish --output <fresh-directory>` selects a review location.

The exporter permits only public bibliographic metadata and topic tags. It
removes legacy private tails and local shortcut sections. Unapproved wiki links,
unsafe URLs, raw HTML and remote images stop export for review. Materialize
remote illustrations as reviewed local source assets before retrying. Image
reencoding removes embedded metadata; the reader must still inspect visible
image content. No OCR privacy guarantee is implied.

CI uses `PRIVATE_SOURCE_REPOSITORY` and `OBSIDIAN_PAT`, builds the same staged
content and audits before upload. Configure and trigger remote deployment only
when requested. The private vault must receive the generated snapshot before
CI uses updated selections. Public `content` symlinks are never build inputs.

## Source release

Maintain identifying strings in private `privacy.release_terms`: account names,
project names, collection IDs and identifying local document keys. This list
is narrower than the note privacy list, which may include generic phrases
legitimately used by the tool's policy and tests.

```bash
python3 scripts/literature/release.py --output /tmp/workflows-source-release
```

The command checks current tracked and nonignored source files before copying
them to a fresh external directory with a SHA256 manifest. It excludes ignored
local settings and Git history, blocks configured identities and credential
signatures, and refuses symlinks/unreviewed binary files. Publish from a reviewed
new snapshot if the existing repository's history contains old identifiers.
The original repository history is not rewritten by this command.

## Remaining-note migration handoff

Handle one note per transaction: resolve exact attachment → read relevant source
sections → check private contexts → inspect Figure/Table evidence → preserve
deep analysis → move personal sections to private documents → correct assets
and source links → run offline check → add to publication selection → build
and audit. Record pending figure and scientific issues privately. Do not bulk
truncate bodies or label legacy mappings as newly verified. Before modifying
an existing note, reread its current bytes and preserve concurrent edits.

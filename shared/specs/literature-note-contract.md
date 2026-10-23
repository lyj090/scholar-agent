# Literature note contract — version 2

Read for synthesis only. A note is an in-depth explanation of its source.
Personal project analysis belongs in a separate private document and is never
an input to source-note synthesis. Missing personal commentary is not an error.

## Opening and metadata

After YAML, start with the literal heading `## 原文与代码` (keep it verbatim —
the exporter and the validators match on this string; it is a required
section marker, not prose to translate): original paper / DOI / arXiv,
official project page and official code if verified. Never infer a code URL.
Say `代码链接：未核实` when no authoritative link was found. Then place the
local Zotero/PDF shortcuts (injected by the writer, removed on publication).
Use the metadata returned by prepare-note; `note_contract: 2` identifies this
contract. Reading status and local identifiers are tool bookkeeping only.

## Depth is the default

Write in Chinese unless requested otherwise. Preserve original terminology,
symbols and source identifiers. Follow the paper's section order, with a short
orientation before the walkthrough. Cover the main text and relevant appendix:

- Problem, assumptions, inputs/outputs, definitions and what is actually novel.
- Mechanism and algorithm steps, explaining why each component is present.
- Key equations with every symbol defined, assumptions, derivation where given,
  and whether the claim is theoretical or measured. Do not fabricate proofs.
- Figures/tables at the relevant discussion: explain panels, axes, units,
  comparisons and the conclusion supported by the evidence.
- Experimental setup, datasets, baselines, metrics, units, ablations and failure
  cases. Preserve configuration differences; do not mix incompatible results.
- Authors' stated limitations and scope. Source omissions may be stated as
  `原文未报告`; novel criticism/inferences must not masquerade as author claims.

Do not shorten a substantive analysis to satisfy a token target. Do not pad it
to reach a character count. Quality is coverage and traceable explanation,
not length. A concise opening is useful; the body remains a deep reading.

## Evidence and figures

Attach source locators to key claims: section, printed Figure/Table/Equation
number or page when the paper has no section numbers. Distinguish the source's
equation from a notation introduced to explain prose. Never invent numbers,
improvement percentages, baselines, confidence or code availability.

An asset's filename/index is NOT a printed figure number. `figures[].md_ref`
is usable only with its evidence status. Prefer the printed PDF caption and
page/region. Repeated numbers are valid for split panels. Figures without
verified correspondence must be resolved through the figure ledger/PDF before
the note is considered complete; never fill gaps by incrementing an index.
Do not combine different numbered figures into one mislabeled image.

Keep asset mappings, hashes, parser defects and verification work in the
private figure ledger / audit report. The note explains the paper, not the
extraction pipeline. Once corrected, remove temporary mapping tables and
parser warnings from the note, preserving useful scientific figure analysis.

## Private tail divider

End the publishable note where the paper ends. To keep review notes, figure
correspondence work, reproduction caveats or local context out of the published
site, put them **after a divider line**:

```md
<!-- private -->
```

Everything from that line to the end of the file is dropped before publication,
whatever its heading — no keyword needs to appear in a title. Keep the divider
on its own line and place anything private below it. This is the reliable way to
separate "the paper" from "my working notes"; heading heuristics only remain for
legacy notes. The divider is optional: a note that ends with the paper needs
none.

The publishable region must stand on its own. A pointer such as `见 §10` dangles
once section 10 moves below the divider; inline the referenced point instead.
The validator rejects a public `§N` whose target heading exists only below the
line.

## Context isolation

Use only source text, figures, bibliographic metadata and verified public
resources during synthesis. Do not read personal project pages, plans, local
research rankings, project tags or local experiment notes to enrich the note.
Do not add personal applicability, implementation plans, hardware constraints,
unpublished comparisons, local paths or private wiki links to the paper body.
Put such material below the private divider (or in a separate private document),
never in the numbered sections. Existing personal headings are supported for
safe legacy export, not generated for new notes.

Paper-to-paper comparisons must be supported by the source or explicitly
requested as a separate comparison document. Optional related-paper links
must resolve to another approved public note; no project/MOC backlinks.

## Spend tokens on analysis

Resolve an exact local cache first. Read the outline, then sections with
`--scope self`; do not reread parent and child content. If a read is truncated,
continue until that section is covered. Read relevant appendix sections too.
Write section drafts once to files, then assemble atomically. Metadata,
source links, assets, validation and public export are deterministic scripts.
Do not repeat full captions/metadata in tool responses or reread the finished
note merely to count headings, check paths, or inspect tag syntax.

For a web source, keep its URL/author/date at the beginning, distinguish source
claims from interpretation, preserve evidence and scope, and apply the same
context isolation. Do not impose a paper's equation/experiment layout on a
web article that does not contain it.

## Private figure ledger format

Each paper asset directory has a private `figure-ledger.json`. The exporter
requires reviewed entries for referenced paper images, including legacy notes
explicitly selected for migration. Web article illustrations do not claim
printed paper figure identities. Prepare emits `figure-candidates.json` and
never overwrites a reviewed ledger. Candidate labels stay `Asset N` until review.

```json
{
  "schema": 2,
  "source_pdf_sha256": "<SHA256 of the reviewed PDF>",
  "assets": [{
    "filename": "asset-001-overview.png",
    "sha256": "<SHA256 of this image>",
    "figures": ["3"],
    "status": "verified",
    "evidence": {
      "page": 6,
      "method": "rendered-pdf-visual-comparison",
      "bbox_normalized": [0.1, 0.1, 0.9, 0.5]
    }
  }]
}
```

Populate `verified` only after actually inspecting the asset against the
rendered PDF page and caption. Repeated Figure 3 entries are valid split
panels. Supplementary IDs such as `S.1` and `B.3` stay strings. Split an image
covering different figures into separate crops; an intentional composite must
list each identity and use the same identities in its alt text. Recompute the
image hash after changes and review again. Hashes detect changes, not semantic
correctness; offline `workflow.py check --note ... --pdf ...` also binds the
review to the current local PDF. The public exporter never copies this ledger.

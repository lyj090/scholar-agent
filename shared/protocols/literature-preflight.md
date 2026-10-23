# Local-First Paper Reading Protocol

> Read this when: a task involves reading, citing, comparing, or verifying any
> external paper — arXiv IDs, DOIs, paper URLs, or "check the literature".
> Referenced by: `skill-routing-ai.md`, `paper-lookup` skill.

---

## The problem this solves

Agents hold an **arXiv ID**; the local corpus is keyed by **Zotero item keys**.
Those key spaces are not bridged by Zotero, so without preflight the only
reliable route to a paper is `web_fetch arxiv.org/abs/<id>` — even when the paper
is already parsed on disk.

The failure is easy to miss because every fetch succeeds. Nothing errors; the
expensive path is simply taken because the cheap one is not visible. On a corpus
of any size, the same papers get fetched repeatedly, and each repeat is a fresh
chance to misread a paper that was already extracted correctly.

A local scan is cheap enough to run before every fetch, so there is no reason to
guess: ask the corpus first, and fetch only on `ABSENT`.

---

## Hard rule

> **Before fetching any paper from the web, run the preflight.**
> `LOCAL_READY` means read the cache. Only `ABSENT` justifies a fetch.

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py paper-status "<ref>"
```

`<ref>` may be an arXiv ID, DOI, paper URL, Zotero key, or title.

| STATE | Meaning | Required action |
|---|---|---|
| `LOCAL_READY` | Parsed MinerU cache exists | **Read locally. Do not fetch.** |
| `LOCAL_UNPARSED` | PDF exists, not parsed | Parse locally (`mineru-parse`), then read |
| `IN_LIBRARY_NO_PDF` | Metadata only | Find/attach the PDF, then parse |
| `ABSENT` | Not in the library | Fetch from the web, then import |

Preflight is deliberately cheap: `paper-status` reuses a cached library index
(15-minute TTL) and a directory scan.

---

## Reading a paper once it is local

Never load a whole paper into context. Section-by-section is enforced by the CLI:

```bash
Z=~/scholar-agent/scripts/literature/zotero_cli.py
python3 $Z mineru outline "<ref>"                    # section map + char spans
python3 $Z mineru read "<ref>" --section 0 --scope self --max-chars 12000
```

`--scope self` avoids injecting child sections twice. `mineru read` hard-bounds
output at 12K chars by default and reports truncation instead of flooding context.

## Finding papers you already have

Use the local corpus instead of `web_search` when asking "do I have work on X?":

```bash
python3 $Z mineru grep "contact-rich manipulation" --max-hits 5 --hits-per-paper 1
```

A full-corpus grep runs in well under a second on a local library.
`NO_LOCAL_MATCH` is the *only* signal that justifies fetching externally — and
it is an explicit, greppable token on purpose.

## Filling a cache miss without the Zotero GUI

Historically a cache miss was a dead end ("CLI 无法主动生成缓存"), so the web was
the only remaining route. Parsing is now available outside the plugin:

```bash
python3 $Z mineru-parse "<ref>"            # cloud, uses the Zotero-stored API key
python3 $Z mineru-parse "<ref>" --dry-run  # resolve PDF/id/cache path, parse nothing
python3 $Z mineru-parse --test-connection
```

It resolves the PDF from Zotero storage, derives the numeric attachment ID (the
cache directory name) from a read-only copy of `zotero.sqlite`, calls MinerU, and
writes a **plugin-compatible** cache — the plugin reuses it instead of re-parsing.

`--mode local` targets a self-hosted `mineru-api` at `--local-base`
(default `http://127.0.0.1:8000`).

---

## Why a web fetch is not merely slower

1. **Token cost.** An arXiv HTML page is far larger than a bounded section read.
2. **Fidelity.** MinerU gives structured markdown with section spans, figure
   captions, and equations; raw HTML is noisy and loses table structure.
3. **Reproducibility.** The cache is a stable artifact; a URL can change (v1→v2).
4. **Version drift.** The cache records the exact parsed version in
   `_llm_source.json`; a re-fetch silently gives you whatever is current.

## What this protocol does *not* claim

Most web fetches are legitimate — reading a paper you do not own is not a
mistake. The goal is to eliminate **re-reading what is already local**, not to
ban the web. When the corpus is empty, `ABSENT` is the normal answer and
fetching is correct.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No MinerU cache found` | Paper not parsed | `mineru-parse "<ref>"` |
| `is not in the Zotero library` | Metadata absent | Import first (`arxiv_fetch.py import`) |
| `PDF file not found` | Linked (not stored) attachment | Fetch the PDF manually, use `--pdf` |
| `Ambiguous` | Multi-chapter book parent | Add `--match "Chapter N"` |
| `SKIP=already parsed` | Cache exists | Use `--overwrite` only if stale |
| HTTP 429 on parse | MinerU daily quota | Resume tomorrow; cache is preserved |
| `upload failed: HTTP 403` | Presigned URL signed without Content-Type | Fixed in client; do not add Content-Type |

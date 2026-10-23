# Paper Lookup

Local-first access to the paper corpus. This skill is `invocation: natural`, so
it loads whenever a task mentions a paper — the point is to make the cheap path
visible before the expensive one is taken.

## Flow

```bash
bash ~/scholar-agent/scripts/agent.sh gate paper-lookup
```

## Hard rule

Run the preflight **before** any `web_fetch` / `web_search` for a paper:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py paper-status "<ref>"
```

`<ref>` is an arXiv ID, DOI, paper URL, Zotero key, or title.

| STATE | Action |
|---|---|
| `LOCAL_READY` | Read the local cache. **Do not fetch.** |
| `LOCAL_UNPARSED` | `mineru-parse "<ref>"`, then read |
| `IN_LIBRARY_NO_PDF` | Locate/attach the PDF, then parse |
| `ABSENT` | Fetching is correct; import afterwards |

## Reading

Read section by section, never in one call — a whole paper crowds out everything
else, and a truncated read silently loses the end:

```bash
python3 ~/scholar-agent/scripts/literature/zotero_cli.py mineru outline "<ref>"
python3 ~/scholar-agent/scripts/literature/zotero_cli.py mineru read "<ref>" --section 0 --scope self
```

Reading *every* section is expected when writing a literature note (see
`zotero-paper-to-obsidian`); what must never happen is paying for the whole paper
in one tool result.

Full protocol: `~/scholar-agent/shared/protocols/literature-preflight.md`.

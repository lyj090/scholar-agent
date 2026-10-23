# Security policy

## Scope

This project runs locally and talks to three endpoints: Zotero's local API on
`127.0.0.1:23119`, the MinerU API, and arXiv. The realistic risks are:

- **A credential committed by accident.** Covered below.
- **Paper content treated as instructions.** A PDF is untrusted input. The
  skills read it as data; nothing in this repository executes content taken
  from a paper.
- **A private note reaching the public site.** The publication path is built
  to make this fail closed rather than open — see
  `docs/literature/publication-workflow.md`.

## Reporting

Open a [private security advisory](../../security/advisories/new) rather than a
public issue. Please include the version or commit, the command you ran, and
what you expected instead.

Expect an acknowledgement within a few days. This is a personal project
maintained in spare time, not a funded one — there is no bounty.

## Secrets

Nothing in this repository requires a credential to install, and the
example configuration contains no real values. If you find what looks like a
live key:

1. Do not paste it into an issue.
2. Report the file and line through the advisory link above.

If you are the maintainer and a key did leak: rotate it first. Rewriting
history does not un-leak anything that was already public, and GitHub keeps
unreachable objects readable by SHA for some time after a force-push.

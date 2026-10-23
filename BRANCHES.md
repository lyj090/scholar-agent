# Branch model

This repository has two branches that never share a commit. They are not
`main` and a short-lived feature branch; they are two different audiences.

| Branch | Remote | History | Audience |
|---|---|---|---|
| `main` | `origin` — private | Full development history | The maintainer |
| `public` | `public` — public | **One commit, no shared ancestor** | Anyone |

```
main      ──●──●──●──●──●──●──●   (development, private)
                   no common ancestor
public    ──●                     (single squashed snapshot, public)
```

## Why not one repository with a private branch

GitHub's visibility is **per repository**, not per branch. A private repository
cannot expose one public branch, and a public repository cannot hide one private
branch. Making `main` public and keeping `public` private would not work in
either direction.

The other reason is history itself. `main` contains a development log — a
third-party plugin's internals, one machine's library, decisions that were
reversed. `public` is built so that none of it can be recovered:

- **One commit.** There is no earlier commit to inspect.
- **No shared ancestor.** The two histories are unrelated, so nothing in `main`
  is reachable from `public`.
- **Force-pushed.** Each rebuild replaces the branch rather than appending.

That last point matters for a subtler reason. Force-pushing does **not** delete
old objects: a commit that was once pushed stays readable by SHA for some time,
and GH Archive permanently records the push event. So the guarantee is not "old
public commits are erased"; it is "we never publish what should stay private, and
every rebuild starts from an orphan commit so nothing accumulates."

## What is excluded from `public`

The exclusion list lives in [`scripts/deploy/build-public.sh`](scripts/deploy/build-public.sh)
and nowhere else. Today:

| Path | Why |
|---|---|
| `docs/devlog/` | Development history: names a third-party plugin and its internals, describes one machine's library |
| `.ai/` | Local agent scratch: reports, research notes, run logs |
| `*.log`, `.pytest_cache/`, `.ruff_cache/` | Machine-local runtime output |
| `*.local.yaml`, `*.local.json`, `.zotero_env` | Machine-local configuration; the env file is credential-bearing |
| `shared/repo-path-index*` | One machine's repository paths |

Everything else in `main` is copied verbatim, so a file added under `src/` or
`docs/` appears in `public` without anyone remembering to add it.

## Publishing

```bash
# 1. Land your work on main
git checkout main
git commit ...

# 2. See what would change, without touching anything
bash scripts/deploy/build-public.sh --check

# 3. Rebuild the public branch (a single orphan commit)
bash scripts/deploy/build-public.sh

# 4. Inspect it before it leaves the machine
git log --oneline
git ls-tree -r --name-only HEAD | grep -E 'devlog|\.local\.|\.ai/'   # expect nothing

# 5. Push
git push --force public public:main
```

The script refuses to run on a dirty tree: a snapshot taken mid-edit includes
half-finished work, and the resulting commit relates to nothing you can point at
later.

`--push` combines steps 3 and 5. Use it once you trust the exclusion list; use
3-then-5 while reviewing it.

## The version tag must be moved with the branch

`public` is force-pushed, so a tag created earlier still points at the **old**
commit. The Releases panel then shows nothing, because the tag is not on the
default branch.

```bash
git tag -d v0.1.0
git push --delete public v0.1.0
git tag -a v0.1.0 -m "0.1.0" <new-sha>
git push public v0.1.0
```

Then recreate the Release in the web UI against the new tag. This step is easy to
forget because the branch push succeeds and the repository looks correct.

## Working on the public branch directly

Do not. `public` is generated: an edit there is lost at the next rebuild. Fix the
source on `main` and rebuild.

The one exception is the initial creation of the branch, which
`build-public.sh` performs with `git checkout --orphan`.

## Checking what a visitor sees

Clone the public remote anonymously — no credentials, no local config — and run
the parts that need nothing installed:

```bash
cd /tmp && git clone https://github.com/<you>/scholar-agent.git check
cd check
git log --oneline | wc -l          # expect 1
ls docs/devlog 2>/dev/null         # expect: no such directory
python3 -m pytest scripts/tests -q # expect the suite to pass
bash scripts/agent.sh list         # expect the skill groups to resolve
```

Do that before announcing a release, not after.

## What this changes

<!-- One sentence. What behaviour is different afterwards? -->

## Why

<!-- What problem does it solve? Link the issue if there is one. -->

## Checks

- [ ] `python3 -m pytest scripts/tests -q` passes
- [ ] `bash scripts/deploy/lint-skills.sh` reports no errors
- [ ] `bash scripts/agent.sh doctor` still reports a healthy core
- [ ] If I changed `skills/`, I ran `bash scripts/deploy/generate.sh` and did
      not hand-edit any `*-skills/` tree

## Notes for review

<!-- Anything you were unsure about, or deliberately left out of scope. -->

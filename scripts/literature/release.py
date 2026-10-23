#!/usr/bin/env python3
"""Create a reviewed source snapshot without Git history or local settings.

The snapshot includes current tracked and nonignored source changes. Run the
repository checks first. Identity terms are private configuration, not source.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts/lib'))
from workflow_config import load_config

TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|ntn_[A-Za-z0-9]{25,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config'); p.add_argument('--output', required=True)
    args = p.parse_args()
    cfg = load_config(args.config)
    terms = cfg.get('privacy', {}).get('release_terms', [])
    if not terms or any(not isinstance(t, str) or not t.strip() for t in terms):
        raise ValueError('release-vocabulary-required')
    dest = Path(args.output).expanduser().resolve()
    if dest.exists() or dest.is_relative_to(ROOT) or ROOT.is_relative_to(dest):
        raise ValueError('fresh-external-output-required')
    files = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
    pending = []
    for rel in sorted(set(filter(None, files))):
        source = ROOT/rel
        if not source.exists(): continue
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise ValueError('release-symlink-requires-review')
        if not source.is_file(): raise ValueError('release-file-required')
        if any(part.startswith('.git') or part in {'node_modules', '__pycache__'} for part in Path(rel).parts) and rel != '.gitignore':
            # .github contains public CI code; .git itself is never in ls-files.
            if not rel.startswith('.github/'): raise ValueError('release-private-file')
        if re.search(r'(?:\.local\.(?:ya?ml|json)|\.env|\.pyc|\.log)$', rel):
            raise ValueError('release-private-file')
        data = source.read_bytes()
        try: text = data.decode('utf-8')
        except UnicodeError: raise ValueError('release-binary-requires-review')
        value = (rel + '\n' + text).casefold()
        if any(t.casefold() in value for t in terms) or TOKEN.search(text):
            raise ValueError('release-privacy-rule')
        pending.append((rel, data, source.stat().st_mode & 0o777))
    # Only validated bytes enter the new directory; no .git, remotes or history.
    dest.mkdir(parents=True)
    manifest = []
    for rel, data, mode in pending:
        target = dest/rel; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data); target.chmod(mode)
        manifest.append({'file': rel, 'sha256': hashlib.sha256(data).hexdigest()})
    (dest/'RELEASE-MANIFEST.json').write_text(json.dumps({'schema': 1, 'files': manifest}, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'ok': True, 'files': len(manifest), 'snapshot': str(dest), 'git_history': False}))


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        rule = str(exc) if re.fullmatch('[a-z-]+', str(exc)) else 'release-failed'
        print(json.dumps({'ok': False, 'rule': rule}), file=sys.stderr); sys.exit(1)

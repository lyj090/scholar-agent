#!/usr/bin/env python3
"""Deterministic Zotero → source note → reviewed public site orchestration.

Maintains only one private YAML/JSON. No model calls, Git writes or deployment.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from workflow_config import load_config
from zotero.note_structure import validate_note
from zotero.figure_identity import validate_figures


def cli(*args, capture=False):
    return subprocess.run([sys.executable, str(ROOT/'scripts/literature/zotero_cli.py'), *args],
                          check=True, text=True, capture_output=capture)


def snapshot(cfg):
    vault = Path(cfg['obsidian']['vault']).expanduser().resolve()
    rel = Path(cfg.get('garden', {}).get('config_snapshot', '.scripts/publishing.yaml'))
    if rel.is_absolute() or '..' in rel.parts or not str(rel).startswith('.scripts/'):
        raise ValueError('snapshot-path-must-be-private')
    target = vault/rel
    # Resolve parent symlinks before any write outside the selected vault.
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.resolve().is_relative_to(vault) or target.is_symlink():
        raise ValueError('snapshot-path-escape')
    garden = {k: v for k, v in cfg.get('garden', {}).items()
              if k in {'title', 'base_url', 'include', 'sources'}}
    data = {'publication_schema': 2, 'garden': garden, 'privacy': {k: v for k, v in cfg.get('privacy', {}).items() if k in {'require_terms', 'terms', 'private_hosts'}}}
    import yaml
    text = '# Generated from private workflow config; do not edit manually.\n' + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    if not target.exists() or target.read_text(encoding='utf-8') != text:
        fd, tmp = tempfile.mkstemp(prefix='.publishing-', dir=target.parent)
        try:
            with os.fdopen(fd, 'w') as f: f.write(text)
            os.replace(tmp, target)
        finally:
            Path(tmp).unlink(missing_ok=True)
    target.chmod(0o600)
    return vault, target


def publish(cfg, output=None):
    vault, config = snapshot(cfg)
    repo = Path(cfg['garden']['repo']).expanduser().resolve()
    run = Path(output).expanduser().resolve() if output else Path(tempfile.mkdtemp(prefix='paper-public-run-'))
    if output:
        if run.exists(): raise ValueError('output-must-be-fresh')
        run.mkdir(parents=True)
    stage, artifact, quartz = run/'content', run/'public', run/'quartz.config.yaml'
    def execute(args, env=None):
        with (run/'build.log').open('a') as log:
            subprocess.run(args, cwd=repo, env=env, check=True, stdout=log, stderr=log)
    script = str(repo/'scripts/publication.mjs')
    execute(['node', script, 'export', '--config', str(config), '--source', str(vault),
             '--output', str(stage), '--overlay', str(repo/'content-extras'), '--quartz-config', str(quartz)])
    execute(['npx', 'quartz', 'build', '-d', str(stage), '-o', str(artifact), '--concurrency', '4'],
            env={**os.environ, 'QUARTZ_CONFIG_PATH': str(quartz)})
    (artifact/'.nojekyll').touch()
    execute(['node', script, 'audit', '--config', str(config), '--content', str(stage), '--output', str(artifact)])
    print(json.dumps({'ok': True, 'artifact': str(artifact), 'log': str(run/'build.log'),
                      'deployed': False}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', help='Private YAML/JSON; defaults to PAPER_WORKFLOW_CONFIG')
    sub = p.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare', help='Resolve exact cache then prepare metadata/assets')
    prepare.add_argument('ref')
    finish = sub.add_parser('finish', help='Validate/write note, then verify Zotero metadata and assets')
    finish.add_argument('key'); finish.add_argument('--content-file', required=True)
    finish.add_argument('--force', action='store_true')
    check = sub.add_parser('check', help='Offline note/figure validation')
    check.add_argument('--note', required=True); check.add_argument('--pdf')
    sub.add_parser('snapshot', help='Derive the private CI snapshot from canonical config')
    pub = sub.add_parser('publish', help='Export → build → audit into a fresh local artifact')
    pub.add_argument('--output')
    args = p.parse_args()
    if args.config: os.environ['PAPER_WORKFLOW_CONFIG'] = str(Path(args.config).expanduser().resolve())
    cfg = load_config()
    if args.command == 'prepare':
        resolved = json.loads(cli('mineru', 'resolve', args.ref, '--json', capture=True).stdout)
        key = resolved.get('parent_item_key')
        if not key: raise ValueError('exact-parent-item-required')
        cli('prepare-note', key, '--attachment-key', resolved['attachment_key'], '--json')
    elif args.command == 'finish':
        cli('write-note', args.key, '--content-file', args.content_file, *(['--force'] if args.force else []))
        cli('verify-note', args.key, '--json')
    elif args.command == 'check':
        content = Path(args.note).read_text(encoding='utf-8')
        issues = validate_note(content) + validate_figures(content, args.note, args.pdf)
        print(json.dumps({'ok': not issues, 'issues': issues}))
        return int(bool(issues))
    elif args.command == 'snapshot':
        snapshot(cfg); print('{"ok": true, "snapshot": "updated"}')
    elif args.command == 'publish': publish(cfg, args.output)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError:
        print('{"ok": false, "rule": "workflow-step-failed"}', file=sys.stderr); sys.exit(1)
    except (ValueError, KeyError, OSError):
        print('{"ok": false, "rule": "workflow-input-invalid"}', file=sys.stderr); sys.exit(1)

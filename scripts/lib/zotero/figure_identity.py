"""Validate reviewed figure identities independently of extraction filenames."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote

# A printed asset is usually a Figure, but papers also number Tables, Algorithms
# and Equations, with Arabic or Roman numerals. A bare number means Figure, so a
# reviewed ledger written before this extension stays valid.
FIGURE_ID = r"[A-Z]?(?:\.\d+|\d+)(?:\.\d+)?"
IDENT = rf"(?:{FIGURE_ID}|[IVX]+)"
LABEL = r"(Figure|Fig\.|图|Table|Algorithm|Equation|表|算法|式)"
KIND = {"figure": "fig", "fig": "fig", "图": "fig",
        "table": "table", "表": "table",
        "algorithm": "algo", "algo": "algo", "算法": "algo",
        "equation": "eq", "eq": "eq", "式": "eq"}
MENTION_RE = re.compile(rf"(?P<label>{LABEL})\s*(?P<ident>{IDENT})(?![A-Za-z0-9])", re.I)
LEDGER_RE = re.compile(rf"\s*(?:(?P<label>{LABEL})\s*)?(?P<ident>{IDENT})\s*", re.I)
# Balanced-bracket image scanner: alt text may contain citation refs like [35].
IMG_RE = re.compile(r"!\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)]+)\)")


def _canonical(kind_word: str | None, ident: str) -> tuple[str, str]:
    kind = KIND.get((kind_word or "fig").lower().rstrip(".").strip(), "fig")
    return (kind, ident.upper())


def _mentioned_ids(alt: str) -> set:
    return {_canonical(m.group('label'), m.group('ident')) for m in MENTION_RE.finditer(alt)}


def _ledger_ids(record) -> set:
    ids = set()
    for entry in record["figures"]:
        assert isinstance(entry, str)
        match = LEDGER_RE.fullmatch(entry)
        assert match
        ids.add(_canonical(match.group('label'), match.group('ident')))
    return ids


def validate_figures(content: str, note_path: str | Path, pdf_path=None) -> list[str]:
    if not re.search(r"^note_contract:\s*2\s*$", content, re.M):
        return []
    root = Path(note_path).parent.resolve()
    issues = set()
    for alt, url in IMG_RE.findall(content):
        if re.match(r"https?://", url):
            issues.add("figure-must-be-local-reviewed-asset")
            continue
        target = (root / unquote(url.strip('<>'))).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            issues.add("figure-file-invalid")
            continue
        try:
            ledger = json.loads((target.parent / 'figure-ledger.json').read_text(encoding='utf-8'))
            source_hash = ledger.get('source_pdf_sha256', '')
            assert ledger.get('schema') == 2 and re.fullmatch(r'[a-f0-9]{64}', source_hash)
            records = [a for a in ledger['assets'] if a['filename'] == target.name]
            assert len(records) == 1
            record = records[0]
            assert record['status'] == 'verified'
            assert record['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
            assert isinstance(record['evidence']['page'], int) and record['evidence']['page'] > 0
            assert record['evidence']['method'] == 'rendered-pdf-visual-comparison'
            assert record['figures']
            assert _ledger_ids(record) == _mentioned_ids(alt)
            if pdf_path:
                assert source_hash == hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
        except (OSError, ValueError, KeyError, TypeError, AssertionError):
            issues.add('figure-review-required')
    return sorted(issues)

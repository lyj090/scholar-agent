#!/usr/bin/env python3
"""Optional dependencies must degrade one feature, not the whole CLI.

Embedding retrieval needs torch, sentence-transformers and numpy. Those live in
`requirements-retrieval.txt` because most users never need them. The module was
imported at load time, so on a base install *every* arXiv command died with a
ModuleNotFoundError traceback — including `find`, which only uses BM25.

This is the kind of bug a maintainer never sees, because their machine has the
optional packages installed. These tests simulate the base install.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RETRIEVER = REPO_ROOT / "scripts" / "lib" / "arxiv" / "retriever.py"


class OptionalImportTest(unittest.TestCase):
    def test_retriever_imports_without_numpy(self) -> None:
        """Blocking numpy must not stop the module from importing."""
        code = f"""
import sys, builtins
real_import = builtins.__import__
def blocked(name, *a, **kw):
    if name.split('.')[0] in ('numpy', 'torch', 'sentence_transformers'):
        raise ImportError("blocked for test")
    return real_import(name, *a, **kw)
builtins.__import__ = blocked
sys.path.insert(0, {str(REPO_ROOT / 'scripts' / 'lib')!r})
sys.path.insert(0, {str(REPO_ROOT / 'scripts' / 'lib' / 'arxiv')!r})
import retriever
assert retriever._EMBEDDING_IMPORT_ERROR is not None, "expected the error to be recorded"
print("imported")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("imported", result.stdout)

    def test_embedding_use_gives_an_actionable_message(self) -> None:
        """Using the missing feature must explain how to install it."""
        code = f"""
import sys, builtins
real_import = builtins.__import__
def blocked(name, *a, **kw):
    if name.split('.')[0] in ('numpy', 'torch', 'sentence_transformers'):
        raise ImportError("blocked for test")
    return real_import(name, *a, **kw)
builtins.__import__ = blocked
sys.path.insert(0, {str(REPO_ROOT / 'scripts' / 'lib')!r})
sys.path.insert(0, {str(REPO_ROOT / 'scripts' / 'lib' / 'arxiv')!r})
import retriever
try:
    retriever._require_embedding_support()
except RuntimeError as exc:
    msg = str(exc)
    assert "requirements-retrieval.txt" in msg, msg
    assert "BM25" in msg, msg
    print("actionable")
else:
    raise SystemExit("expected RuntimeError")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("actionable", result.stdout)

    def test_bm25_path_does_not_need_embedding(self) -> None:
        """`find` uses BM25; it must not touch the embedding module."""
        code = f"""
import sys, builtins
real_import = builtins.__import__
def blocked(name, *a, **kw):
    if name.split('.')[0] in ('numpy', 'torch', 'sentence_transformers'):
        raise ImportError("blocked for test")
    return real_import(name, *a, **kw)
builtins.__import__ = blocked
sys.path.insert(0, {str(REPO_ROOT / 'scripts' / 'lib')!r})
sys.path.insert(0, {str(REPO_ROOT / 'scripts' / 'lib' / 'arxiv')!r})
import retriever
# BM25 ranking must not require the optional stack.
assert retriever.rank_papers_by_bm25 is not None
print("bm25-ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bm25-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()

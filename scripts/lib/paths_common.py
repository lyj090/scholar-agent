#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared path discovery / patch helpers for setup_paths.py and check_paths.py."""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATHS_LOCAL = os.path.join(REPO_ROOT, "shared", "paths.local.yaml")
PATHS_EXAMPLE = os.path.join(REPO_ROOT, "shared", "config", "paths.example.yaml")
PATHS_DISCOVERY_LOCAL = os.path.join(REPO_ROOT, "shared", "paths-discovery.local.yaml")

DEFAULT_SCAN_ROOTS = ("~/Documents", "~/Docs", "~/文档", "~")
DEFAULT_MAX_DEPTH = 5
VAULT_MARKER_FILES = ("00-Vault-Entry.md",)
OBSIDIAN_SUBDIRS = {
    "literature_notes": "02-Literature/ZoteroNotes",
    "inbox": "00-Inbox",
    "concepts_moc": "03-Concepts",
}
#: Canonical Zotero local-API address. Single source of truth: this module is
#: the lower layer (stdlib only) and ``zotero/config.py`` imports it, so the
#: deploy tooling and the runtime cannot drift apart again. ``127.0.0.1`` is
#: deliberate over ``localhost`` — on a host that resolves ``localhost`` to IPv6
#: first, Zotero (IPv4-only) would be reported as unreachable.
DEFAULT_ZOTERO_API = "http://127.0.0.1:23119"

OBSIDIAN_PATH_KEYS = frozenset(
    {"vault", "literature_notes", "inbox", "concepts_moc"}
)


def expand_path(p: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p.strip())))


def to_tilde_path(p: str) -> str:
    p = os.path.abspath(p)
    home = os.path.expanduser("~")
    if p == home:
        return "~"
    if p.startswith(home + os.sep):
        return "~" + p[len(home) :]
    return p


def _dir_writable(p: str) -> bool:
    try:
        rp = os.path.realpath(p)
        return os.path.isdir(rp) and os.access(rp, os.W_OK)
    except OSError:
        return False


def _try_yaml_load(path: str) -> dict[str, Any] | None:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError:
        return None
    return data if isinstance(data, dict) else None


def load_discovery_config() -> dict[str, Any]:
    """Load the optional vault-discovery override.

    Only a machine-local file is read. A committed ``*.example`` used to be
    consulted as a fallback, but every key in it duplicated the built-in
    defaults below verbatim, so it changed no behaviour — it only created a
    second place to edit. Configure by copying the defaults into
    ``shared/paths-discovery.local.yaml``.
    """
    return _try_yaml_load(PATHS_DISCOVERY_LOCAL) or {}


def discovery_scan_roots() -> list[str]:
    cfg = load_discovery_config()
    roots = cfg.get("vault_scan_roots")
    if isinstance(roots, list) and roots:
        return [str(r) for r in roots]
    return list(DEFAULT_SCAN_ROOTS)


def discovery_max_depth() -> int:
    cfg = load_discovery_config()
    depth = cfg.get("max_depth")
    if isinstance(depth, int) and depth > 0:
        return depth
    return DEFAULT_MAX_DEPTH


def discovery_marker_files() -> tuple[str, ...]:
    cfg = load_discovery_config()
    markers = cfg.get("vault_marker_files")
    if isinstance(markers, list) and markers:
        return tuple(str(m) for m in markers)
    return VAULT_MARKER_FILES


def discovery_subdirs() -> dict[str, str]:
    cfg = load_discovery_config()
    sub = cfg.get("obsidian_subdirs")
    if isinstance(sub, dict) and sub:
        return {str(k): str(v) for k, v in sub.items()}
    return dict(OBSIDIAN_SUBDIRS)


def discovery_zotero_api() -> str:
    cfg = load_discovery_config()
    zt = cfg.get("zotero")
    if isinstance(zt, dict):
        api = zt.get("local_api")
        if isinstance(api, str) and api.strip():
            return api.strip()
    return DEFAULT_ZOTERO_API


@dataclass
class VaultCandidate:
    path: str
    score: int
    reasons: list[str] = field(default_factory=list)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, VaultCandidate):
            return NotImplemented
        return (self.score, self.path) < (other.score, other.path)


def _should_skip_dir(name: str) -> bool:
    if name.startswith("."):
        return True
    low = name.lower()
    return low in {
        "node_modules",
        ".git",
        ".cache",
        ".venv",
        "venv",
        "__pycache__",
        "build",
        "devel",
        "dist",
    }


def _score_vault_dir(vault: str, markers: tuple[str, ...], subdirs: dict[str, str]) -> VaultCandidate:
    reasons: list[str] = []
    score = 0
    for marker in markers:
        if os.path.isfile(os.path.join(vault, marker)):
            score += 100
            reasons.append(f"marker:{marker}")
            break
    for key, rel in subdirs.items():
        sub = os.path.join(vault, rel)
        if os.path.isdir(sub):
            score += 15
            reasons.append(f"subdir:{key}")
    if _dir_writable(vault):
        score += 10
        reasons.append("writable")
    else:
        score -= 1000
        reasons.append("not_writable")
    return VaultCandidate(path=vault, score=score, reasons=reasons)


def discover_vaults() -> list[VaultCandidate]:
    """Scan configured roots; return vault candidates sorted by score desc."""
    markers = discovery_marker_files()
    subdirs = discovery_subdirs()
    max_depth = discovery_max_depth()
    seen: set[str] = set()
    candidates: list[VaultCandidate] = []

    for root_raw in discovery_scan_roots():
        root = expand_path(root_raw)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, _filenames in os.walk(root, topdown=True, followlinks=False):
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
            depth = dirpath[len(root) :].count(os.sep)
            if depth > max_depth:
                dirnames.clear()
                continue

            has_marker = any(
                os.path.isfile(os.path.join(dirpath, m)) for m in markers
            )
            has_structure = (
                os.path.isdir(os.path.join(dirpath, "00-Inbox"))
                and os.path.isdir(os.path.join(dirpath, "02-Literature"))
            )
            if not has_marker and not has_structure:
                continue

            try:
                real = os.path.realpath(dirpath)
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)

            cand = _score_vault_dir(real, markers, subdirs)
            if cand.score > 0:
                candidates.append(cand)

    candidates.sort(key=lambda c: (-c.score, c.path))
    return candidates


def derive_obsidian_paths(vault: str) -> dict[str, str]:
    vault_exp = expand_path(vault)
    subdirs = discovery_subdirs()
    out: dict[str, str] = {"vault": to_tilde_path(vault_exp)}
    for key, rel in subdirs.items():
        sub = os.path.join(vault_exp, rel)
        if os.path.isdir(sub):
            out[key] = to_tilde_path(sub)
    return out


def probe_zotero_api(url: str, timeout: float = 1.5) -> bool:
    try:
        req = urllib.request.Request(url.rstrip("/") + "/", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def load_paths_local() -> dict[str, Any]:
    try:
        from .workflow_config import load_config
    except ImportError:
        from workflow_config import load_config
    return load_config()


def vault_path_valid(vault_raw: str | None) -> bool:
    if not vault_raw or not str(vault_raw).strip():
        return False
    vault = expand_path(str(vault_raw))
    return os.path.isdir(vault) and _dir_writable(vault)


def pick_vault_candidate(
    candidates: list[VaultCandidate],
    *,
    min_score: int = 100,
) -> VaultCandidate | None:
    if not candidates:
        return None
    best = candidates[0]
    if best.score < min_score:
        return None
    if len(candidates) > 1 and candidates[1].score == best.score:
        return None
    return best


def build_obsidian_zotero_values(vault: str) -> tuple[dict[str, str], dict[str, str]]:
    obs = derive_obsidian_paths(vault)
    api = discovery_zotero_api()
    zot: dict[str, str] = {"local_api": api}
    cfg = load_paths_local()
    zt_old = cfg.get("zotero") if isinstance(cfg.get("zotero"), dict) else {}
    for key in ("user_id", "inbox_collection_key"):
        val = zt_old.get(key)
        if val is not None and str(val).strip():
            zot[key] = str(val).strip()
        else:
            zot[key] = ""
    return obs, zot


def patch_paths_yaml(
    template_path: str,
    dest_path: str,
    obsidian: dict[str, str],
    zotero: dict[str, str],
) -> None:
    with open(template_path, encoding="utf-8") as f:
        lines = f.readlines()

    in_obsidian = False
    in_zotero = False
    out: list[str] = []

    def replace_section_value(key: str, new_val: str, section: str) -> str:
        if section == "obsidian" and key in obsidian:
            return obsidian[key]
        if section == "zotero" and key in zotero:
            return zotero[key]
        return ""

    for line in lines:
        stripped = line.strip()
        if stripped == "obsidian:":
            in_obsidian, in_zotero = True, False
            out.append(line)
            continue
        if stripped == "zotero:":
            in_obsidian, in_zotero = False, True
            out.append(line)
            continue
        if stripped == "skills:":
            in_obsidian, in_zotero = False, False
            out.append(line)
            continue

        m = re.match(r"^(\s*)([a-z_]+):\s*(.*)$", line)
        if m and (in_obsidian or in_zotero):
            indent, key, _rest = m.groups()
            section = "obsidian" if in_obsidian else "zotero"
            new_val = replace_section_value(key, "", section)
            if new_val:
                if new_val.startswith("http"):
                    out.append(f"{indent}{key}: {new_val}\n")
                elif "'" in new_val or '"' in new_val:
                    escaped = new_val.replace("'", "''")
                    out.append(f"{indent}{key}: '{escaped}'\n")
                else:
                    out.append(f"{indent}{key}: {new_val}\n")
                continue

        out.append(line)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as f:
        f.writelines(out)


def ensure_utf8_stdio(errors: str = "replace") -> None:
    """Make stdout/stderr emit UTF-8 instead of dying on non-ASCII output.

    On Windows, and on any POSIX host with LANG=C, Python's stdio uses the
    locale encoding. Printing an em dash or a box-drawing character then raises
    UnicodeEncodeError from inside a progress or summary line — the command has
    already done its work and still exits as a traceback. `errors="replace"`
    keeps output readable on a console that genuinely cannot render the glyph,
    instead of substituting a crash for a character.

    Safe to call more than once, and harmless where stdout is already UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (ValueError, OSError):  # detached or non-reconfigurable stream
            pass

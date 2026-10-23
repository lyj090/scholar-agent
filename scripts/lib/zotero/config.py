#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — Configuration for the Zotero/MinerU CLI.

Everything here is **discovered by default**; ``shared/paths.local.yaml`` only
exists to *override* a discovery result. That inversion is the point: a new
machine needs no YAML at all, and the file stops being a checklist of 8 values
that must be kept in sync by hand.

Discovery order for each value:

    explicit YAML  ->  environment variable  ->  probe the machine  ->  default

Everything discovered is reported once via :func:`explain` so a surprising
result is diagnosable rather than mysterious.

NOT responsible for: API calls, cache parsing, CLI routing.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Repo root: scripts/lib/zotero/config.py → 4 levels up
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_SCRIPT_DIR)))
DEFAULT_CONFIG = os.environ.get("PAPER_WORKFLOW_CONFIG", os.path.join(REPO_ROOT, "shared", "paths.local.yaml"))

#: Zotero's local API is always on this port; the port never varies. Imported
#: from ``paths_common`` (the lower layer, stdlib only) so the deploy tooling
#: that resolves and writes this value cannot disagree with the runtime default.
#: They had drifted: this module used ``127.0.0.1`` and paths_common used
#: ``localhost``, which is not the same address on an IPv6-first host.
try:  # ``scripts/lib`` is on sys.path for every CLI entry point.
    from paths_common import DEFAULT_ZOTERO_API as DEFAULT_API
except ImportError:  # pragma: no cover - only when run outside the repo tree
    DEFAULT_API = "http://127.0.0.1:23119"
#: Zotero accepts ``0`` as "the locally logged-in user", so the numeric id is
#: only needed by callers that echo it — it is not a setting worth requiring.
DEFAULT_USER_ID = "0"
#: Directory name the MinerU caches live under, inside the Zotero data dir.
MINERU_SUBDIR = "llm-for-zotero-mineru"
#: Firefox profile prefs, where Zotero records its data directory.
PREFS_GLOB = os.path.expanduser("~/.zotero/zotero/*/prefs.js")


@dataclass
class ZoteroConfig:
    """Resolved configuration for Zotero CLI operations."""

    api_url: str
    user_id: str
    inbox_collection_key: str
    mineru_cache: str
    literature_notes: str
    vault: str
    #: Which source each value came from, for `--explain` and debugging.
    sources: dict = field(default_factory=dict)

    @property
    def data_dir(self) -> str:
        """Zotero data directory (the MinerU cache dir's parent)."""
        return os.path.dirname(self.mineru_cache)

    @property
    def sqlite_path(self) -> str:
        return os.path.join(self.data_dir, "zotero.sqlite")

    def to_dict(self) -> dict:
        return {
            "zotero.local_api": self.api_url,
            "zotero.user_id": self.user_id,
            "zotero.inbox_collection_key": self.inbox_collection_key,
            "zotero.mineru_cache": self.mineru_cache,
            "zotero.data_dir": self.data_dir,
            "obsidian.vault": self.vault,
            "obsidian.literature_notes": self.literature_notes,
        }


def _expand(value: str) -> str:
    """Expand ~ and environment variables, return an absolute path."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(str(value).strip())))


# ----------------------------------------------------------------------
#  YAML (override layer)
# ----------------------------------------------------------------------


def _parse_yaml_flat(path: str) -> dict[str, str]:
    """Read direct Zotero/Obsidian scalars using the shared YAML/JSON loader."""
    from workflow_config import load_config
    data = load_config(path)
    return {f"{section}.{key}": str(value)
            for section in ("zotero", "obsidian")
            for key, value in data.get(section, {}).items()
            if isinstance(value, (str, int, float)) and not isinstance(value, bool)}


# ----------------------------------------------------------------------
#  Discovery probes
# ----------------------------------------------------------------------


def _zotero_data_dir() -> str | None:
    """Read Zotero's data directory out of its Firefox profile prefs.

    This is the single fact that cannot be guessed: the user may have moved it
    (``extensions.zotero.dataDir``). Everything else about Zotero derives from
    it or from the fixed local API port.
    """
    for path in sorted(glob.glob(PREFS_GLOB)):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        match = re.search(r'dataDir",\s*"([^"]+)"', text)
        if match:
            return _expand(match.group(1))
    # Fall back to the conventional location when Zotero has never run.
    conventional = os.path.expanduser("~/Zotero")
    return conventional if os.path.isdir(conventional) else None


def _api_reachable(url: str) -> bool:
    """True when Zotero's local API answers.

    Probes ``/api/`` rather than ``/``: the server returns **404** for the bare
    root, and ``HTTPError`` is a subclass of ``URLError``, so probing the root
    reported a running Zotero as unreachable. Any HTTP status at all means the
    port is serving something.
    """
    try:
        request = urllib.request.Request(url.rstrip("/") + "/api/", method="GET")
        with urllib.request.urlopen(request, timeout=1.5) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as error:
        # A 4xx/5xx still proves the port is live.
        return 400 <= error.code < 600
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _discover_inbox_key(api_url: str, user_id: str) -> str | None:
    """Find the inbox collection by name through the local API.

    Only consulted when the YAML does not pin one, so a user who renamed their
    inbox to something without "inbox" in it can still set it explicitly.
    """
    try:
        url = f"{api_url.rstrip('/')}/api/users/{user_id}/collections"
        with urllib.request.urlopen(url, timeout=3) as resp:
            rows = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    candidates = []
    for row in rows or []:
        data = row.get("data", {}) if isinstance(row, dict) else {}
        name = str(data.get("name") or "").strip()
        key = data.get("key") or row.get("key")
        if key and "inbox" in name.casefold():
            candidates.append((name.casefold(), key))
    # Deterministic: alphabetical by name, then key.
    return sorted(candidates)[0][1] if candidates else None


def _discover_vault() -> tuple[str | None, str | None, dict, list[str]]:
    """Locate the Obsidian vault and its literature-notes subdir.

    Returns ``(vault, literature_notes, derived, other_candidates)``.

    Reuses ``paths_common.discover_vaults`` so the YAML and the deploy scripts
    agree on what a vault looks like instead of each carrying its own heuristic.

    Ties are reported rather than resolved. Two vaults can score identically
    (both have the marker file and the standard subdirs), and since notes are
    *written* to this path, silently picking one risks editing the wrong library.
    """
    try:
        sys.path.insert(0, os.path.dirname(_SCRIPT_DIR))
        from paths_common import derive_obsidian_paths, discover_vaults

        candidates = discover_vaults()
        if not candidates:
            return None, None, {}, []
        best = candidates[0]
        derived = derive_obsidian_paths(best.path)
        others = [c.path for c in candidates[1:] if c.score >= best.score]
        return (
            _expand(derived["vault"]),
            _expand(derived["literature_notes"]) if derived.get("literature_notes") else None,
            derived,
            others,
        )
    except Exception:
        return None, None, {}, []


# ----------------------------------------------------------------------
#  Loader
# ----------------------------------------------------------------------


def _pick(
    yaml_data: dict[str, str],
    key: str,
    env: str | None,
    discover,
    default: str | None,
    sources: dict,
) -> str | None:
    """Resolve one setting: YAML → env → probe → default."""
    value = yaml_data.get(key)
    if value:
        sources[key] = "yaml"
        return value
    if env and os.environ.get(env):
        sources[key] = f"env:{env}"
        return os.environ[env]
    if discover is not None:
        found = discover()
        if found:
            sources[key] = "discovered"
            return found
    if default:
        sources[key] = "default"
        return default
    sources[key] = "missing"
    return None


def load_config(config_path: str | None = None) -> ZoteroConfig:
    """Resolve configuration, discovering anything the YAML does not pin.

    A missing YAML file is **not** an error — it only means nothing is
    overridden. Genuinely required values that cannot be discovered (the vault,
    because the user may have several) raise with a message naming the exact
    YAML line to add.
    """
    path = config_path or DEFAULT_CONFIG
    yaml_data = _parse_yaml_flat(path) if os.path.isfile(path) else {}
    sources: dict[str, str] = {}

    data_dir = _pick(
        yaml_data, "zotero.data_dir", "ZOTERO_DATA_DIR", _zotero_data_dir, None, sources
    )
    api_url = _pick(
        yaml_data, "zotero.local_api", "ZOTERO_LOCAL_API", None, DEFAULT_API, sources
    )
    if not _api_reachable(api_url):
        # Keep going: reads may still work offline via the SQLite copy, and a
        # hard failure here would break exactly the case that matters most
        # (Zotero not running).
        print(
            f"[WARN] Zotero local API not responding at {api_url}",
            file=sys.stderr,
        )

    user_id = _pick(
        yaml_data, "zotero.user_id", "ZOTERO_USER_ID", None, DEFAULT_USER_ID, sources
    )

    mineru_cache = _pick(
        yaml_data,
        "zotero.mineru_cache",
        "ZOTERO_MINERU_CACHE",
        (lambda: os.path.join(data_dir, MINERU_SUBDIR)) if data_dir else None,
        None,
        sources,
    )

    inbox_key = _pick(
        yaml_data,
        "zotero.inbox_collection_key",
        "ZOTERO_INBOX_KEY",
        lambda: _discover_inbox_key(api_url, user_id),
        None,
        sources,
    )

    vault_disc, notes_disc, derived, other_vaults = _discover_vault()
    vault = _pick(
        yaml_data, "obsidian.vault", "OBSIDIAN_VAULT", lambda: vault_disc, None, sources
    )
    literature_notes = _pick(
        yaml_data,
        "obsidian.literature_notes",
        "OBSIDIAN_LITERATURE_NOTES",
        lambda: notes_disc or (derived.get("literature_notes") if derived else None),
        None,
        sources,
    )

    # A tied vault is reported, not silently resolved: notes are written to this
    # path, so picking wrong edits the wrong library. Machines with one vault
    # (the common case) never see this.
    if other_vaults and sources.get("obsidian.vault") == "discovered":
        print(
            f"[WARN] {len(other_vaults) + 1} Obsidian vaults match equally. Using:\n"
            f"         {vault}\n"
            "       Also matched:\n"
            + "".join(f"         {p}\n" for p in other_vaults)
            + "       Pin the intended one in shared/paths.local.yaml:\n"
            "         obsidian:\n"
            "           vault: <path>",
            file=sys.stderr,
        )

    problems = []
    if not vault:
        problems.append(
            "obsidian.vault — no Obsidian vault found. Set it in paths.local.yaml\n"
            "    obsidian:\n      vault: ~/path/to/Vault"
        )
    if not literature_notes:
        problems.append(
            "obsidian.literature_notes — not found under the vault.\n"
            "    obsidian:\n      literature_notes: ~/path/to/Vault/02-Literature/ZoteroNotes"
        )
    if not mineru_cache:
        problems.append(
            "zotero.mineru_cache — Zotero's data directory could not be located.\n"
            "    zotero:\n      data_dir: ~/Zotero"
        )
    if problems:
        print("[ERROR] Cannot resolve configuration:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        print(
            f"\n  Config file: {path}"
            + ("" if os.path.isfile(path) else "  (does not exist — create it)"),
            file=sys.stderr,
        )
        sys.exit(1)

    config = ZoteroConfig(
        api_url=api_url.rstrip("/"),
        user_id=user_id,
        inbox_collection_key=inbox_key or "",
        mineru_cache=_expand(mineru_cache),
        literature_notes=_expand(literature_notes),
        vault=_expand(vault),
        sources=sources,
    )

    for label, value in (
        ("vault", config.vault),
        ("literature_notes", config.literature_notes),
        ("mineru_cache", config.mineru_cache),
    ):
        if not os.path.isdir(value):
            print(f"[WARN] Directory not found: {label} = {value}", file=sys.stderr)

    return config


def explain(config: ZoteroConfig) -> str:
    """Render resolved settings with their source, for debugging a surprise."""
    lines = ["Resolved configuration (yaml > env > discovered > default):"]
    for key, value in config.to_dict().items():
        lines.append(f"  {key:32s} = {value}")
        lines.append(f"  {'':32s}   from {config.sources.get(key, '?')}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual debugging aid
    print(explain(load_config()))

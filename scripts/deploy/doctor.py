#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doctor.py — one command that answers "is my install healthy?".

Every other entry point assumes its own prerequisites and fails on the one it
needs. That is correct for a skill being invoked, and unhelpful for a person
who has just cloned the repository and does not yet know which parts apply to
them. `doctor` is the read-only counterpart: it inspects the machine, reports
each capability as ready / partial / off, and never writes anything.

The distinction it is built around: **the repository works without Zotero,
Obsidian or MinerU**. Only the literature skills need a library. A newcomer
must be able to see that difference instead of reading it in a paragraph.

Exit codes:
    0  core works (literature may be off — that is a valid install)
    1  core is broken (a required dependency is missing)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

from paths_common import ensure_utf8_stdio  # noqa: E402

# doctor prints em dashes and box glyphs; on a non-UTF-8 console that raises
# inside the report. Configure the stream before any output.
ensure_utf8_stdio()

READY = "ready"
PARTIAL = "partial"
OFF = "off"
BROKEN = "broken"

_SYMBOL = {READY: "OK  ", PARTIAL: "~   ", OFF: "--  ", BROKEN: "FAIL"}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""
    required: bool = False
    items: list[str] = field(default_factory=list)


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def check_python() -> Check:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        return Check("python", READY, f"{major}.{minor}", required=True)
    return Check(
        "python",
        BROKEN,
        f"{major}.{minor} — needs 3.10+ for `X | None` annotations",
        fix="install Python 3.10 or newer",
        required=True,
    )


def check_pyyaml() -> Check:
    try:
        import yaml  # noqa: F401

        version = getattr(yaml, "__version__", "?")
        return Check("pyyaml", READY, f"{version}", required=True)
    except ImportError:
        return Check(
            "pyyaml",
            BROKEN,
            "not installed — configuration and every skill gate need it",
            fix="pip install pyyaml",
            required=True,
        )


def check_requests() -> Check:
    try:
        import requests  # noqa: F401

        return Check("requests", READY, getattr(requests, "__version__", "?"))
    except ImportError:
        return Check(
            "requests",
            OFF,
            "not installed — needed only for Zotero writes, MinerU and arXiv",
            fix="pip install requests",
        )


def check_skills() -> Check:
    """Every skill must exist as source and in each generated tree.

    This is a structural check, not a full lint: doctor has to stay fast enough
    that people run it. `bash scripts/deploy/lint-skills.sh` is the thorough one.
    """
    source = ROOT / "skills"
    if not source.is_dir():
        return Check("skills", BROKEN, "skills/ is missing", required=True)
    skills = sorted(p.name for p in source.iterdir() if p.is_dir())
    if not skills:
        return Check("skills", BROKEN, "no skills found", required=True)

    trees = ("claude-skills", "codex-skills", "cursor-skills", "grok-skills", "dsh-skills")
    missing: list[str] = []
    for tree in trees:
        base = ROOT / tree
        if not base.is_dir():
            missing.append(f"{tree}/ (not generated)")
            continue
        for skill in skills:
            if not (base / skill).exists():
                missing.append(f"{tree}/{skill}")

    if missing:
        return Check(
            "skills",
            PARTIAL,
            f"{len(skills)} skills, {len(missing)} generated entries missing",
            fix="bash scripts/deploy/generate.sh",
            required=True,
        )
    return Check(
        "skills", READY, f"{len(skills)} skills across {len(trees)} trees", required=True
    )


def check_tests() -> Check:
    """Import-time health only: a full run belongs in CI, not in doctor."""
    # -p no:cacheprovider keeps doctor from writing .pytest_cache, and
    # --co with -q is the cheapest way to prove the suite imports.
    code, out = _run(
        [
            sys.executable, "-m", "pytest", "scripts/tests",
            "-q", "--collect-only", "-p", "no:cacheprovider",
        ],
        timeout=120,
    )
    last = out.splitlines()[-1] if out else ""
    # "no tests ran" means collection found nothing — never report that as ready.
    if code == 0 and "collected" in last and "no tests ran" not in last:
        return Check("tests", READY, last)
    return Check(
        "tests",
        PARTIAL,
        last or "collection failed",
        fix="python3 -m pytest scripts/tests -q   (needs Python 3.10+)",
    )


def check_zotero() -> Check:
    try:
        from zotero.config import _api_reachable, load_config
    except ImportError as exc:
        return Check("zotero", OFF, f"config module unavailable ({exc})")

    api = os.environ.get("ZOTERO_LOCAL_API", "http://127.0.0.1:23119")
    running = _api_reachable(api)
    try:
        config = load_config()
    except SystemExit:
        return Check(
            "zotero",
            OFF,
            "no library configured",
            fix="python3 scripts/deploy/setup_paths.py --apply",
        )

    if running:
        return Check("zotero", READY, f"{config.api_url} reachable")
    if not running:
        return Check(
            "zotero",
            PARTIAL,
            f"no API at {api} — reads still work from the local database",
            fix="start Zotero (needed for writes, not for reads)",
        )
    return Check("zotero", PARTIAL, "library database present")


def check_mineru() -> Check:
    """Ask the catalog how many parsed caches exist; no network."""
    try:
        from zotero.config import load_config
        from zotero.mineru_catalog import MineruCatalog
    except ImportError:
        return Check("mineru", OFF, "catalog module unavailable")

    try:
        config = load_config()
    except SystemExit:
        return Check("mineru", OFF, "no cache directory configured")

    cache_dir = Path(config.mineru_cache)
    if not cache_dir.is_dir():
        return Check(
            "mineru",
            OFF,
            f"no cache at {cache_dir}",
            fix="point zotero.mineru_cache at your MinerU cache, or parse on demand",
        )

    try:
        catalog = MineruCatalog(str(cache_dir), api=None, config=config)
        records = list(catalog.records())
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return Check("mineru", PARTIAL, f"cache unreadable ({exc})")

    if not records:
        return Check(
            "mineru",
            OFF,
            "cache directory empty — nothing parsed yet",
            fix="python3 scripts/literature/zotero_cli.py mineru-parse <ref>",
        )
    return Check(
        "mineru",
        READY,
        f"{len(records)} parsed caches (figures, tables, equations available)",
    )


def check_vault() -> Check:
    try:
        from zotero.config import load_config
    except ImportError:
        return Check("obsidian", OFF, "config module unavailable")
    try:
        config = load_config()
    except SystemExit:
        return Check(
            "obsidian",
            OFF,
            "no vault configured",
            fix="python3 scripts/deploy/setup_paths.py --apply",
        )
    # Check the vault separately from the notes directory. Discovery can supply
    # a plausible notes path even when the configured vault is wrong, and
    # reporting READY in that case hides the mistake that matters: notes would
    # be written to the wrong library.
    vault = Path(config.vault)
    if not vault.is_dir():
        return Check(
            "obsidian",
            OFF,
            f"vault not found: {vault}",
            fix="set obsidian.vault in shared/paths.local.yaml",
        )
    notes = Path(config.literature_notes)
    if not notes.is_dir():
        return Check(
            "obsidian",
            OFF,
            f"notes directory missing: {notes}",
            fix="set obsidian.literature_notes in shared/paths.local.yaml",
        )
    count = len(list(notes.glob("*.md")))
    return Check("obsidian", READY, f"{count} notes in {notes.name}")


def check_agents() -> Check:
    """Which agent tools are present on this machine, so deploy has a target."""
    found = []
    for name, path in (
        ("claude", "~/.claude"),
        ("codex", "~/.codex"),
        ("cursor", "~/.cursor"),
        ("grok", "~/.grok"),
        ("dsh", "~/.dsh"),
    ):
        if Path(path).expanduser().is_dir():
            found.append(name)
    if found:
        return Check("agents", READY, "installed: " + ", ".join(found))
    return Check(
        "agents",
        PARTIAL,
        "no agent tool directories found",
        fix="./deploy.sh --all   (creates the trees; install a tool to use them)",
    )


def check_network_optional() -> Check:
    """Report whether anything needs the network. Nothing does, by default."""
    return Check(
        "network",
        OFF,
        "not required — every read path works offline",
    )


CHECKS = (
    check_python,
    check_pyyaml,
    check_requests,
    check_skills,
    check_tests,
    check_zotero,
    check_mineru,
    check_vault,
    check_agents,
    check_network_optional,
)


@contextlib.contextmanager
def _quiet_stderr():
    """Silence sub-imports that print setup advice; doctor prints its own."""
    saved = sys.stderr
    sys.stderr = open(os.devnull, "w")
    try:
        yield
    finally:
        sys.stderr.close()
        sys.stderr = saved


#: Probe function -> the label shown to the user. `check_vault` reports under
#: "obsidian" because that is the capability a person recognises.
_DISPLAY_NAME = {check_vault: "obsidian"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report whether this installation is healthy. Writes nothing.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    # Every probe below may call load_config(), which prints an ASCII error block
    # to stderr and exits when a machine is not configured. doctor reports that
    # itself, in one line, so the block is suppressed rather than duplicated.
    with _quiet_stderr():
        checks = []
        for fn in CHECKS:
            try:
                checks.append(fn())
            except Exception as exc:  # noqa: BLE001 - a probe failing is data
                # A probe that raises is itself a finding. Catching here is what
                # lets doctor run on a half-installed machine — the situation it
                # exists for — instead of dying on the first missing module.
                name = _DISPLAY_NAME.get(fn, fn.__name__.removeprefix("check_"))
                checks.append(
                    Check(name, PARTIAL, f"probe failed: {type(exc).__name__}: {exc}")
                )
    core_ok = all(c.status != BROKEN for c in checks if c.required)

    if args.json:
        print(
            json.dumps(
                {
                    "core_ok": core_ok,
                    "checks": [
                        {
                            "name": c.name,
                            "status": c.status,
                            "detail": c.detail,
                            "fix": c.fix,
                            "required": c.required,
                        }
                        for c in checks
                    ],
                },
                indent=2,
            )
        )
        return 0 if core_ok else 1

    print("scholar-agent doctor\n")
    for check in checks:
        tag = "required" if check.required else "optional"
        print(f"  {_SYMBOL[check.status]} {check.name:<9} {check.detail}")
        if check.fix and check.status in (BROKEN, PARTIAL, OFF):
            print(f"       {'':<9} fix: {check.fix}")

    print()
    literature = [c for c in checks if c.name in ("zotero", "mineru", "obsidian")]
    lit_ready = all(c.status == READY for c in literature)
    if core_ok and lit_ready:
        print("Core is healthy and the literature flow is fully configured.")
    elif core_ok:
        off = [c.name for c in literature if c.status != READY]
        print("Core is healthy. The literature flow is not configured "
              f"({', '.join(off)}) — that is a valid install, not an error.")
    else:
        broken = [c.name for c in checks if c.status == BROKEN]
        print(f"Core is broken: {', '.join(broken)}. Fix those first.")

    return 0 if core_ok else 1


if __name__ == "__main__":
    sys.exit(main())

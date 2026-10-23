#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_paths.py — 校验 shared/paths.local.yaml 路径配置

模式:
  全量校验（默认）: 校验所有填写的 obsidian/zotero 路径
  --strict: vault 与 zotero.local_api 必填且有效
  --skill <name>: 只校验该 skill 在 shared/config/skill-paths.yaml 中声明的 key

用法:
  python3 scripts/deploy/check_paths.py                       # 全量校验
  python3 scripts/deploy/check_paths.py --strict              # 必填项校验
  python3 scripts/deploy/check_paths.py --skill zotero-paper-to-obsidian  # 只校验该 skill 所需 key
  python3 scripts/deploy/check_paths.py --dry-run             # 不写文件、不删块

退出码: 0 正常; 1 校验失败; 2 参数错误
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATHS_LOCAL = os.path.join(REPO_ROOT, "shared", "paths.local.yaml")
PATHS_EXAMPLE = os.path.join(REPO_ROOT, "shared", "config", "paths.example.yaml")
# Tracked requirement map (not the gitignored paths.local.yaml — see
# _load_skill_requirements for why that distinction matters).
SKILL_PATHS = os.path.join(REPO_ROOT, "shared", "config", "skill-paths.yaml")

MARK_RESOLVED_BEGIN = "# <<<< check_paths:resolved BEGIN >>>>"
MARK_RESOLVED_END = "# <<<< check_paths:resolved END >>>>"

# ─── Colors ──────────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()


def _c(code: str) -> str:
    return code if _USE_COLOR else ""


C_RESET = _c("\033[0m")
C_GREEN = _c("\033[32m")
C_YELLOW = _c("\033[33m")
C_RED = _c("\033[31m")
C_DIM = _c("\033[2m")
C_BOLD = _c("\033[1m")
C_CYAN = _c("\033[36m")


def _ok(msg: str) -> None:
    print(f"{C_GREEN}✓{C_RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{C_RED}✗{C_RESET} {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"{C_YELLOW}⚠{C_RESET} {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(f"{C_CYAN}▸{C_RESET} {msg}")


# ─── Constants ───────────────────────────────────────────────────────────────

OBSIDIAN_PATH_KEYS = frozenset(
    {
        "vault",
        "literature_notes",
        "inbox",
        "concepts_moc",
    }
)

ZOTERO_OUTPUT_KEYS = frozenset({"local_api"})


def _try_yaml_load(text: str) -> dict[str, Any] | None:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else None


def _load_paths_local_simple(text: str) -> dict[str, Any]:
    obsidian: dict[str, str] = {}
    zotero: dict[str, str] = {}
    section: str | None = None
    skip_resolved = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if skip_resolved:
            if line.startswith((" ", "\t")):
                continue
            skip_resolved = False
        if stripped == "resolved:" or stripped.startswith("resolved:"):
            skip_resolved = True
            continue
        if stripped == "obsidian:":
            section = "obsidian"
            continue
        if stripped == "zotero:":
            section = "zotero"
            continue
        if section and re.match(r"^[a-z_]+:\s*", stripped):
            key, _, rest = stripped.partition(":")
            val = rest.strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            if section == "obsidian":
                obsidian[key] = val
            else:
                zotero[key] = val
    return {"obsidian": obsidian, "zotero": zotero}


def load_paths_local(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    data = _try_yaml_load(text)
    if data is not None:
        return data
    return _load_paths_local_simple(text)


def expand_path(p: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p.strip())))


def path_exists(p: str) -> bool:
    return os.path.exists(p)


def _is_http(s: str) -> bool:
    t = s.strip().lower()
    return t.startswith("http://") or t.startswith("https://")


def _looks_like_fs_path(s: str) -> bool:
    s = s.strip()
    if not s or _is_http(s):
        return False
    return s.startswith("~") or s.startswith("/") or s.startswith(".")


def _dir_writable(p: str) -> bool:
    try:
        rp = os.path.realpath(p)
    except OSError:
        return False
    try:
        return os.path.isdir(rp) and os.access(rp, os.W_OK)
    except OSError:
        return False


def _obsidian_path_errors(
    key: str,
    expanded: str,
    *,
    require_dir: bool = True,
) -> list[str]:
    errs: list[str] = []
    if not path_exists(expanded):
        errs.append(f"paths.local 中 obsidian.{key} 指向的路径不存在: {expanded}")
        return errs
    try:
        rp = os.path.realpath(expanded)
    except OSError as e:
        errs.append(f"obsidian.{key} 无法解析路径: {expanded} ({e})")
        return errs
    if require_dir and not os.path.isdir(rp):
        errs.append(f"obsidian.{key} 须为目录: {expanded}")
        return errs
    if not _dir_writable(expanded):
        errs.append(f"obsidian.{key} 目录存在但不可写: {expanded}")
    return errs


def _merge_discovered(cfg: dict[str, Any]) -> None:
    """Fill missing obsidian/zotero keys from the runtime resolver, in place.

    ``paths.local.yaml`` is an *override* file: values it omits are discovered at
    runtime by ``lib/zotero/config.py``. Validating the raw YAML alone would
    therefore report working values as missing. This delegates to that resolver
    so there is exactly one definition of "what is configured".
    """
    lib_dir = os.path.join(REPO_ROOT, "scripts", "lib")
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
    try:
        from zotero.config import load_config
        resolved = load_config(PATHS_LOCAL)
    except SystemExit:
        return
    except Exception:  # noqa: BLE001 - never let this break the checker
        return

    obs = cfg.setdefault("obsidian", {})
    zt = cfg.setdefault("zotero", {})
    defaults = {
        ("obsidian", "vault"): resolved.vault,
        ("obsidian", "literature_notes"): resolved.literature_notes,
        ("zotero", "local_api"): resolved.api_url,
        ("zotero", "user_id"): resolved.user_id,
        ("zotero", "inbox_collection_key"): resolved.inbox_collection_key,
        ("zotero", "mineru_cache"): resolved.mineru_cache,
    }
    # Vault subdirectories are derived from the vault itself, so they need no
    # YAML entry either — deriving them here keeps the two lists consistent.
    try:
        from paths_common import derive_obsidian_paths

        for key, value in derive_obsidian_paths(resolved.vault).items():
            defaults[("obsidian", key)] = value
    except Exception:  # noqa: BLE001
        pass

    for (section, key), value in defaults.items():
        target = obs if section == "obsidian" else zt
        current = target.get(key)
        if (current is None or not str(current).strip()) and value:
            target[key] = value


def compute_machine_state(
    require_complete: bool,
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str], list[str], list[str], bool]:
    """返回 (obs_out, zot_out, obs_meta, zot_meta, errors, warnings, paths_local_present)。"""
    errors: list[str] = []
    warnings: list[str] = []
    obs_out: dict[str, str] = {}
    obs_meta: dict[str, str] = {}
    zot_out: dict[str, str] = {}
    zot_meta: dict[str, str] = {}

    # The override file is optional (see check_skill). Absence falls through to
    # discovery; the values themselves are what get validated. Never suggest
    # copying the template either — it is comments only, so the copy parses to an
    # empty mapping and every later check reads that as "configured".
    cfg: dict[str, Any] = {"obsidian": {}, "zotero": {}}
    paths_local_present = os.path.isfile(PATHS_LOCAL)
    if paths_local_present:
        try:
            cfg = load_paths_local(PATHS_LOCAL)
        except OSError as e:
            errors.append(f"无法读取 paths.local: {e}")
            return obs_out, zot_out, obs_meta, zot_meta, errors, warnings, True
        except Exception as e:  # noqa: BLE001
            errors.append(f"解析 paths.local 失败: {e}")
            return obs_out, zot_out, obs_meta, zot_meta, errors, warnings, True

    # Fill in whatever the YAML left out from the same discovery the CLI uses, so
    # this tool and the runtime cannot disagree about what is configured. Without
    # this, a one-line override file reports every discovered value as "未配置" —
    # noise that trains the user to ignore the check.
    _merge_discovered(cfg)

    obs = cfg.get("obsidian") if isinstance(cfg.get("obsidian"), dict) else {}
    zt = cfg.get("zotero") if isinstance(cfg.get("zotero"), dict) else {}

    vault_raw2 = obs.get("vault")
    if vault_raw2 and str(vault_raw2).strip():
        vault_exp = expand_path(str(vault_raw2).strip())
        errs = _obsidian_path_errors("vault", vault_exp)
        if errs:
            errors.extend(errs)
        else:
            obs_out["vault"] = vault_exp
            obs_meta["vault"] = "ok"

    for key in sorted(OBSIDIAN_PATH_KEYS):
        if key == "vault":
            continue
        raw = obs.get(key)
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            continue
        if not isinstance(raw, str):
            continue
        expanded = expand_path(raw)
        errs = _obsidian_path_errors(key, expanded)
        if errs:
            errors.extend(errs)
        else:
            obs_out[key] = expanded
            obs_meta[key] = "ok"

    for key in sorted(OBSIDIAN_PATH_KEYS):
        if key == "vault":
            continue
        if key not in obs_out:
            warnings.append(
                f"REMIND: obsidian.{key} 未配置；请在 paths.local 的 obsidian 段手写目录路径。"
            )

    la = zt.get("local_api")
    if la is not None and str(la).strip():
        las = str(la).strip()
        if _is_http(las):
            zot_out["local_api"] = las
            zot_meta["local_api"] = "ok"
        else:
            errors.append("zotero.local_api 须为 http:// 或 https:// 开头的 URL。")

    for zk, raw in sorted(zt.items()):
        if zk in ZOTERO_OUTPUT_KEYS or zk in ("user_id", "inbox_collection_key"):
            continue
        if not isinstance(raw, str) or not raw.strip():
            continue
        if _looks_like_fs_path(raw):
            expanded = expand_path(raw)
            if not path_exists(expanded):
                errors.append(f"zotero.{zk} 路径不存在: {expanded}")
            elif not os.path.isdir(os.path.realpath(expanded)):
                errors.append(f"zotero.{zk} 须为目录: {expanded}")
            elif not _dir_writable(expanded):
                errors.append(f"zotero.{zk} 目录存在但不可写: {expanded}")
            else:
                zot_out[zk] = expanded
                zot_meta[zk] = "ok"

    if require_complete:
        if not obs_out.get("vault"):
            errors.append(
                "obsidian.vault 未解析到有效可写目录：请在 shared/paths.local.yaml 的 obsidian 段手写 vault。"
            )
        la_ok = zot_out.get("local_api")
        if not la_ok or not str(la_ok).strip():
            errors.append(
                "zotero.local_api 未解析到有效值：请在 paths.local 的 zotero 手写 local_api（如 http://localhost:23119）。"
            )
        elif not _is_http(str(la_ok)):
            errors.append("zotero.local_api 须为 http:// 或 https:// 开头的 URL。")

    if not zot_out.get("local_api"):
        warnings.append(
            "REMIND: zotero.local_api 未设置；请在 paths.local 的 zotero.local_api 手写 "
            "http://localhost:23119 等。"
        )

    return obs_out, zot_out, obs_meta, zot_meta, errors, warnings, paths_local_present


def strip_legacy_resolved_block(path: str) -> bool:
    """若存在旧版 check_paths resolved 标记块则整段删除；返回是否做了修改。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return False
    if MARK_RESOLVED_BEGIN not in raw:
        return False
    pattern = re.escape(MARK_RESOLVED_BEGIN) + r"[\s\S]*?" + re.escape(MARK_RESOLVED_END) + r"\n?"
    new_raw, n = re.subn(pattern, "", raw, count=1)
    if n == 0:
        return False
    new_raw = new_raw.rstrip() + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_raw)
    return True


def _parse_skill_requirements(text: str) -> dict[str, list[str]]:
    """Parse the minimal YAML shape used by ``skill-paths.yaml``.

    Hand-parsed on purpose: this checker runs on bare machines where PyYAML may
    be absent, and a per-skill gate that silently stops enforcing because an
    optional library is missing is worse than a small parser. Accepted forms:

        name: []          # no requirements
        name:             # followed by "- key" items
          - key
    """
    out: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            name, _, rest = line.partition(":")
            name = name.strip()
            if not name:
                current = None
                continue
            current = name
            rest = rest.strip()
            out[name] = [] if rest in ("", "[]") else [rest]
            continue
        if current is None:
            continue
        item = line.strip()
        if item.startswith("- "):
            out[current].append(item[2:].strip().strip("\"'"))
    return out


def _load_skill_requirements() -> dict[str, list[str]]:
    """Read the tracked per-skill requirement map.

    This deliberately does *not* read ``paths.local.yaml``. That file is a
    gitignored, machine-local override: a requirement list stored there is
    unknowable on a fresh clone, so this used to return ``None`` for every skill
    and the per-skill check reported "no requirements" — a gate that passed for
    every input, including a vault path that does not exist.

    Requirements are repository policy, so they live in a tracked file.
    """
    if not os.path.isfile(SKILL_PATHS):
        return {}
    try:
        with open(SKILL_PATHS, encoding="utf-8") as f:
            return _parse_skill_requirements(f.read())
    except OSError:
        return {}


def _get_skill_required_keys(skill_name: str) -> list[str] | None:
    """Required dotted keys for ``skill_name``, or None when it is not declared."""
    return _load_skill_requirements().get(skill_name)


def _resolve_dotted_key(cfg: dict[str, Any], dotted: str) -> str | None:
    """解析 'obsidian.vault' 这类 dotted key 的值。"""
    parts = dotted.split(".", 1)
    if len(parts) != 2:
        return None
    section, key = parts
    sec = cfg.get(section)
    if not isinstance(sec, dict):
        return None
    val = sec.get(key)
    if val is None:
        return None
    return str(val).strip() if val else ""


def _setup_hints_for_errors(errors: list[str]) -> list[str]:
    """Return actionable setup hints for common path errors."""
    hints: list[str] = []
    joined = " ".join(errors).lower()
    if "paths.local" in joined or "不存在" in joined and "yaml" in joined:
        # Do NOT suggest a bare `cp` of the template: it is comments only, so it
        # parses to an empty mapping and the gate would then pass on paths that
        # were never configured. `setup_paths.py --apply` discovers and writes
        # real values.
        hints.append("python3 scripts/deploy/setup_paths.py --apply")
        hints.append("python3 scripts/lib/zotero/config.py  # show what is discovered")
    if "obsidian.vault" in joined or "vault" in joined:
        hints.append("python3 scripts/deploy/setup_paths.py --apply  # auto-discover vault")
        hints.append(f"vim {PATHS_LOCAL}  # set obsidian.vault")
    if "zotero.user_id" in joined or "user_id" in joined:
        hints.append("Zotero → Settings → Advanced → userID in data directory")
        hints.append(f"vim {PATHS_LOCAL}  # set zotero.user_id")
    if "zotero.local_api" in joined or "local_api" in joined:
        hints.append("Start Zotero with local API enabled (default http://localhost:23119)")
        hints.append(f"vim {PATHS_LOCAL}  # set zotero.local_api")
    if "mineru_cache" in joined:
        hints.append("See docs/literature/architecture.md for the MinerU cache path")
    if "inbox_collection_key" in joined:
        hints.append("See shared/specs/zotero-inbox-to-library-ai.md for inbox collection key")
    if not hints:
        hints.append(f"vim {PATHS_LOCAL}")
        hints.append("python3 scripts/deploy/check_paths.py --skill <name> --setup-hints")
    hints.append("Full guide: README.md § Requirements")
    return hints


def check_key(dotted: str, *, quiet: bool = False) -> int:
    """Validate a single dotted key (e.g. obsidian.vault). Returns 0 if ready."""
    # Same rule as check_skill: the override file is optional, so judge the
    # resolved value rather than the file's presence.
    cfg: dict[str, Any] = {"obsidian": {}, "zotero": {}}
    if os.path.isfile(PATHS_LOCAL):
        try:
            cfg = load_paths_local(PATHS_LOCAL)
        except Exception as e:  # noqa: BLE001
            if not quiet:
                _fail(f"解析 paths.local 失败: {e}")
            return 1
    _merge_discovered(cfg)
    val = _resolve_dotted_key(cfg, dotted)
    if not val:
        if not quiet:
            _fail(f"{dotted}: 值为空或未配置")
        return 1
    if _is_http(val):
        return 0
    if not _looks_like_fs_path(val):
        return 0
    expanded = expand_path(val)
    section, key = dotted.split(".", 1)
    if section == "obsidian":
        errs = _obsidian_path_errors(key, expanded)
    else:
        errs = []
        if not path_exists(expanded):
            errs.append(f"{dotted}: 路径不存在 → {expanded}")
        elif os.path.isdir(expanded) and not _dir_writable(expanded):
            errs.append(f"{dotted}: 目录不可写 → {expanded}")
    if errs:
        if not quiet:
            for e in errs:
                _fail(e)
        return 1
    if not quiet:
        _ok(f"{dotted} = {expanded}")
    return 0


def _error_keys(errors: list[str]) -> list[str]:
    keys: list[str] = []
    for e in errors:
        if ":" in e:
            keys.append(e.split(":", 1)[0].strip())
    return keys


def check_skill(
    skill_name: str,
    *,
    quiet: bool = False,
    setup_hints: bool = False,
    compact: bool = False,
) -> int:
    """校验单个 skill 声明的路径 key，返回 0=通过，1=失败。"""
    if compact:
        quiet = True

    keys = _get_skill_required_keys(skill_name)
    if keys is None:
        # Undeclared skill: unknown is not the same as "no requirements". Saying
        # OK here would let a typo in skill-paths.yaml silently disable the gate
        # for the skill it was meant to guard.
        if compact:
            print(f"ERR skill_undeclared skill={skill_name} fix=add it to shared/config/skill-paths.yaml")
        elif not quiet:
            _fail(f"skill={C_BOLD}{skill_name}{C_RESET} 未在 shared/config/skill-paths.yaml 中声明")
            print(
                f"  {C_DIM}每个 skill 都必须声明路径需求（无需求写 `[]`），"
                f"否则无法区分「没有需求」和「拼写错误」。{C_RESET}",
                file=sys.stderr,
            )
        return 1

    # A skill with no machine prerequisites runs anywhere: it must not require
    # paths.local.yaml to exist at all. Checking the file first made this gate
    # fail on machines where the skill was perfectly usable.
    if not keys:
        if compact:
            print(f"OK skill={skill_name} paths=none")
        elif not quiet:
            _ok(f"skill={C_BOLD}{skill_name}{C_RESET}: 无路径需求")
        return 0

    # The file is an *override*, so its absence is not a failure. Demanding it
    # made a machine whose paths all auto-discover fail every literature skill,
    # while an empty file — which contains strictly less information — passed.
    # Decide from the resolved configuration instead.
    cfg: dict[str, Any] = {"obsidian": {}, "zotero": {}}
    if os.path.isfile(PATHS_LOCAL):
        try:
            cfg = load_paths_local(PATHS_LOCAL)
        except Exception as e:  # noqa: BLE001
            if compact:
                print(f"ERR paths_parse skill={skill_name} detail={e}")
                print("FIX=fix shared/paths.local.yaml syntax")
            elif not quiet:
                _fail(f"解析 paths.local 失败: {e}")
            return 1

    # Fill whatever the override left out from the same resolver the CLI uses,
    # then judge. Without this, auto-discovered values look unconfigured.
    _merge_discovered(cfg)

    errors: list[str] = []
    resolved: dict[str, str] = {}

    for dotted in keys:
        val = _resolve_dotted_key(cfg, dotted)
        if not val:
            errors.append(f"{dotted}: 值为空或未配置")
            continue

        if _is_http(val):
            resolved[dotted] = val
            continue

        if not _looks_like_fs_path(val):
            resolved[dotted] = val
            continue

        expanded = expand_path(val)
        if not path_exists(expanded):
            errors.append(f"{dotted}: 路径不存在 → {expanded}")
        elif os.path.isdir(expanded) and not _dir_writable(expanded):
            errors.append(f"{dotted}: 目录不可写 → {expanded}")
        else:
            resolved[dotted] = expanded

    if errors:
        if compact:
            missing = ",".join(_error_keys(errors))
            print(f"ERR paths_not_ready skill={skill_name} missing={missing}")
            hints = _setup_hints_for_errors(errors)
            if hints:
                print(f"FIX={hints[0]}")
        elif not quiet:
            _fail(f"skill={C_BOLD}{skill_name}{C_RESET}")
            for e in errors:
                print(f"  {C_RED}•{C_RESET} {e}", file=sys.stderr)
            print(f"  {C_DIM}请编辑 {PATHS_LOCAL} 修正上述路径。{C_RESET}", file=sys.stderr)
            if setup_hints:
                print("  Setup hints:", file=sys.stderr)
                for h in _setup_hints_for_errors(errors):
                    print(f"    {h}", file=sys.stderr)
        return 1

    if compact:
        print(f"OK skill={skill_name}")
    elif not quiet:
        _ok(f"skill={C_BOLD}{skill_name}{C_RESET}")
        for k, v in sorted(resolved.items()):
            print(f"  {C_DIM}{k}{C_RESET} = {v}")
    return 0


def check_strict_core(*, quiet: bool = False) -> int:
    """Vault required; zotero.local_api optional."""
    obs_out, _, _, _, errors, warnings, _ = compute_machine_state(require_complete=False)
    vault_errors = [e for e in errors if "vault" in e.lower()]
    if vault_errors:
        if not quiet:
            for e in vault_errors:
                _fail(e)
        return 1
    if not obs_out.get("vault"):
        if not quiet:
            _fail("obsidian.vault 未配置或无效")
        return 1
    if not quiet:
        _ok("strict-core: obsidian.vault OK")
        for w in warnings:
            _warn(w)
    return 0


def check_strict_literature(*, quiet: bool = False) -> int:
    """Vault + zotero.local_api required for literature deploy."""
    # `present` reports whether the optional override file exists; it is not a
    # readiness condition. Judge the resolved errors and values.
    obs_out, zot_out, _, _, errors, warnings, _present = compute_machine_state(require_complete=True)
    if errors:
        if not quiet:
            for e in errors:
                _fail(e)
        return 1
    if not obs_out.get("vault") or not zot_out.get("local_api"):
        if not quiet:
            _fail("literature deploy requires obsidian.vault and zotero.local_api")
        return 1
    if not quiet:
        _ok("strict-literature: vault + local_api OK")
        for w in warnings:
            _warn(w)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="校验 shared/paths.local.yaml 路径配置"
    )
    ap.add_argument("--dry-run", action="store_true", help="不写文件（不删除旧 resolved 块）")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="vault 与 local_api 必填且路径/URL 有效，否则 exit 1（同 --strict-literature）",
    )
    ap.add_argument(
        "--strict-core",
        action="store_true",
        help="仅校验 obsidian.vault（core 部署用）",
    )
    ap.add_argument(
        "--strict-literature",
        action="store_true",
        help="校验 vault + zotero.local_api（literature 部署用）",
    )
    ap.add_argument(
        "--check-key",
        type=str,
        default=None,
        metavar="DOTTED",
        help="校验单个 key，如 obsidian.vault",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="仅 exit code，无 stdout（供 bootstrap 脚本用）",
    )
    ap.add_argument(
        "--compact",
        action="store_true",
        help="Machine lines only (OK/ERR/FIX); for skill-gate / agents",
    )
    ap.add_argument(
        "--setup-hints",
        action="store_true",
        help="与 --skill 联用，失败时打印 setup 步骤",
    )
    ap.add_argument(
        "--skill",
        type=str,
        default=None,
        help="只校验指定 skill 声明的路径 key（见 shared/config/skill-paths.yaml）",
    )
    args = ap.parse_args()
    quiet = args.quiet

    if args.check_key:
        return check_key(args.check_key, quiet=quiet)

    if args.strict_core:
        return check_strict_core(quiet=quiet)

    if args.strict_literature or args.strict:
        return check_strict_literature(quiet=quiet)

    # --skill 模式：独立流程
    if args.skill:
        return check_skill(
            args.skill,
            quiet=quiet or args.compact,
            setup_hints=args.setup_hints and not args.compact,
            compact=args.compact,
        )

    obs_out, zot_out, meta_obs, meta_zot, errors, warnings, paths_present = compute_machine_state(
        require_complete=args.strict,
    )

    for w in warnings:
        _warn(w)
    for e in errors:
        _fail(e)

    hard_fail = args.strict and bool(errors)
    should_touch_file = (not args.dry_run) and (not hard_fail) and os.path.isfile(PATHS_LOCAL)

    if should_touch_file:
        if strip_legacy_resolved_block(PATHS_LOCAL):
            _info(f"removed legacy resolved block from {PATHS_LOCAL}")
    elif args.dry_run:
        _info("dry-run, not modifying files")
    elif hard_fail:
        _fail("校验失败，未修改 paths.local")

    if not hard_fail:
        for k in sorted(OBSIDIAN_PATH_KEYS):
            p = obs_out.get(k, "")
            if not p:
                print(f"  {C_YELLOW}○{C_RESET} {C_DIM}obsidian.{k}{C_RESET} {C_YELLOW}PENDING{C_RESET}")
            elif path_exists(p) and _dir_writable(p):
                print(f"  {C_GREEN}●{C_RESET} {C_DIM}obsidian.{k}{C_RESET} {p}")
            elif path_exists(p):
                print(f"  {C_RED}●{C_RESET} {C_DIM}obsidian.{k}{C_RESET} {C_RED}NOT_WRITABLE{C_RESET} {p}")
            else:
                print(f"  {C_RED}●{C_RESET} {C_DIM}obsidian.{k}{C_RESET} {C_RED}MISSING{C_RESET} {p}")
        for k in sorted(ZOTERO_OUTPUT_KEYS):
            v = zot_out.get(k, "")
            if not v:
                print(f"  {C_YELLOW}○{C_RESET} {C_DIM}zotero.{k}{C_RESET} {C_YELLOW}PENDING{C_RESET}")
            else:
                print(f"  {C_GREEN}●{C_RESET} {C_DIM}zotero.{k}{C_RESET} {v}")
        for k, v in sorted(zot_out.items()):
            if k in ZOTERO_OUTPUT_KEYS:
                continue
            print(f"  {C_GREEN}●{C_RESET} {C_DIM}zotero.{k}{C_RESET} {v}")
    else:
        for k, p in sorted(obs_out.items()):
            if path_exists(p) and _dir_writable(p):
                print(f"  {C_GREEN}●{C_RESET} {C_DIM}obsidian.{k}{C_RESET} {p}")
            elif path_exists(p):
                print(f"  {C_RED}●{C_RESET} {C_DIM}obsidian.{k}{C_RESET} {C_RED}NOT_WRITABLE{C_RESET} {p}")
            else:
                print(f"  {C_RED}●{C_RESET} {C_DIM}obsidian.{k}{C_RESET} {C_RED}MISSING{C_RESET} {p}")
        for k, v in sorted(zot_out.items()):
            print(f"  {C_DIM}zotero.{k}{C_RESET} = {v}")

    if hard_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

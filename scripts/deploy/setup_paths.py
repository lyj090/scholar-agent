#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_paths.py — 发现本机 Obsidian vault 并写入 shared/paths.local.yaml

作用：仅生成本机路径配置文件（obsidian.* + 默认 zotero.local_api），不部署 skill。
不会填写 zotero.user_id、收藏夹 key 等——需手改 paths.local 或按 check_paths --setup-hints 补齐。

用法:
  python3 scripts/deploy/setup_paths.py              # 发现候选并交互选择
  python3 scripts/deploy/setup_paths.py --discover   # 仅列出候选
  python3 scripts/deploy/setup_paths.py --apply      # 写入 paths.local（交互确认）
  python3 scripts/deploy/setup_paths.py --apply --yes  # 自动选最高分唯一候选
  python3 scripts/deploy/setup_paths.py --apply --vault ~/path  # 指定 vault

退出码: 0 成功; 1 失败; 2 参数错误
"""

from __future__ import annotations

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from lib.paths_common import (  # noqa: E402
    PATHS_EXAMPLE,
    PATHS_LOCAL,
    VaultCandidate,
    build_obsidian_zotero_values,
    discover_vaults,
    expand_path,
    load_paths_local,
    patch_paths_yaml,
    pick_vault_candidate,
    probe_zotero_api,
    to_tilde_path,
    vault_path_valid,
)

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
    print(f"{C_YELLOW}⚠{C_RESET} {msg}")


def _info(msg: str) -> None:
    print(f"{C_CYAN}▸{C_RESET} {msg}")


def print_candidates(candidates: list[VaultCandidate]) -> None:
    if not candidates:
        _warn("未发现符合条件的 Obsidian vault")
        _info("可在 shared/paths-discovery.local.yaml 增加 vault_scan_roots 后重试")
        return
    print(f"\n{C_BOLD}发现的 vault 候选:{C_RESET}\n")
    for i, c in enumerate(candidates[:10], 1):
        reasons = ", ".join(c.reasons)
        print(f"  {C_BOLD}{i}.{C_RESET} score={c.score}  {to_tilde_path(c.path)}")
        print(f"     {C_DIM}{reasons}{C_RESET}")


def resolve_vault(
    candidates: list[VaultCandidate],
    *,
    vault_arg: str | None,
    assume_yes: bool,
) -> str | None:
    if vault_arg:
        vault = expand_path(vault_arg)
        if not vault_path_valid(vault):
            _fail(f"指定的 vault 无效或不可写: {vault}")
            return None
        return vault

    cfg = load_paths_local()
    obs = cfg.get("obsidian") if isinstance(cfg.get("obsidian"), dict) else {}
    existing = obs.get("vault")
    if vault_path_valid(str(existing) if existing else None):
        _info(f"保留已有有效 vault: {to_tilde_path(expand_path(str(existing)))}")
        return expand_path(str(existing))

    picked = pick_vault_candidate(candidates)
    if picked and assume_yes:
        _ok(f"自动选用 vault: {to_tilde_path(picked.path)} (score={picked.score})")
        return picked.path

    if picked and not assume_yes:
        print_candidates(candidates)
        print(f"\n推荐: {C_GREEN}{to_tilde_path(picked.path)}{C_RESET} (score={picked.score})")
        try:
            ans = input("使用推荐 vault? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            _fail("已取消")
            return None
        if ans in ("", "y", "yes"):
            return picked.path

    if not candidates:
        return None

    print_candidates(candidates)
    if assume_yes:
        _fail("存在多个同分候选或置信度不足，无法自动选择；请使用 --vault 指定")
        return None

    try:
        choice = input(f"选择 vault [1-{min(len(candidates), 10)}] 或输入路径: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        _fail("已取消")
        return None

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < min(len(candidates), 10):
            return candidates[idx].path
        _fail("编号超出范围")
        return None

    if choice:
        vault = expand_path(choice)
        if vault_path_valid(vault):
            return vault
        _fail(f"路径无效: {vault}")
        return None

    return None


def apply_paths(vault: str, *, force: bool) -> int:
    cfg = load_paths_local()
    obs_old = cfg.get("obsidian") if isinstance(cfg.get("obsidian"), dict) else {}
    if not force and vault_path_valid(str(obs_old.get("vault") or "")):
        existing_vault = expand_path(str(obs_old["vault"]))
        if existing_vault != expand_path(vault):
            _warn(f"已有 vault 配置，跳过覆盖（使用 --force 强制）: {to_tilde_path(existing_vault)}")
            vault = existing_vault

    obs, zot = build_obsidian_zotero_values(vault)
    template = PATHS_EXAMPLE if os.path.isfile(PATHS_EXAMPLE) else PATHS_LOCAL
    if not os.path.isfile(template):
        _fail(f"缺少模板文件: {PATHS_EXAMPLE}")
        return 1

    if not os.path.isfile(PATHS_LOCAL):
        _info(f"创建 {PATHS_LOCAL}")
    patch_paths_yaml(template, PATHS_LOCAL, obs, zot)
    _ok(f"已写入 {PATHS_LOCAL}")

    for key, val in sorted(obs.items()):
        print(f"  {C_DIM}obsidian.{key}{C_RESET} = {val}")
    api = zot.get("local_api", "")
    print(f"  {C_DIM}zotero.local_api{C_RESET} = {api}")
    if api and probe_zotero_api(str(api)):
        _ok("Zotero local API 可访问")
    else:
        _warn("Zotero local API 未响应（可稍后启动 Zotero 再校验）")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="发现 Obsidian vault 并配置 paths.local.yaml")
    ap.add_argument("--discover", action="store_true", help="仅列出候选 vault")
    ap.add_argument("--apply", action="store_true", help="写入 shared/paths.local.yaml")
    ap.add_argument("--yes", "-y", action="store_true", help="非交互：自动选用唯一高分候选")
    ap.add_argument("--vault", type=str, default=None, help="直接指定 vault 路径")
    ap.add_argument("--force", action="store_true", help="覆盖已有有效 vault 配置")
    args = ap.parse_args()

    candidates = discover_vaults()

    if args.discover and not args.apply:
        print_candidates(candidates)
        return 0 if candidates else 1

    if not args.apply:
        print_candidates(candidates)
        if candidates:
            _info("写入配置: python3 scripts/deploy/setup_paths.py --apply")
        return 0 if candidates else 1

    vault = resolve_vault(candidates, vault_arg=args.vault, assume_yes=args.yes)
    if not vault:
        _fail("未能确定 vault 路径")
        print(f"  请手动编辑: {PATHS_LOCAL}", file=sys.stderr)
        print(f"  或指定: python3 scripts/deploy/setup_paths.py --apply --vault <path>", file=sys.stderr)
        return 1

    return apply_paths(vault, force=args.force)


if __name__ == "__main__":
    sys.exit(main())

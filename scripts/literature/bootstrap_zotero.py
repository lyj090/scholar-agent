#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bootstrap_zotero.py — 一键自动化配置 Zotero 工作流环境。

分阶段执行：
Phase 1: 从 prefs.js 读取 User ID，并从 profile 发现写授权 key。
Phase 2: 通过本地只读 API 自动识别 Inbox Collection Key。
Phase 3: 同步本地索引 (CorpusCache) 并提取科研画像。
Phase 4: 更新环境变量与部署 Skills。

不需要 llm-for-zotero 插件：读走 Local API / sqlite，写走 Zotero 10 原生写 API。
"""

import os
import re
import glob
import subprocess
import sys

# ─── 配置与常量 ───────────────────────────────────────────────────────────────

PREFS_PATTERN = os.path.expanduser("~/.zotero/zotero/*/prefs.js")
PATHS_CONFIG = "shared/paths.local.yaml"
ZSHRC_LOCAL = os.path.expanduser("~/.zshrc.local")
ENV_FILE = ".zotero_env"

def run_command(cmd, shell=False):
    try:
        result = subprocess.run(cmd, shell=shell, capture_output=True, text=True, check=True)
        return result.stdout.strip(), None
    except subprocess.CalledProcessError as e:
        return None, e.stderr.strip()

# ─── Phase 1: 基础凭证提取 ───────────────────────────────────────────────────

def phase_1_get_basic_creds():
    print("\n[Phase 1] 正在扫描 Zotero 配置文件获取基础凭证...")
    creds = {}
    
    # 尝试从 prefs.js 获取
    for path in glob.glob(PREFS_PATTERN):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                # User ID (尝试匹配常见的 key 格式)
                uid_match = re.search(r'zoteroUserId",\s*"(\d+)"', content)
                if uid_match: creds['user_id'] = uid_match.group(1)
        except Exception: continue
    
    # Fallback: 如果 prefs.js 没找到 User ID，尝试从现有的 paths.local.yaml 读
    if 'user_id' not in creds and os.path.exists(PATHS_CONFIG):
        try:
            import yaml
            with open(PATHS_CONFIG, 'r') as f:
                cfg = yaml.safe_load(f)
                uid = cfg.get('zotero', {}).get('user_id')
                if uid: creds['user_id'] = str(uid)
        except Exception: pass

    # 写授权：Zotero 10 的本地 API key 存在 profile 的 localAPIKeys.json，
    # 不再需要从插件 prefs 里抠 MCP token。
    try:
        scripts_lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")
        if scripts_lib not in sys.path:
            sys.path.insert(0, scripts_lib)
        from zotero.local_write import find_remembered_key

        api_key = find_remembered_key()
        if api_key:
            creds['api_key'] = api_key
    except Exception:
        pass

    if 'user_id' in creds:
        print(f"  ✔ User ID={creds['user_id']}")
        if 'api_key' in creds:
            print(f"  ✔ 写授权已就绪: {creds['api_key'][:8]}...")
        else:
            print("  ⚠ 尚未授权写操作（只读可用）。需要写入时运行一次:")
            print("     curl -X POST -H 'Content-Type: application/json' \\")
            print("          -H 'Zotero-Server-ID: <id>' \\")
            print("          -d '{\"appName\":\"scholar-agent\"}' \\")
            print("          http://127.0.0.1:23119/api/local/authorize")
        return creds

    print("  ✘ 未能提取 User ID。请在 Zotero 同步设置中确认已登录。")
    return None

# ─── Phase 2: 自动识别 Inbox ────────────────────────────────────────────────

def phase_2_discover_inbox(creds):
    """Discover Inbox via the read-only Local API, without MCP/UI context."""
    print("\n[Phase 2] 正在通过 Zotero Local API 自动识别 Inbox 文件夹...")
    try:
        scripts_lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")
        if scripts_lib not in sys.path:
            sys.path.insert(0, scripts_lib)
        from zotero.api import ZoteroAPI

        rows = ZoteroAPI("http://127.0.0.1:23119", creds["user_id"]).get_collections()
        candidates = []
        for row in rows:
            data = row.get("data", {}) if isinstance(row, dict) else {}
            name = str(data.get("name") or "").strip()
            key = data.get("key") or (row.get("key") if isinstance(row, dict) else None)
            if key and "inbox" in name.casefold():
                candidates.append((name, key))
        if candidates:
            name, key = sorted(candidates, key=lambda pair: (pair[0].casefold(), pair[1]))[0]
            print(f"  ✔ 自动识别到 Inbox: {name} ({key})")
            return key
    except Exception as error:
        print(f"  ⚠ Local API 查询失败: {error}")

    print("  ⚠ 未能识别到 Inbox 文件夹，请手动在 Zotero 中创建名为 'Inbox' 的收藏。")
    return None

# ─── Phase 3: 构建索引与更新配置 ──────────────────────────────────────────────

def phase_3_update_config_and_index(creds, inbox_key):
    print("\n[Phase 3] 正在更新本地配置与索引...")
    
    # 1. 更新 paths.local.yaml
    if not os.path.exists(PATHS_CONFIG):
        run_command(["python3", "scripts/deploy/setup_paths.py", "--apply"])
    
    try:
        import yaml
        with open(PATHS_CONFIG, 'r') as f:
            config = yaml.safe_load(f)
        
        config['zotero']['user_id'] = creds['user_id']
        if inbox_key:
            config['zotero']['inbox_collection_key'] = inbox_key
            
        with open(PATHS_CONFIG, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
        print(f"  ✔ 已更新 {PATHS_CONFIG}")
    except Exception as e:
        print(f"  ✘ 更新配置文件失败: {e}")

    # 2. 记录写授权（可选）：本地 API key 由 Zotero 持久化在 profile 中，
    #    这里只写进 env 文件便于工具显式引用，不再是唯一来源。
    with open(ENV_FILE, "w") as f:
        if creds.get('api_key'):
            f.write(f"export ZOTERO_LOCAL_API_KEY={creds['api_key']}\n")
    print(f"  ✔ 已生成 {ENV_FILE}")

    # 3. 触发索引同步
    print("  [*] 正在同步科研画像 (find --profile)...")
    run_command(["python3", "scripts/literature/arxiv_fetch.py", "find", "--profile", "--top", "1"])
    print("  ✔ 本地索引同步完成")

# ─── Phase 4: Shell 继承与 Skill 部署 ─────────────────────────────────────────

def phase_4_finalize():
    print("\n[Phase 4] 正在完成最终部署...")
    
    # 1. 写入 .zshrc.local
    block = f'\n# Zotero Auto-Config\nif [ -f "{os.getcwd()}/{ENV_FILE}" ]; then\n    source "{os.getcwd()}/{ENV_FILE}"\nfi\n'
    if os.path.exists(ZSHRC_LOCAL):
        with open(ZSHRC_LOCAL, 'r') as f:
            if "Zotero Auto-Config" not in f.read():
                with open(ZSHRC_LOCAL, 'a') as fa:
                    fa.write(block)
                print(f"  ✔ 已将加载逻辑写入 {ZSHRC_LOCAL}")
    
    # 2. 部署 Skills（部署范围由 deploy.sh 自行探测已安装的平台）
    run_command(["bash", "deploy.sh", "--group", "literature", "--yes"])
    print("  ✔ Zotero 相关技能部署完成")

# ─── 主程序 ───────────────────────────────────────────────────────────────────

def main():
    print("🚀 Zotero 自动化工作流 bootstrap 开始...")
    
    # 安装依赖
    run_command("pip install pyyaml --quiet", shell=True)

    creds = phase_1_get_basic_creds()
    if not creds: return

    inbox_key = phase_2_discover_inbox(creds)
    
    phase_3_update_config_and_index(creds, inbox_key)
    
    phase_4_finalize()

    print("\n✨ Zotero 自动化环境配置成功！")
    print(f"请执行: source {ZSHRC_LOCAL}")
    print("现在你可以直接使用 /skills 中的 arxiv-to-library 等技能了。")

if __name__ == "__main__":
    main()

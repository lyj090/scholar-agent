# shared：跨 Codex / Cursor / Claude / Grok / DSH 的「长真相」

放 **不宜拆成两条 SKILL 重复维护** 的内容。目录与 [`skills/`](../skills/) + [`scripts/`](../scripts/) 对齐。

## 三层结构

| 目录 | 内容 | 读者 |
|------|------|------|
| [`specs/`](specs/) | `*-ai.md` — skill 被触发时 Agent 执行的规格 | Agent（经 skill-gate / SKILL 引用） |
| [`config/`](config/) | 可提交的配置模板与分组（`skill-groups.yaml`, `tag-taxonomy-rules.yaml`, …） | 人类 + deploy/check_paths |
| [`protocols/`](protocols/) | 跨 skill 协议（context budget、路径解析、skill 路由、trace schema） | Agent + 维护者 |

## 本机配置（留在根目录）

以下文件 **gitignore**，每台机器一份，**全部可选**（没有也能跑，值会自动发现）：

| 文件 | 内容 | 谁读 |
|---|---|---|
| `paths.local.yaml` | Zotero / Obsidian 路径覆盖（通常只需 `obsidian.vault`） | `zotero/config.py`、check_paths、setup_paths |
| `paths-discovery.local.yaml` | vault 扫描根/深度/标记文件的覆盖（默认值已内置，仅多 vault 时用） | `paths_common.py`（setup_paths / check_paths） |
| `repo-path-index.local.yaml` | 仓库路径 + remote + 别名；`/research --all`、`search-all`、`gc` 的跨项目注册表 | `research.sh` |

三者都不是"模板复制"关系：默认值写在代码里，这些文件只在你**要覆盖**时才需要存在。

## 人类索引

- 白名单治理：**[allowlist-common.md](allowlist-common.md)** ↔ [`config/allowlist-common.json`](config/allowlist-common.json)

## 不要放

- 密钥、token、机器绝对路径（用 `paths.local.yaml`）
- 仅单工具 3 行就能说清的提示（写在该工具的 `SKILL.md`）

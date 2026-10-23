<div align="center">

# Scholar Agent

**面向 AI 编程助手的本地优先文献工作流。**

<p>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-2563eb?style=flat-square"></a>
  <a href="../../actions/workflows/ci.yml"><img alt="CI" src="https://github.com/lyj090/scholar-agent/actions/workflows/ci.yml/badge.svg"></a>
  <a href="../../releases"><img alt="Version" src="https://img.shields.io/badge/version-0.1.0-2563eb?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2563eb?style=flat-square">
  <img alt="Zotero" src="https://img.shields.io/badge/zotero-10%2B-2563eb?style=flat-square">
  <img alt="Agents" src="https://img.shields.io/badge/agents-5-17171a?style=flat-square">
</p>

找到那篇论文,认真读完,留下经得起追问的笔记,只把能公开的部分发布出去 ——
一份源文件,同时部署到五个 agent。

[English](README.md) · [**简体中文**](README.zh-CN.md)

</div>

---

## 为什么做这个

让 agent 帮你总结一篇论文是容易的。麻烦都在摘要周边,而且当场都不像"出错"。

**它去抓你早就有的东西。** agent 手里是 arXiv ID,库里按 item key 索引,两套标识符
之间没有桥接,于是它上网 —— 哪怕那篇论文已经解析好躺在本地。

**它把整篇论文读进来。** 只想问某一节,却要加载全部;而且文本留在窗口里,之后每一轮
都要为它再付一次。

**你没法核对结果。** 一句话读起来一样顺,不管它来自论文还是来自模型的记忆。错的图
和正确的图带着同样多的自信。

**私有笔记流到公开站点。** 标题"听起来像私人"不构成任何保证。这个错误只有在发布
之后才看得见。

## 怎么解决

**先问本地,再谈联网。** 每个引用先分成四种状态,只有"库里没有"才允许联网。命中
本地的东西不会产生幻觉。

**先看大纲,再读一节。** 论文按节提供,而且每节大小提前可知,所以没人问的部分
根本不会被加载。

**让每条论断可追溯。** 笔记只允许包含原文说过的东西,每条带定位。模型补的内容
单独标记,当成它自己的动作。

**公开与私有机械切分。** 一个显式标记,由导出器切断。不靠标题启发式,也不靠
"记得检查的人"。

**写一次,部署到各处。** 一份源文件生成 Codex、Cursor、Claude Code、Grok 和
Agent Harness 的技能目录,所以它们不会漂移。

**缺什么就说缺什么,不给一堆看不懂的报错。** `doctor` 把每项能力集中报一遍,
安装坏了是一张清单,而不是一段 traceback。

## 技能

六个技能,五个管文献,一个管代码研究。

| 技能 | 作用 |
|---|---|
| `paper-lookup` | 抓之前先查这篇论文本地有没有 |
| `arxiv-to-library` | 找 arXiv 论文、入库、去重 |
| `zotero-inbox-to-library` | 把积压的未整理论文分类归档 |
| `zotero-paper-to-obsidian` | 写一份忠于原文的阅读笔记 |
| `obsidian-zotero-tag-governance` | 让 Obsidian 与 Zotero 的标签保持一致 |
| `research` | 有结构地研究代码,笔记落盘 |

## 快速开始

```bash
git clone git@github.com:<you>/scholar-agent.git
cd scholar-agent
./deploy.sh --all
```

不确定哪些已经配好了?直接问:

```bash
bash scripts/agent.sh doctor
```

它会把每项能力报成 ready / partial / off,并说明缺什么。它不写任何文件。
文献流显示 off 是**正常的安装状态** —— 只有那几个技能需要图库。

这一步生成全部平台目录并软链到位。之后改 `skills/` 一次即可全局生效。

文献类技能需要 Zotero 10+ 和一个配置好的 vault,其余部分两者都不需要。
路径会自动发现 —— 只有多个候选评分相同时才需要手动固定:

```bash
python3 scripts/deploy/setup_paths.py --discover   # 看候选
python3 scripts/deploy/setup_paths.py --apply      # 写入选择
```

## 环境要求

Python 3.10+ 和 PyYAML。文献类技能需要 Zotero 10 或更新 —— 10 是下限,因为更早的
版本没有可写的本地 API。MinerU 可选:没有它只是失去图、表、公式的提取,不影响
阅读和记笔记。

这是一组脚本,不是包。请克隆,不要 `pip install`。

Windows 上可以使用 Python 代码和命令行;部署脚本是 POSIX shell,所以
`deploy.sh` 那一步的软链部署只在 macOS 和 Linux 上有效。

## 文档

- **[文献管线如何工作](docs/literature/architecture.md)** —— Zotero、MinerU 与命令行,
  以及那些实测而非假设出来的约束。
- **[发布公开子集](docs/literature/publication-workflow.md)** —— 笔记如何从本地 vault
  走到站点,同时不泄漏不该发布的内容。
- **[Agent 运行时规则](AGENTS.md)** —— agent 最先读的入口;每个技能的规格在
  [`shared/specs/`](shared/specs/)。
- **[Cursor 技能](docs/setup/cursor-skills.md)** 与
  **[Python 环境](docs/setup/venv-setup.md)** —— 安装细节。
- **[BRANCHES.md](BRANCHES.md)** —— 私有分支与公开分支的关系。

哪个技能负责什么见上表;`bash scripts/agent.sh list` 打印当前检出的权威清单。

## 参与贡献

欢迎提 issue 和 PR。这个仓库同时也是维护者每天在用的环境,所以只服务某台机器
特殊癖好的改动通常不会被合并。[CONTRIBUTING.md](CONTRIBUTING.md) 说明了哪些改动
容易接受。

## 许可

MIT —— 见 [LICENSE](LICENSE)。

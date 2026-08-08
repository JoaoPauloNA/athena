# Athena-MCP 🔱

[![CI](https://github.com/JoaoPauloNA/athena/actions/workflows/ci.yml/badge.svg)](https://github.com/JoaoPauloNA/athena/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**一个 MCP 服务器,统一检测、路由、验证并优化你机器上所有 AI CLI 的使用成本。**

> 💡 Athena 的路由理念受 OmniRoute 概念启发 —— 同样的思想,独立的实现。

🇺🇸 [Read in English](README.md) · 🇧🇷 [Leia em Português](README.pt-BR.md)

---

## Athena-MCP 是什么?

Athena-MCP 把你机器上安装的 AI CLI(Codex、Claude Code、Cursor Agent、Antigravity、OpenCode、Ollama、Kimi、Qwen 等)整合为一个统一编排的"智能体委员会",同时以 **MCP 服务器**(让任何 AI 聊天都能委派任务)和 **Web 管理面板** 两种形式对外提供服务。

Athena 不依赖单一代理,而是:

- **检测** —— 自动发现系统中所有的 AI CLI(macOS / Windows / Linux)
- **路由** —— 基于自动更新的评分表,把每个任务分配给最合适的 provider/模型
- **验证** —— 用真实的项目证据核对执行报告:"测谎仪"能发现代理虚报完成了实际未做的工作
- **节约** —— 让简单任务避开昂贵的重量级模型
- **容灾** —— 通过可配置的 combo 链在多个 provider 之间自动故障转移

## 功能特性

| 功能 | 说明 |
|---|---|
| 🔍 **跨平台 CLI 扫描器** | 扫描 PATH 以及 `~/.local/bin`、Homebrew、npm 全局目录、Scoop、cargo、go、flatpak、snap……23 个已知 CLI 即使未安装也会列出 |
| 📊 **管理面板** | Provider、模型、combo、用量统计和"各角色最佳模型"表,地址 `localhost:7100` |
| 🏆 **模型评分表** | 按角色(前端、后端、推理、速度)0–10 分,**每周从实时排行榜自动更新**(SWE-bench、GPQA、Design Arena) |
| 🕵️ **测谎仪(验证器)** | 由廉价/免费模型(优先 OpenCode 免费模型)将每份报告与 git 证据交叉核对;虚假报告退回修正一次,仍虚假则升级给编排者决策 |
| 💡 **`recommend` 工具** | 描述任务,返回本机已安装的最佳 provider+模型及理由 |
| 💰 **经济路由** | 评估任务复杂度;简单任务自动排除重量级模型 |
| 🔄 **Combo 故障转移** | Provider 链支持重试、每步独立模型和超时策略 |
| 📜 **10 项报告契约** | 执行器只返回精简的结构化报告,编排者的上下文保持干净 |

## 快速开始

```bash
git clone https://github.com/JoaoPauloNA/athena.git
cd athena
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

启动管理面板:

```bash
python -c "from athena.dashboard.app import run_dashboard; run_dashboard()"   # API 端口 :20129
cd frontend && npx vite --port 7100                                            # UI 端口 :7100
```

作为 MCP 服务器(stdio)运行,在你的 MCP 客户端配置中加入:

```json
{
  "mcpServers": {
    "athena": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "athena.mcp_server"],
      "cwd": "/path/to/athena"
    }
  }
}
```

## MCP 工具

| 工具 | 用途 |
|---|---|
| `list_providers` | 已安装/可用的 CLI、模型、角色和评分 |
| `ask_provider` | 向指定 provider 发送任务(`verify=true` 启用测谎仪) |
| `run_combo` | 通过故障转移链执行 prompt |
| `deliberate` | 并行咨询多个代理 |
| `recommend` | "这个任务该叫谁?" —— 评分 × 已安装模型 × 复杂度 |
| `refresh_models` | 重新扫描各 CLI 的模型目录 |
| `list_usage` | 每个 provider 的调用计数和 token 估算 |

## 验证流程

```
编排者 → 执行器 CLI → 10 项报告
              │
              ▼
   验证器(可用的最便宜模型,
   绝不与执行器使用同一 provider)
   核对 git status/diff + 报告中提到的文件
              │
   真实 ──────┴────── 虚假
    │                    │
  接受              退回执行器修正(1 次)
                         │
              真实 ──────┴────── 再次虚假
               │                    │
            接受            升级给编排者
                    (结论 + 证据 + 完整历史)
```

## 文档

- 📙 [架构](docs/zh-CN/architecture.md) · [MCP 工具参考](docs/zh-CN/mcp-tools.md)
- 📘 [Architecture](docs/en/architecture.md) · [MCP tools reference](docs/en/mcp-tools.md)
- 📗 [Arquitetura](docs/pt-BR/arquitetura.md) · [Referência das ferramentas MCP](docs/pt-BR/ferramentas-mcp.md)

## 环境要求

- Python ≥ 3.10
- 至少安装一个 AI CLI(Codex、Claude Code、Cursor Agent、Antigravity、OpenCode、Ollama……)
- Node.js(仅用于面板开发服务器)

## 许可证

MIT © 2026 João Paulo

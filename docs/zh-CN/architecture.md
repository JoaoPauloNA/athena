# 架构

🇺🇸 [EN](../en/architecture.md) · 🇧🇷 [PT-BR](../pt-BR/arquitetura.md)

## 总览

```
┌─────────────────────────────────────────────────────────┐
│ 编排者(任何支持 MCP 的 AI 聊天)                          │
└──────────────┬──────────────────────────┬───────────────┘
               │ stdio (JSON-RPC)         │ HTTP
┌──────────────▼──────────────┐  ┌────────▼───────────────┐
│ athena/mcp_server.py        │  │ athena/dashboard/app.py│
│ 7 个 MCP 工具               │  │ FastAPI :20129         │
└──────────────┬──────────────┘  └────────┬───────────────┘
               │                          │
┌──────────────▼──────────────────────────▼───────────────┐
│ athena/providers.py                                     │
│ ask_provider · ask_provider_verified · combos           │
└──┬─────────┬──────────┬──────────┬─────────┬────────────┘
   │         │          │          │         │
┌──▼───┐ ┌───▼────┐ ┌───▼─────┐ ┌──▼──────┐ ┌▼───────────┐
│bridge│ │contract│ │verifier │ │recommend│ │models/     │
│.py   │ │.py     │ │.py      │ │.py      │ │ratings.py  │
│执行层│ │10 项报 │ │测谎仪   │ │任务→最佳│ │模型目录+   │
│      │ │告契约  │ │         │ │provider │ │评分        │
└──┬───┘ └────────┘ └────┬────┘ └─────────┘ └────────────┘
   │                     │
┌──▼─────────────────────▼────────────────────────────────┐
│ AI CLI:codex · claude · agent · agy · openclaude ·      │
│ opencode · ollama(+ 自动发现及 16 个已知 CLI)           │
└─────────────────────────────────────────────────────────┘
```

## 模块

| 模块 | 职责 |
|---|---|
| `bridge.py` | 子进程执行、PTY、输出清理、**增强 PATH**(即使宿主 GUI 的 PATH 极简,`which` 也能在 `~/.local/bin`、Homebrew、Scoop、npm 全局目录等位置找到 CLI) |
| `providers.py` | CLI 注册表(23 个已知 CLI 的静态目录 + 自动发现)、按 CLI 构建命令、`ask_provider`、`ask_provider_verified` |
| `contract.py` | 注入到每个执行器 prompt 的 10 项报告契约 + 基于正则的轻量格式检查 |
| `verifier.py` | 测谎仪:客观证据(`git status`、`git diff --stat`、报告引用文件是否存在)+ 廉价模型裁决;防串通(验证器 ≠ 执行器 provider);修正循环与二次失败升级 |
| `recommend.py` | 任务分类(角色 × 复杂度)、经济上限(简单任务→仅轻量模型)、在已安装模型中推荐 provider+model |
| `ratings.py` | 按角色 0–10 评分,JSON 缓存 `~/.athena/model_ratings.json`(TTL 7 天),由自动化网络调研任务每周刷新 |
| `models.py` | 各 CLI 的实时模型目录(`--list-models`/`models`)、后备目录、重量分级(light/medium/heavy) |
| `router.py` + `combos.py` | 故障转移链:provider 按序执行、重试、每步独立模型/超时 |
| `agents.py` | 注入 prompt 的命名角色(架构师、评审、反方……) |
| `usage.py` | 本地按 provider 计数(`~/.athena/usage.json`) |
| `dashboard/app.py` | FastAPI 后端 + HTMX/Jinja 模板 + JSON API |

## CLI 检测(跨平台)

1. **增强 PATH** —— 进程 PATH + 各操作系统常见位置:
   - **Windows:** `~/.local/bin`、`%LOCALAPPDATA%\Programs`、npm 全局、WinGet Links、Scoop shims、Chocolatey、pip `--user` Scripts、cargo、go
   - **macOS:** `~/.local/bin`、`/opt/homebrew/bin`、`/usr/local/bin`、npm 全局、cargo、go、bun
   - **Linux:** `~/.local/bin`、`/usr/local/bin`、snap、flatpak exports、npm 全局、cargo、go、bun
2. **名称启发式** —— 整词关键字匹配(剥离 Windows 扩展名:`claude.cmd` → `claude`)
3. **探测** —— 对 `--help`/`--version` 输出按 AI 关键字打分(`.cmd`/`.bat` shim 通过 `cmd /c` 运行)
4. **静态目录** —— 23 个已知 CLI 始终列出;未安装的显示为 *Offline*

## 数据文件(`~/.athena/`)

| 文件 | 内容 |
|---|---|
| `models_catalog.json` | 各 CLI 实时模型列表(TTL 5 天) |
| `model_ratings.json` | 按角色评分,每周从公开排行榜刷新 |
| `combos.json` | 故障转移链 |
| `usage.json` | 调用计数、时长、token 估算 |
| `custom_providers.json` | 用户自定义 provider(覆盖自动发现) |

均可通过 `ATHENA_DATA_DIR`、`ATHENA_MODELS_FILE` 等环境变量配置(见 `athena/config.py`)。

## 设计原则

1. **编排者的上下文是神圣的** —— 执行器只返回精简的 10 项报告,绝不回灌代码。
2. **信任,但要验证** —— 报告必须与项目的客观证据核对,而非照单全收。
3. **先便宜后昂贵** —— 验证使用免费/本地模型;重量级模型仅在复杂度足够时使用(仅建议,绝不阻止)。
4. **有什么用什么** —— 所有路由决策都基于机器上实际安装的 CLI 计算。

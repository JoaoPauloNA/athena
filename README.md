# Athena-MCP 🔱

**OmniRouter for AI-agent CLIs — one MCP server to detect, route, verify and economize across every AI CLI on your machine.**

🇧🇷 [Leia em Português](README.pt-BR.md) · 🇨🇳 [阅读中文](README.zh-CN.md)

---

## What is Athena-MCP?

Athena-MCP turns the AI CLIs installed on your machine (Codex, Claude Code, Cursor Agent, Antigravity, OpenCode, Ollama and more) into a single orchestrated council, exposed both as an **MCP server** (so any AI chat can delegate tasks) and a **web dashboard** for management.

Instead of betting on one agent, Athena:

- **Detects** every AI CLI on your system automatically (macOS, Windows, Linux)
- **Routes** each task to the best provider/model using an auto-updating ratings table
- **Verifies** execution reports against real project evidence — a "lie detector" that catches agents claiming work they never did
- **Economizes** by steering simple tasks away from expensive heavy models
- **Fails over** across providers with configurable combos

## Features

| Feature | Description |
|---|---|
| 🔍 **Cross-platform CLI scanner** | Finds AI CLIs on PATH + `~/.local/bin`, Homebrew, npm global, Scoop, cargo, go, flatpak, snap… 23 known CLIs listed even when not installed |
| 📊 **Dashboard** | Providers, models, combos, usage stats and the "Best per Role" table at `localhost:7100` |
| 🏆 **Model ratings table** | Scores 0–10 per role (frontend, backend, reasoning, speed), refreshed **weekly from live leaderboards** (SWE-bench, GPQA, Design Arena) |
| 🕵️ **Lie detector (verifier)** | A cheap/free model (OpenCode free tier first) cross-checks each report against git evidence; false reports go back for fixing once, then escalate to the orchestrator |
| 💡 **`recommend` tool** | Describe a task, get the best installed provider+model with reasoning |
| 💰 **Economy routing** | Task complexity estimation; heavy models are excluded from suggestions for simple tasks |
| 🔄 **Combos with failover** | Chains of providers with retries, per-step models and timeout policies |
| 📜 **10-topic report contract** | Executors return lean structured reports — the orchestrator's context stays clean |

## Quick start

```bash
git clone https://github.com/JoaoPauloNA/athena.git
cd athena
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Run the dashboard:

```bash
python -c "from athena.dashboard.app import run_dashboard; run_dashboard()"   # API on :20129
cd frontend && npx vite --port 7100                                            # UI on :7100
```

Run as an MCP server (stdio), e.g. in your MCP client config:

```json
{
  "mcpServers": {
    "athena": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "athena.mcp_server"],
      "cwd": "/path/to/athena-mcp"
    }
  }
}
```

## MCP tools

| Tool | Purpose |
|---|---|
| `list_providers` | Installed/available CLIs, models, roles, ratings |
| `ask_provider` | Send a task to one provider (`verify=true` enables the lie detector) |
| `run_combo` | Run a prompt through a failover chain |
| `deliberate` | Ask several agents in parallel |
| `recommend` | "Who should I call for this task?" — ratings × installed models × complexity |
| `refresh_models` | Re-scan CLI model catalogs |
| `list_usage` | Per-provider call counters and token estimates |

## How verification works

```
Orchestrator → Executor CLI → 10-topic report
                     │
                     ▼
        Verifier (cheapest available model,
        never the executor's own provider)
        checks git status/diff + cited files
                     │
        TRUE ────────┴──────── FALSE
         │                       │
      accepted            sent back to executor (1x)
                               │
                     TRUE ─────┴──── FALSE again
                      │                 │
                   accepted      ESCALATE to orchestrator
                                 (verdict + evidence + history)
```

## Documentation

- 📘 [Architecture](docs/en/architecture.md) · [MCP tools reference](docs/en/mcp-tools.md)
- 📗 [Arquitetura](docs/pt-BR/arquitetura.md) · [Referência das ferramentas MCP](docs/pt-BR/ferramentas-mcp.md)
- 📙 [架构](docs/zh-CN/architecture.md) · [MCP 工具参考](docs/zh-CN/mcp-tools.md)

## Requirements

- Python ≥ 3.10
- At least one AI CLI installed (Codex, Claude Code, Cursor Agent, Antigravity, OpenCode, Ollama…)
- Node.js (only for the dashboard dev server)

## License

MIT © 2026 João Paulo

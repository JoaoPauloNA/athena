# Athena-MCP 🔱

[![CI](https://github.com/JoaoPauloNA/athena/actions/workflows/ci.yml/badge.svg)](https://github.com/JoaoPauloNA/athena/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
![Status: alpha](https://img.shields.io/badge/status-alpha%20%2F%20pre--release-orange.svg)

**One MCP server to detect, route, verify and economize across the AI CLIs installed on your machine.**

> 💡 Athena's routing approach is inspired by the OmniRoute concept — same idea, independent implementation.

> ⚠️ **Status: alpha / pre-release.** Athena-MCP is built for controlled local sharing — running on your own machine, or handed to people and machines you already trust. It has not been hardened for public or untrusted exposure. Read [Safety and validation](#safety-and-validation) and [Limitations](#limitations) before relying on it for anything beyond local, cooperative use.

🇧🇷 [Leia em Português](README.pt-BR.md) · 🇨🇳 [阅读中文](README.zh-CN.md)

---

## What is Athena-MCP?

Athena-MCP turns the AI CLIs installed on your machine (Codex, Claude Code, Cursor Agent, Antigravity, OpenCode, Ollama and more) into a single orchestrated council, exposed both as an **MCP server** (so any AI chat can delegate tasks) and a **web dashboard** for management.

Instead of betting on one agent, Athena:

- **Detects** AI CLIs on your system (macOS, Windows, Linux)
- **Routes** each task to a provider/model using a local ratings table crossed with what's installed
- **Verifies** execution reports against real project evidence for the claims it can actually check — never a general proof of correctness
- **Economizes** by steering simple tasks away from expensive heavy models
- **Fails over** across providers with configurable combos, gated by confirmed termination of the previous attempt

## Features

| Feature | Description |
|---|---|
| 🔍 **CLI scanner** | Finds AI CLIs on PATH + `~/.local/bin`, Homebrew, npm global, Scoop, cargo, go, flatpak, snap… on macOS, Linux and Windows; 22 known CLIs listed even when not installed |
| 📊 **Dashboard** | Providers, models, combos, usage stats and the "Best per Role" table at `localhost:7100` — no authentication, local use only |
| 🏆 **Model ratings table** | Scores 0–10 per role (frontend, backend, reasoning, speed), cached locally with a 7-day TTL and a seed fallback; refreshing it from live leaderboards is an external, opt-in job, not something Athena runs automatically |
| 🧪 **Report verifier** | Deterministic layer re-runs whitelisted test/lint commands cited in the report and checks that claimed files exist (no model involved); advisory layer (cheap/free model) triages whatever the deterministic layer can't decide. Neither proves general correctness — see [Verification](docs/en/verification.md) |
| 💡 **`recommend` tool** | Describe a task, get the best installed provider+model with reasoning |
| 💰 **Economy routing** | Task complexity estimation; heavy models are excluded from suggestions for simple tasks |
| 🔄 **Combos with failover** | Chains of providers with retries, per-step models and timeout policies — fallback only proceeds when the service profile allows it and the previous attempt's termination is confirmed |
| 📜 **10-topic report contract** | Executors return lean structured reports — the orchestrator's context stays clean |
| 🧭 **Execution lifecycle & control** | Every long-running call gets an `execution_id` with an explicit state machine, idle/absolute timeouts, and idempotent cancellation via `cancel_execution` |
| 🕯️ **Optional [Moiras](https://github.com/JoaoPauloNA/moiras) shadow observer** | `ATHENA_MOIRAS_SHADOW=1` enables a coalescing background sampler and exposes its inert advisory through `get_execution`; Athena never reads it to control timeout, cancellation, fallback, lease, or authorization |

## Quick start

```bash
git clone https://github.com/JoaoPauloNA/athena.git
cd athena
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Optional Moiras shadow observation requires the separate compatible package and an explicit server-start opt-in:

```bash
pip install -e '.[moiras]'
ATHENA_MOIRAS_SHADOW=1 python -m athena.mcp_server
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

Athena-MCP exposes 12 tools over stdio. Full parameters in [MCP tools reference](docs/en/mcp-tools.md).

| Tool | Purpose |
|---|---|
| `list_providers` | Installed/available CLIs, models, roles, ratings |
| `list_combos` | Lists configured combos and their failover chains |
| `run_combo` | Runs a prompt through a combo's failover chain (`verify=true` checks each provider's report) |
| `ask_provider` | Sends a task to one provider (`verify=true` runs the report verifier) |
| `deliberate` | Asks several agents in parallel (no verification step) |
| `recommend` | "Who should I call for this task?" — ratings × installed models × complexity |
| `refresh_models` | Re-scans CLI model catalogs |
| `list_usage` | Per-provider call counters and token estimates |
| `list_reliability` | Local claimed-vs-verified ranking per CLI |
| `get_execution` | Looks up a registered execution by `execution_id` or `request_id` |
| `list_executions` | Lists recently registered executions (read-only, sanitized) |
| `cancel_execution` | Requests idempotent cancellation of a running execution |

## How verification works

```
Orchestrator → Executor CLI → 10-topic report
                     │
                     ▼
        Deterministic check (whitelisted test/lint
        commands re-run, cited files checked)
                     │
        conclusive ──┴────── inconclusive
         │                       │
      verdict              Advisory check (cheapest
                            available model, never the
                            executor's own provider)
                                 │
                    TRUE ────────┴──────── FALSE
                     │                       │
                  accepted            sent back to executor (1x)
                                          │
                                FALSE again → ESCALATE to orchestrator
                                (verdict + evidence + history)
```

Both layers only judge what the report itself makes checkable — cited commands and cited files. Neither is a general correctness proof, and `deliberate` does not run verification at all. Details and current roadmap: [docs/en/verification.md](docs/en/verification.md).

## Safety and validation

- `skip_permissions=true` passes each CLI's no-confirmation flag (e.g. `--dangerously-skip-permissions`) — only use it in projects/workspaces you trust.
- The dashboard has **no authentication**; keep ports `7100`/`20129` off networks you don't control.
- SSH executions (`ssh_host`) never confirm that a remote process actually terminated — timeouts and cancellations over SSH stay indeterminate and block automatic fallback until you verify manually.
- Full details: [SECURITY.md](SECURITY.md).
- Local, reproducible validation sequence (lint + full test suite + offline synthetic gate, no real CLI required): [docs/en/local-sharing.md](docs/en/local-sharing.md).

## Limitations

- **Alpha, local-sharing status** — not hardened for public or multi-tenant exposure.
- **Process-group cleanup is covered on POSIX (macOS/Linux)** for descendants that remain in Athena's owned process group. During teardown Athena conservatively looks for currently visible descendants that escaped through `setsid()`/`setpgid()` and refuses tree confirmation when it finds one; failure to observe an escape is not a universal proof that none occurred. On Windows, Athena controls the direct child process only; the wider process tree is classified `NOT_GUARANTEED` on timeout/cancel.
- **The workspace lease is in-process only** — it serializes concurrent attempts inside one Athena process and gives no protection across multiple worker processes or hosts sharing a filesystem.
- **Fallback/retry is conditional, not automatic** — it depends on the service profile's policy for the failure kind (error vs. timeout) and requires the previous attempt's termination to be positively confirmed. The `authenticated_external` and `unknown` profiles never fall back automatically.
- **Model ratings are a local cache, not a live feed** — Athena does not fetch or refresh leaderboard data on its own; refreshing is an external, opt-in step.
- **Moiras is optional and observation-only** — the MCP server enables it only when `ATHENA_MOIRAS_SHADOW` is explicitly truthy. It requires Moiras `0.1.x` with schema `1.0`, keeps advisories in process memory, and cannot change Athena control flow. Athena's current lifecycle source can reach four classes (`REAL_PROGRESS`, `ACTIVITY_WITHOUT_PROGRESS`, `PROBABLE_INACTIVITY`, `INDETERMINATE`); `LEGITIMATE_WAIT` and `EXTERNAL_BLOCK` require explicit signals for which the standard Athena lifecycle currently has no producer.

## Documentation

- 📘 [Architecture](docs/en/architecture.md) · [MCP tools reference](docs/en/mcp-tools.md) · [Verification](docs/en/verification.md) · [Local sharing & validation](docs/en/local-sharing.md)
- 📗 [Arquitetura](docs/pt-BR/arquitetura.md) · [Referência das ferramentas MCP](docs/pt-BR/ferramentas-mcp.md) · [Verificação](docs/pt-BR/verificacao.md) · [Compartilhamento local](docs/pt-BR/compartilhamento-local.md)
- 📙 [架构](docs/zh-CN/architecture.md) · [MCP 工具参考](docs/zh-CN/mcp-tools.md) *(community translation, not kept in sync with the English/Portuguese docs above — it does not define any guarantee)*

## Requirements

- Python ≥ 3.10
- At least one AI CLI installed (Codex, Claude Code, Cursor Agent, Antigravity, OpenCode, Ollama…)
- Node.js (only for the dashboard dev server)

## License

MIT © 2026 João Paulo

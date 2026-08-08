# Architecture

🇧🇷 [PT-BR](../pt-BR/arquitetura.md) · 🇨🇳 [中文](../zh-CN/architecture.md)

## Overview

```
┌─────────────────────────────────────────────────────────┐
│ Orchestrator (any MCP-capable AI chat)                  │
└──────────────┬──────────────────────────┬───────────────┘
               │ stdio (JSON-RPC)         │ HTTP
┌──────────────▼──────────────┐  ┌────────▼───────────────┐
│ athena/mcp_server.py        │  │ athena/dashboard/app.py│
│ 7 MCP tools                 │  │ FastAPI :20129         │
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
│exec  │ │10-topic│ │lie      │ │task→best│ │catalogs +  │
│layer │ │report  │ │detector │ │provider │ │scores      │
└──┬───┘ └────────┘ └────┬────┘ └─────────┘ └────────────┘
   │                     │
┌──▼─────────────────────▼────────────────────────────────┐
│ AI CLIs: codex · claude · agent · agy · openclaude ·    │
│ opencode · ollama (+ auto-discovered & 16 known CLIs)   │
└─────────────────────────────────────────────────────────┘
```

## Modules

| Module | Responsibility |
|---|---|
| `bridge.py` | Subprocess execution, PTY, output cleanup, **enriched PATH** (`which` finds CLIs in `~/.local/bin`, Homebrew, Scoop, npm global etc. even when the host GUI has a minimal PATH) |
| `providers.py` | CLI registry (static catalog of 23 known CLIs + auto-discovery), command building per CLI, `ask_provider`, `ask_provider_verified` |
| `contract.py` | The 10-topic report contract injected into every executor prompt + cheap regex format check |
| `verifier.py` | Lie detector: objective evidence (`git status`, `git diff --stat`, cited-file existence) + cheap model verdict; anti-collusion (verifier ≠ executor provider); fix loop and 2-strike escalation |
| `recommend.py` | Task classification (role × complexity), economy ceiling (simple→light only), provider+model recommendation from installed models |
| `ratings.py` | Model scores per role (0–10), JSON cache `~/.athena/model_ratings.json` with 7-day TTL, refreshed weekly by an automated web-research job |
| `models.py` | Live model catalog per CLI (`--list-models`/`models`), fallback catalog, weight classification (light/medium/heavy) |
| `router.py` + `combos.py` | Failover chains: ordered providers, retries, per-step model/timeout |
| `agents.py` | Named roles (architect, reviewer, counterpoint…) injected into prompts |
| `usage.py` | Local per-provider counters (`~/.athena/usage.json`) |
| `dashboard/app.py` | FastAPI backend + HTMX/Jinja templates + JSON API |

## CLI detection (cross-platform)

1. **Enriched PATH** — process PATH + per-OS known locations:
   - **Windows:** `~/.local/bin`, `%LOCALAPPDATA%\Programs`, npm global, WinGet Links, Scoop shims, Chocolatey, pip `--user` Scripts, cargo, go
   - **macOS:** `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, npm global, cargo, go, bun
   - **Linux:** `~/.local/bin`, `/usr/local/bin`, snap, flatpak exports, npm global, cargo, go, bun
2. **Name heuristic** — whole-word keyword match (with Windows extension stripping: `claude.cmd` → `claude`)
3. **Probe** — `--help`/`--version` output scored for AI keywords (`.cmd`/`.bat` shims run via `cmd /c`)
4. **Static catalog** — 23 known CLIs always listed; uninstalled ones show as *Offline* in the dashboard

## Data files (`~/.athena/`)

| File | Content |
|---|---|
| `models_catalog.json` | Live model lists per CLI (5-day TTL) |
| `model_ratings.json` | Scores per role, refreshed weekly from public leaderboards |
| `combos.json` | Failover chains |
| `usage.json` | Call counters, durations, token estimates |
| `custom_providers.json` | User-defined providers (override auto-discovery) |

All configurable via `ATHENA_DATA_DIR`, `ATHENA_MODELS_FILE`, etc. (see `athena/config.py`).

## Design principles

1. **The orchestrator's context is sacred** — executors return lean 10-topic reports, never code dumps.
2. **Trust, but verify** — reports are checked against objective project evidence, not taken at face value.
3. **Cheap before expensive** — free/local models for verification; heavy models only when complexity justifies them (advisory, never blocking).
4. **Works with what you have** — every routing decision is computed from the CLIs actually installed on the machine.

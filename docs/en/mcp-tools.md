# MCP Tools Reference

🇧🇷 [PT-BR](../pt-BR/ferramentas-mcp.md) · 🇨🇳 [中文](../zh-CN/mcp-tools.md)

The server speaks MCP over stdio (`python -m athena.mcp_server`). All responses are JSON inside a text content block.

## `list_providers`

Lists every registered CLI: availability, resolved binary path, default role, live model catalog, recommended default model, rating scores.

**Input:** `{}`

## `ask_provider`

Sends a task to a specific provider. The prompt is wrapped in the 10-topic report contract.

| Param | Type | Required | Description |
|---|---|---|---|
| `provider` | string | ✅ | `codex`, `agent`, `claude`, `agy`, `openclaude`, `opencode`, `ollama` |
| `prompt` | string | ✅ | The task |
| `model` | string | | Model id (falls back to provider's recommended default) |
| `working_directory` | string | | Project directory for the executor |
| `timeout` | integer | | Seconds (provider default if omitted) |
| `skip_permissions` | boolean | | Pass the CLI's no-confirmation flag |
| `verify` | boolean | | Enable the **lie detector** (see below) |

**With `verify=true`:** after execution, the cheapest available verifier model (free OpenCode models first, never the executor's provider) cross-checks the report against git evidence and cited files. A FALSE report is sent back to the executor once with the reasons; a second FALSE returns `verdict.escalado=true` so the orchestrator can decide (switch CLI, split the task, abort).

**Response extras:** `report_format_ok`, `warnings[]` (including the heavy-model-for-simple-task economy notice), `verdict` (when verified).

## `run_combo`

Runs a prompt through a combo's failover chain.

| Param | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | ✅ | The task |
| `combo_id` | string | | Default: `default` |
| `working_directory` | string | | |
| `timeout` | integer | | Per-step override |

On timeout or error the next chain step is tried, per the combo's failover policy.

## `deliberate`

Consults several agents in parallel and returns all responses.

| Param | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | ✅ | |
| `providers` | string[] | | Default: `["agent", "agy", "claude"]` |

## `recommend`

"Who should I call?" Combines the weekly-refreshed ratings table with what is actually installed.

| Param | Type | Required | Description |
|---|---|---|---|
| `task` | string | ✅ | Natural-language task description |
| `task_type` | string | | Force a role: `frontend`, `backend`, `raciocinio`, `rapidez` |
| `top_n` | integer | | Number of recommendations (default 3) |
| `only_installed` | boolean | | Only suggest installed models (default true) |

**Response:** detected role(s), estimated complexity (`simple|medium|complex`), ranked recommendations (`provider` + `model_id` ready for `ask_provider`), economy note listing heavy models excluded, and a ready-to-use `dica` (tip string).

## `refresh_models`

Re-scans CLI model catalogs (`--list-models`, `opencode models`, …) and rewrites the cache.

| Param | Type | Description |
|---|---|---|
| `force` | boolean | Ignore TTL (default true) |

## `list_usage`

Per-provider counters: calls, total duration, estimated tokens, last used.

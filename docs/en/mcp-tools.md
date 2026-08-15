# MCP Tools Reference

🇧🇷 [PT-BR](../pt-BR/ferramentas-mcp.md) · 🇨🇳 [中文](../zh-CN/mcp-tools.md) *(community translation, not synced — informational only)*

The server speaks MCP over stdio (`python -m athena.mcp_server`). All responses are JSON inside a text content block. There are **12 tools**.

## Long-running tools and execution tracking

`run_combo` and `ask_provider` are the two long-running tools. For each call the server auto-generates an `execution_id` (or accepts one you pass in `arguments.execution_id`, as a non-empty string) and streams lifecycle updates into an in-memory registry as the call progresses. That `execution_id` is echoed at the top level of the response and can be used with `get_execution`, `list_executions` and `cancel_execution`. The registry is bounded (256 executions / 64 attempts each by default) and sanitized — no prompts, reports or credentials are stored, only identity/timing/state metadata (see [Architecture — Execution lifecycle](architecture.md#execution-lifecycle)).

Numeric timeout parameters (`timeout`, `overall_timeout`, `verification_timeout`, `idle_timeout`) must be real numbers greater than 0 (booleans are rejected); `idle_timeout` must not exceed the effective absolute timeout in force for the call.

## `list_providers`

Lists every registered CLI: availability, resolved binary path, default role, live model catalog, recommended default model, rating scores.

**Input:** `{}`

## `list_combos`

Lists configured combos with their failover chains.

**Input:** `{}`

## `run_combo`

Runs a prompt through a combo's conditional failover chain.

| Param | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | ✅ | The task |
| `combo_id` | string | | Default: `default` |
| `working_directory` | string | | Required if the resolved `service_profile` requires a workspace |
| `timeout` | integer | | Per-step override (seconds) |
| `verification_timeout` | number | | Ceiling for the verification phase, if `verify=true` |
| `overall_timeout` | number | | Deadline for the whole combo run across all steps; when exhausted before a safe next stage, raises `ComboDeadlineExceeded` |
| `verify` | boolean | | Checks each provider's report (see below); a report marked FALSE counts as a failure and triggers failover to the next provider |
| `task_type` | string | | `frontend`, `backend`, `raciocinio`, `rapidez` — explicit role (auto-detected from the prompt if omitted) |
| `service_profile` | string | | One of the profile ids in [Architecture — Modules](architecture.md#modules) (`text_generation`, `code_agent`, `build_test`, `research`, `local_model`, `verification`, `workspace_mutation`, `authenticated_external`, `unknown`) |
| `idle_timeout` | number | | Seconds without observable output before the step is treated as stalled |
| `execution_id` | string | | Reuse a specific execution id instead of the auto-generated one |

On timeout or error the next chain step is tried, but only when the combo's failover policy and the resolved service profile both allow it for that failure kind, **and** the previous attempt's termination is positively confirmed — otherwise the call raises `FallbackBlocked` instead of silently continuing (see [Architecture — Fallback / retry safety](architecture.md#fallback--retry-safety)). With `verify=true`, an indeterminate verdict is accepted with a warning (not treated as failure); a verification phase that cannot confirm its own termination also blocks the run rather than guessing.

## `ask_provider`

Sends a task to a specific provider. The prompt is wrapped in the 10-topic report contract.

| Param | Type | Required | Description |
|---|---|---|---|
| `provider` | string | ✅ | `codex`, `agent`, `claude`, `agy`, `openclaude`, `opencode`, `ollama` |
| `prompt` | string | ✅ | The task |
| `model` | string | | Model id (falls back to provider's recommended default) |
| `working_directory` | string | | Required if the resolved `service_profile` requires a workspace |
| `timeout` | integer | | Seconds (provider/profile default if omitted) |
| `skip_permissions` | boolean | | Passes the CLI's no-confirmation flag — see [Safety and validation](../../README.md#safety-and-validation) |
| `verify` | boolean | | Runs the report verifier (see below) |
| `task_type` | string | | `frontend`, `backend`, `raciocinio`, `rapidez` — explicit role (auto-detected if omitted) |
| `service_profile` | string | | Same profile ids as `run_combo` |
| `idle_timeout` | number | | Seconds without observable output before the call is treated as stalled |
| `execution_id` | string | | Reuse a specific execution id instead of the auto-generated one |

**With `verify=true`:** after execution, the report is checked first by the deterministic layer (re-runs whitelisted test/lint commands the report claims passed, checks cited files) and, only if that is inconclusive, by an advisory cheap model (free tier first, never the executor's own provider). A FALSE verdict sends the report back to the executor once with the reasons; if the retry is also marked FALSE, the response carries `verdict.escalado=true` so the orchestrator can decide (switch CLI, split the task, abort). If the service profile doesn't allow an automatic corrective retry (e.g. `authenticated_external`), the first FALSE already escalates. Neither verification layer proves general correctness — see [Verification](verification.md).

**Response extras:** `report_format_ok`, `warnings[]` (including the heavy-model-for-simple-task economy notice), `verdict` (when verified), `execution` (lifecycle metadata for the attempt).

## `deliberate`

Consults several agents in parallel and returns all responses. Does **not** run any verification step.

| Param | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | ✅ | |
| `providers` | string[] | | Default: `["agent", "agy", "claude"]` |

## `recommend`

"Who should I call?" Combines the local ratings cache with what is actually installed.

| Param | Type | Required | Description |
|---|---|---|---|
| `task` | string | ✅ | Natural-language task description |
| `task_type` | string | | Force a role: `frontend`, `backend`, `raciocinio`, `rapidez` |
| `top_n` | integer | | Number of recommendations (default 3) |
| `only_installed` | boolean | | Only suggest installed models (default true) |

**Response:** detected role(s), estimated complexity (`simple|medium|complex`), ranked recommendations (`provider` + `model_id` ready for `ask_provider`), economy note listing heavy models excluded, and a ready-to-use `dica` (tip string).

## `refresh_models`

Re-scans CLI model catalogs (`--list-models`, `opencode models`, …) and rewrites the local cache.

| Param | Type | Description |
|---|---|---|
| `force` | boolean | Ignore TTL (default true) |

## `list_usage`

Per-provider counters: calls, total duration, estimated tokens, last used.

**Input:** `{}`

## `list_reliability`

Local claimed-vs-verified ranking per CLI, from verdicts persisted (redacted) to `~/.athena/verdicts.json`: how often each CLI declared "done" and it held up, false-report rate and escalation count.

| Param | Type | Description |
|---|---|---|
| `limit` | integer | Recent episodes to include in `ultimos_episodios` (default 20) |

## `get_execution`

Looks up a registered execution by `execution_id` or `request_id` (the original JSON-RPC id of the `tools/call` that started it).

| Param | Type | Description |
|---|---|---|
| `execution_id` | string | |
| `request_id` | string \| number | |

**Response:** `{"execution": null}` if not found, otherwise the sanitized execution record (state, attempts, timestamps — no prompts/reports).

## `list_executions`

Lists recently registered executions, read-only and sanitized, most recently updated first.

| Param | Type | Description |
|---|---|---|
| `limit` | integer | Clamped to `1..100` (default 20) |

## `cancel_execution`

Requests idempotent cancellation of an execution by `execution_id` or `request_id`. Calling it again on an already-finalized or already-cancelling execution is a safe no-op.

| Param | Type | Description |
|---|---|---|
| `execution_id` | string | |
| `request_id` | string \| number | |
| `reason` | string | Normalized to one of a small safe-reason set; anything else is recorded as `user_requested` |

**Response:** `{"found": bool, "requested": bool, "execution_id": ...}` — `requested=false` with `found=true` means the execution was already finalized or a cancellation was already in flight.

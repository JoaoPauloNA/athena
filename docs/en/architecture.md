# Architecture

🇧🇷 [PT-BR](../pt-BR/arquitetura.md) · 🇨🇳 [中文](../zh-CN/architecture.md) *(community translation, not synced — informational only)*

## Overview

```
┌─────────────────────────────────────────────────────────┐
│ Orchestrator (any MCP-capable AI chat)                  │
└──────────────┬──────────────────────────┬───────────────┘
               │ stdio (JSON-RPC)         │ HTTP
┌──────────────▼──────────────┐  ┌────────▼───────────────┐
│ athena/mcp_server.py        │  │ athena/dashboard/app.py│
│ 12 MCP tools                │  │ FastAPI :20129         │
└──────────────┬──────────────┘  └────────┬───────────────┘
               │                          │
┌──────────────▼──────────────────────────▼───────────────┐
│ athena/providers.py                                     │
│ ask_provider · ask_provider_verified · combos           │
└──┬─────────┬──────────┬──────────┬─────────┬────────────┘
   │         │          │          │         │
┌──▼───┐ ┌───▼────┐ ┌───▼─────┐ ┌──▼──────┐ ┌▼───────────┐
│bridge│ │contract│ │verifier │ │recommend│ │models/     │
│.py   │ │.py     │ │+dverify │ │.py      │ │ratings.py  │
│exec  │ │10-topic│ │.py      │ │task→best│ │catalogs +  │
│layer │ │report  │ │report   │ │provider │ │scores      │
│      │ │        │ │check    │ │         │ │            │
└──┬───┘ └────────┘ └────┬────┘ └─────────┘ └────────────┘
   │                     │
┌──▼─────────────────────▼────────────────────────────────┐
│ AI CLIs: codex · claude · agent · agy · openclaude ·    │
│ opencode · ollama (+ auto-discovered & 15 other known    │
│ CLIs), local or over SSH                                 │
└─────────────────────────────────────────────────────────┘
```

Underneath `bridge.py`, three modules govern *how safely* a subprocess run can be retried or reported on: `execution.py` (per-attempt lifecycle state machine), `execution_registry.py` (server-side registry backing the `*_execution` MCP tools) and `workspace_lease.py` (in-process serialization against a working directory). `service_profiles.py` decides the timeout/fallback policy for a call before it starts, and `ssh.py` builds the command for remote execution.

## Modules

| Module | Responsibility |
|---|---|
| `bridge.py` | Subprocess/PTY execution, output cleanup, **enriched PATH** (`which` finds CLIs in `~/.local/bin`, Homebrew, Scoop, npm global etc. even when the host GUI has a minimal PATH). Enforces the absolute + idle deadlines and drives `execution.py` transitions for every attempt. |
| `execution.py` | Execution contract: `ExecutionState` state machine (`QUEUED → STARTING → RUNNING → ... → COMPLETED/FAILED/CANCELLED/TIMED_OUT/TERMINATION_UNCONFIRMED`), `ExecutionRecord` (identity, timing, termination-confirmation flags, sanitized `to_dict()`), `DeadlineBudget` for multi-stage timeouts, and `ExecutionControl` for thread-safe cancellation. |
| `execution_registry.py` | Bounded (default 256 records / 64 attempts each), sanitized, in-memory registry that `mcp_server.py` updates as long-running tools (`run_combo`, `ask_provider`) progress. Backs `get_execution`, `list_executions`, `cancel_execution`. Redacts identifiers, categorizes free-text reasons into a fixed code list, and evicts oldest *finalized* records first — never an active one. |
| `workspace_lease.py` | In-process lease keyed by canonical (realpath-resolved) working directory: `canonical workspace → owning execution_id → active attempt_id`. Serializes `router.run_combo()` calls and fallback/retry attempts that share a working directory. **In-process only** — `threading.Lock` + a plain dict give no protection across separate OS processes, workers or hosts sharing the same filesystem. |
| `moiras_adapter.py` | Optional, disabled-by-default shadow observer. When the MCP server starts with `ATHENA_MOIRAS_SHADOW=1`, its callback-safe `submit()` path coalesces allowlisted updates for a bounded background sampler; `get_execution` can then expose the current attempt's inert advisory. Athena never consumes it for control flow. |
| `service_profiles.py` | Named policies (`text_generation`, `code_agent`, `build_test`, `research`, `local_model`, `verification`, `workspace_mutation`, `authenticated_external`, `unknown`) that set the max absolute timeout, whether a working directory is required, and whether fallback is allowed on error/timeout. `authenticated_external` and `unknown` never allow automatic fallback. |
| `ssh.py` | Builds the `ssh` argv for `ask_provider(..., ssh_host=...)`: validates the host string, shell-quotes the remote command as a single token to prevent injection via prompt content, and never accepts or stores a password/credential. |
| `providers.py` | CLI registry (static catalog of 22 known CLIs + auto-discovery), command building per CLI, `ask_provider`, `ask_provider_verified`. Acquires/releases the workspace lease around each attempt. |
| `contract.py` | The 10-topic report contract injected into every executor prompt + cheap regex format check |
| `verifier.py` | Advisory verifier: cheap-model triage over objective evidence (`git status`, `git diff --stat`, cited-file existence); anti-collusion (verifier ≠ executor provider); fix loop and 2-strike escalation. Delegates to `dverify.py` first and only falls through to the model-based check when nothing is conclusive. |
| `dverify.py` | Deterministic verifier: no model. Re-runs a whitelist of test/lint commands the report claims passed (token-split via `shlex`, never a shell, per-command timeout, max 3 commands) and compares real exit codes; checks that files claimed as created/edited actually exist. Skips re-running a command if the report already admits it failed nearby. |
| `reliability.py` | Persists each verification episode, redacted, to `~/.athena/verdicts.json` (last 500); aggregates a local claimed-vs-verified rate per CLI for `list_reliability` and `recommend`. |
| `recommend.py` | Task classification (role × complexity), economy ceiling (simple→light only), provider+model recommendation from installed models |
| `ratings.py` | Model scores per role (0–10), local JSON cache `~/.athena/model_ratings.json` with a 7-day TTL and a seed fallback. Athena itself never fetches leaderboard data — refreshing the cache from live sources is an external, opt-in job outside this repo. |
| `models.py` | Live model catalog per CLI (`--list-models`/`models`), fallback catalog, weight classification (light/medium/heavy) |
| `router.py` + `combos.py` | Failover chains: ordered providers, retries, per-step model/timeout. Before starting a new attempt, `router._fallback_block_reason` requires the previous attempt's execution metadata to positively confirm termination (direct process, and process-tree when a `pgid` existed) — otherwise it raises `FallbackBlocked` instead of risking two concurrent processes. |
| `agents.py` | Named roles (architect, reviewer, counterpoint…) injected into prompts |
| `usage.py` | Local per-provider counters (`~/.athena/usage.json`) |
| `dashboard/app.py` | FastAPI backend + HTMX/Jinja templates + JSON API (no authentication — local use only, see [SECURITY.md](../../SECURITY.md)) |

## Execution lifecycle

Every provider subprocess attempt is tracked by an `ExecutionRecord` (`athena/execution.py`) moving through an explicit, one-way state machine — terminal states (`COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`, `TERMINATION_UNCONFIRMED`) never transition again. Two independent deadlines apply: an **absolute** ceiling on total run time, and an optional **idle** ceiling that resets on every observed stdout/stderr/PTY chunk — the absolute ceiling always wins when both expire at once.

On timeout or cancellation, `bridge.py` sends `SIGTERM` to the process group, waits a grace window (default 3s), then escalates to `SIGKILL`, and only reports the attempt as `CANCELLED`/`TIMED_OUT` once the process and owned group are positively confirmed empty. If that confirmation cannot be obtained, the state is `TERMINATION_UNCONFIRMED` instead — this is what blocks automatic fallback and workspace-lease release (see below). On POSIX, `start_new_session=True` makes the launched process its own process-group leader. During teardown Athena also takes a best-effort process-table snapshot: if it positively sees a descendant that escaped the owned group through `setsid()`/`setpgid()`, owned-scope confirmation is refused. This is conservative positive detection, not universal containment or proof of absence; a short-lived/reparented escape can evade an observation. Lifecycle metadata now publishes `termination_scope` (`no_process`, `direct_process`, or `owned_process_group`) so the legacy `process_tree_terminated_confirmed` field is not mistaken for proof about all OS descendants. On Windows there is no equivalent group guarantee — see [Platform support](#platform-support).

`mcp_server.py` registers one `execution_id` per `run_combo`/`ask_provider` call (the two long-running tools) and streams attempt updates into `execution_registry.py`, which is what `get_execution`, `list_executions` and `cancel_execution` read from. `cancel_execution` is idempotent and works by `execution_id` or by the original JSON-RPC `request_id`. Raw request IDs remain private lookup keys even when their public representation is hashed.

## Workspace lease

`workspace_lease.py` prevents two attempts against the *same canonical working directory* from running concurrently — whether they come from two different `run_combo()` calls or from a fallback/retry inside one call. A lease is transferred to a new attempt only after the prior attempt's execution metadata confirms it is safely over; otherwise the transfer raises `WorkspaceLeaseError` and the caller stays fail-closed (lease retained, no new attempt started).

This is **in-process only**: the lease lives in one Python process's memory (`threading.Lock` + dict). It does not coordinate across multiple Athena processes, worker pools, or machines sharing the same filesystem — that is out of scope for the current implementation.

## Optional Moiras boundary

`athena/moiras_adapter.py` is the only implemented boundary to the independent [Moiras project](https://github.com/JoaoPauloNA/moiras). The MCP integration is disabled by default. Installing the `moiras` optional extra and setting `ATHENA_MOIRAS_SHADOW` to `1`, `true`, `yes` or `on` before server startup creates one bounded sampler thread (one-second interval; per-attempt stores are capped at 64 entries by default). Lifecycle callbacks only copy an allowlist into a coalescing pending set; they neither import nor call Moiras on the stdout/stderr drain path. Repeated unchanged non-terminal snapshots remain pending so elapsed inactivity can become observable; terminal snapshots are removed after sampling. Shutdown asks the sampler to stop.

Only execution/attempt IDs, lifecycle state, progress/state/history markers, synthetic counters, fixed profile `athena-shadow`, timestamps, artifact revision and explicit wait/block booleans cross the boundary. Provider, prompt, output, command, path, host, user, PID and PGID do not. The runtime contract accepts Moiras `0.1.x` with `SCHEMA_VERSION == "1.0"`, the required callables and the exact six-class Sentinel enum; absence or incompatibility produces a categorical inert advisory without exception text. The optional dependency currently points to the compatible Moiras release tag.

With snapshots produced by Athena's standard `ExecutionRecord`, four classifications are reachable: `REAL_PROGRESS`, `ACTIVITY_WITHOUT_PROGRESS`, `PROBABLE_INACTIVITY` and `INDETERMINATE`. `LEGITIMATE_WAIT` requires `waiting_for_authorization` or `waiting_for_credential`, and `EXTERNAL_BLOCK` requires `external_block`; these remain supported boundary inputs, but the standard Athena lifecycle currently emits none of those three flags. They must not be advertised as observed from normal Athena execution until a producer is implemented.

When enabled, `get_execution` adds `execution.moiras_shadow` for the current attempt: either the latest advisory or `null` while none is ready. Sampling is asynchronous and process-local, so the value can lag the newest lifecycle event and is not persisted. Every advisory fixes `affects_control_flow=false`, `executed=false`, `mode=shadow`; it is not a council/model decision and cannot relax lifecycle, fallback, cancellation, authorization or lease rules.

## Fallback / retry safety

`router.run_combo()` does not fall back to the next provider (or retry) unless both are true: (1) the active `service_profile` allows fallback for that failure kind (`allow_fallback_on_error` / `allow_fallback_on_timeout`), and (2) the previous attempt's execution metadata positively confirms termination. A `client_abandoned` attempt, a remote (SSH) session without confirmed remote termination, or any non-terminal state blocks fallback outright and raises `FallbackBlocked`. The `authenticated_external` and `unknown` service profiles set both fallback flags to `false` unconditionally, so authenticated or unclassified actions are never retried automatically.

## SSH remote execution

`ask_provider(..., ssh_host=...)` runs the CLI on a remote host via `ssh.py`'s command builder (host validation, no shell interpolation of prompt content, key-based auth only — Athena never accepts or stores a password). Because the process runs on another machine, **Athena cannot directly confirm the remote process actually terminated**: `ExecutionRecord.remote_termination_confirmed` stays `False`/unset unless an explicit confirmation path sets it, so a timeout or cancellation over SSH resolves to `TERMINATION_UNCONFIRMED` rather than `CANCELLED`/`TIMED_OUT` — which blocks fallback and workspace-lease release the same way a stuck local process would.

## Platform support

CLI *detection* runs on macOS, Windows and Linux (see below). Process-group lifecycle cleanup is exercised by the current test suite on **POSIX (macOS/Linux) only**. The best-effort POSIX process-table check can positively detect a currently visible descendant that escaped the owned group and then fail closed, but cannot prove that no escape ever occurred. `harness/p0_gate.py` records this bounded scope explicitly: `posix` is `LOCAL_CONTROLLED_ONLY`, while `windows` is `NOT_GUARANTEED` (Athena controls the direct child there but does not claim confirmed cleanup of a wider tree).

## CLI detection (cross-platform)

1. **Enriched PATH** — process PATH + per-OS known locations:
   - **Windows:** `~/.local/bin`, `%LOCALAPPDATA%\Programs`, npm global, WinGet Links, Scoop shims, Chocolatey, pip `--user` Scripts, cargo, go
   - **macOS:** `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, npm global, cargo, go, bun
   - **Linux:** `~/.local/bin`, `/usr/local/bin`, snap, flatpak exports, npm global, cargo, go, bun
2. **Name heuristic** — whole-word keyword match (with Windows extension stripping: `claude.cmd` → `claude`)
3. **Probe** — `--help`/`--version` output scored for AI keywords (`.cmd`/`.bat` shims run via `cmd /c`)
4. **Static catalog** — 22 known CLIs always listed; uninstalled ones show as *Offline* in the dashboard

## Data files (`~/.athena/`)

| File | Content |
|---|---|
| `models_catalog.json` | Live model lists per CLI (5-day TTL) |
| `model_ratings.json` | Scores per role, local cache with a 7-day TTL and a seed fallback (see [Ratings](#modules)) |
| `combos.json` | Failover chains |
| `usage.json` | Call counters, durations, token estimates |
| `custom_providers.json` | User-defined providers (override auto-discovery) |
| `verdicts.json` | Last 500 verification episodes, redacted (no prompts/full reports), feeding `list_reliability` |

All configurable via `ATHENA_DATA_DIR`, `ATHENA_MODELS_FILE`, etc. (see `athena/config.py`).

## Design principles

1. **The orchestrator's context is sacred** — executors return lean 10-topic reports, never code dumps.
2. **Trust, but verify what's checkable** — reports are checked against objective project evidence for the specific claims that admit a check; this is not a general correctness proof.
3. **Cheap before expensive** — free/local models for verification; heavy models only when complexity justifies them (advisory, never blocking).
4. **Fail closed on lifecycle uncertainty** — fallback, lease transfer/release and verification all refuse to proceed when a prior attempt's termination isn't positively confirmed.
5. **Works with what you have** — every routing decision is computed from the CLIs actually installed on the machine.

# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

> Everything below has been implemented and exercised against the local
> offline gate (`harness/p0_gate.py`) — scoped lint + the P0 synthetic test
> matrix run with `ATHENA_SKIP_AUTODISCOVERY=1` against an isolated
> `ATHENA_DATA_DIR`. The full suite is a separate local-sharing gate. It has
> **not** been published as a release; current test counts are recorded in
> the gate's JSON output under `harness/results/`, not hardcoded here since
> they change as the suite grows.

### Added
- **Execution contract & lifecycle state machine** (`athena/execution.py`): explicit `ExecutionState` machine (`QUEUED → STARTING → RUNNING → ... → COMPLETED/FAILED/CANCELLED/TIMED_OUT/TERMINATION_UNCONFIRMED`, terminal states never transition again), dual absolute/idle deadlines via `DeadlineBudget`, termination-confirmation tracking for both the direct process and its process tree, thread-safe cancellation (`ExecutionControl`), and a sanitized `to_dict()` serialization (no prompts/reports/credentials).
- **Execution registry + MCP execution tools**: a bounded (256 executions / 64 attempts each, default), sanitized, in-memory registry (`athena/execution_registry.py`) backing three new MCP tools — `get_execution`, `list_executions`, `cancel_execution` (idempotent, by `execution_id` or `request_id`) — plus automatic `execution_id` registration and lifecycle streaming for `run_combo`/`ask_provider` in `mcp_server.py`. The MCP server now exposes 12 tools.
- **In-process workspace lease** (`athena/workspace_lease.py`): serializes concurrent attempts against the same canonical (realpath-resolved) working directory within one Athena process — across separate `run_combo()` calls and across fallback/retry attempts inside one call. Explicitly does **not** protect across multiple OS processes, worker pools, or hosts sharing the same filesystem.
- **Service profiles** (`athena/service_profiles.py`): named timeout/fallback policies (`text_generation`, `code_agent`, `build_test`, `research`, `local_model`, `verification`, `workspace_mutation`, `authenticated_external`, `unknown`) resolved per call, each with its own max absolute timeout, workspace requirement, and fallback-on-error/fallback-on-timeout flags. Exposed via the new `service_profile`/`idle_timeout` parameters on `run_combo`/`ask_provider`. `authenticated_external` and `unknown` never allow automatic fallback.
- **SSH remote execution builder** (`athena/ssh.py`): validates the destination host string, shell-quotes the remote command as a single token to prevent injection via prompt content, key-based auth only (never accepts/stores a password). Remote runs never positively confirm process termination — timeouts/cancellations over SSH resolve to `TERMINATION_UNCONFIRMED`, which blocks automatic fallback and workspace-lease release by design.
- **Router fallback safety**: `router.run_combo()` now requires positively confirmed termination of the previous attempt (direct process, and process tree when a `pgid` existed) before starting a new attempt or falling back to the next provider, raising `FallbackBlocked` or `ComboDeadlineExceeded` instead of risking two concurrent processes against the same workspace.
- **Offline synthetic gate** (`harness/p0_gate.py`): runs scoped `ruff check` plus the P0 lifecycle/router/lease/MCP/SSH/profile/privacy matrix against an isolated, temporary `ATHENA_DATA_DIR` with `ATHENA_SKIP_AUTODISCOVERY=1`, and writes a PASS/FAIL JSON report per stage — including a platform support classification (`posix: LOCAL_CONTROLLED_ONLY`, `windows: NOT_GUARANTEED`) and, where applicable, collected test counts — to `harness/results/`. The local-sharing checklist runs the full suite separately.

### Changed
- `athena/bridge.py`: subprocess/PTY execution now drives `execution.py` state transitions end-to-end, enforces the idle deadline alongside the absolute one, and only reports a terminal state after process(-tree) cleanup is confirmed or explicitly marked unconfirmed.
- `athena/providers.py`, `athena/router.py`: acquire/transfer/release the workspace lease around each attempt; `ask_provider_verified`'s corrective fix-loop now runs through the same lease/lifecycle machinery as a normal attempt instead of bypassing it.
- `athena/verifier.py`, `athena/dverify.py`: verification phases now carry their own `execution` lifecycle metadata (deadline, cancellation, termination confirmation) instead of running outside the execution contract; a verification phase that can't confirm its own termination reports `TERMINATION_UNCONFIRMED` rather than guessing a verdict.
- `athena/reliability.py`: verdict records now categorize free-text termination/cancellation reasons into a fixed, redacted code list before persisting to `~/.athena/verdicts.json`.

### Fixed
- **Verifier false positive on repo-root-relative paths**: agents often report created files relative to the git repo root, not the working directory; `find_missing_created_files` now resolves claimed paths against both before flagging them missing. Found during the first real orchestration episode (a Cursor/Sonnet report was wrongly marked FALSE twice).
- **`test_combo` timeout cap** (`router.py`) and **stdout/stderr `$PWD` sync** (`bridge.py`): `test_combo` now caps its per-provider probe timeout consistently, and subprocess `cwd` changes are mirrored into the child's `PWD` env var on POSIX — some CLIs (confirmed: opencode) trust `$PWD` over the actual working directory argument and would otherwise write to the wrong project.

## [0.1.0] - 2026-08-07

### Added
- Cross-platform AI CLI scanner (macOS/Windows/Linux) with enriched PATH (`~/.local/bin`, Homebrew, Scoop, npm global, WinGet, Chocolatey, cargo, go, flatpak, snap)
- Static catalog of known AI CLIs (uninstalled ones shown as Offline) + heuristic auto-discovery
- MCP server with 7 tools: `list_providers`, `ask_provider`, `run_combo`, `deliberate`, `recommend`, `refresh_models`, `list_usage`
- Report verifier (`ask_provider` with `verify=true`): cheap-model verification of 10-topic reports against git evidence; fix loop; 2-strike escalation to the orchestrator
- `recommend` tool: ratings × installed models × task complexity, with economy routing (heavy models excluded for simple tasks)
- Model ratings table (0–10 per role: frontend/backend/reasoning/speed), cached in `~/.athena/model_ratings.json` with a local TTL
- Web dashboard (FastAPI + HTMX) with providers, models, combos, usage and Best-per-Role table
- Failover combos with retries and per-step model/timeout
- Trilingual documentation: English, Português (BR), 中文
- Test suite and CI (ruff + pytest on Ubuntu/macOS/Windows × Python 3.10–3.12)

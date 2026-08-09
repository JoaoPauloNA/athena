# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Deterministic verifier** (`athena/dverify.py`): model-free verification layer that re-runs the exact test/lint commands a report claims passed (whitelist only, no shell, per-command timeout) and compares real exit codes, plus checks that files claimed as created actually exist. Conclusive results short-circuit the advisory (model-based) verifier; `ATHENA_VERIFY_MODE=auto|deterministic|advisory` controls the pipeline. 11 new tests (45 total).
- **Verdict persistence + reliability ranking** (`athena/reliability.py`): every verification episode is recorded redacted (no prompts/reports) in `~/.athena/verdicts.json` (500-record retention). New MCP tool `list_reliability` and a read-only dashboard card show per-CLI claimed-vs-verified rates, false-report rates and escalations. 8 new tests (53 total).
- **Trust loop closed**: `run_combo` accepts `verify=true` — each provider's report is verified and a FALSE report counts as failure, triggering failover to the next provider (the combo no longer accepts a lying "done"). `recommend` now applies local reliability history (30% weight) on top of public ratings and warns when a provider has ≥50% verified-false reports. 7 new tests (60 total).
- **Mini-harness claimed-vs-verified** (`harness/`): repeatable episode runner — clean workspace per task, agent executes, harness runs the oracle for real regardless of claims, episodes recorded to `harness/results/` and the reliability store. Task suite v1: 6 tasks in 3 levels (easy correctness, medium bug-fix/edge-cases, hard multi-file regression + honesty trap). First real smoke runs already caught one CLI lying (free model claimed "pytest passed" with exit 2) and one honestly reporting an impossible task. 5 new tests (65 total).

### Fixed
- **Verifier false positive on repo-root-relative paths**: agents often report created files relative to the git repo root, not the working directory; `find_missing_created_files` now resolves claimed paths against both before flagging them missing. Found during the first real orchestration episode (a Cursor/Sonnet report was wrongly marked FALSE twice). 2 regression tests (67 total).

## [0.1.0] - 2026-08-07

### Added
- Cross-platform AI CLI scanner (macOS/Windows/Linux) with enriched PATH (`~/.local/bin`, Homebrew, Scoop, npm global, WinGet, Chocolatey, cargo, go, flatpak, snap)
- Static catalog of 23 known AI CLIs (uninstalled ones shown as Offline) + heuristic auto-discovery
- MCP server with 7 tools: `list_providers`, `ask_provider`, `run_combo`, `deliberate`, `recommend`, `refresh_models`, `list_usage`
- **Lie detector** (`ask_provider` with `verify=true`): cheap-model verification of 10-topic reports against git evidence; fix loop; 2-strike escalation to the orchestrator
- `recommend` tool: ratings × installed models × task complexity, with economy routing (heavy models excluded for simple tasks)
- Model ratings table (0–10 per role: frontend/backend/reasoning/speed), cached in `~/.athena/model_ratings.json` and refreshed weekly from public leaderboards
- Web dashboard (FastAPI + HTMX) with providers, models, combos, usage and Best-per-Role table
- Failover combos with retries and per-step model/timeout
- Trilingual documentation: English, Português (BR), 中文
- Test suite (34 tests) and CI (ruff + pytest on Ubuntu/macOS/Windows × Python 3.10–3.12)

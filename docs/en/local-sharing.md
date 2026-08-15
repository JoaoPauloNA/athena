# Local sharing & validation

🇧🇷 [PT-BR](../pt-BR/compartilhamento-local.md)

> How to validate an Athena-MCP checkout before sharing it locally — with people or machines you already trust — without needing any AI CLI installed.

## Why this exists

Athena-MCP is alpha software built for controlled local sharing, not public or untrusted exposure (see the root [README](../../README.md#limitations)). Before handing a checkout to someone else, or trusting a change yourself, run the reproducible sequence below. Once the development dependencies are available, the validation commands need no network access and no installed AI CLI — the logic under test is exercised through the test suite, not real provider subprocesses.

## Sequence

1. Set up a virtualenv and install dev dependencies (see [CONTRIBUTING.md](../../CONTRIBUTING.md)).
2. Lint: `ruff check athena tests` — must exit 0.
3. Full test suite, isolated data dir, auto-discovery off:
   ```bash
   export ATHENA_DATA_DIR="$(mktemp -d)"
   export ATHENA_SKIP_AUTODISCOVERY=1
   pytest -q
   ```
   Must exit 0. `ATHENA_DATA_DIR` must point at an existing, writable, empty-or-scratch directory — the suite writes cache/registry files there instead of your real `~/.athena/`.
4. Offline synthetic gate:
   ```bash
   python harness/p0_gate.py
   ```
   This step manages its own isolated `ATHENA_DATA_DIR` (a temporary directory) and sets `ATHENA_SKIP_AUTODISCOVERY=1` internally — no manual env setup needed here. It re-runs a scoped `ruff check` plus the execution-lifecycle, router, workspace-lease, MCP-registry/cancel/EOF, SSH-builder, service-profile, privacy/reliability and optional-Moiras-adapter unit test files as separate stages, and writes a JSON report to `harness/results/p0-gate-<timestamp>.json` (or the path given via `--output`).
5. When reviewing the optional real Moiras contract, install the optional extra and run the cross-repository tests separately:
   ```bash
   pip install -e '.[moiras]'
   pytest -q tests/test_moiras_adapter.py tests/test_moiras_adapter_integration.py
   ```
   This additional check uses the Moiras dependency and is therefore not part of the Moiras-independent offline gate. CI performs an equivalent contract job from separate Athena and Moiras checkouts.

## PASS / FAIL criteria

- **PASS**: step 2 exits 0, step 3 exits 0, and step 4's report has `overall_status: "passed"` — every entry in its `stages` array has `status: "passed"`.
- **FAIL**: any of the above fails. Before sharing a checkout, read the failing stage's `id` and `exit_code` in the JSON report (or the raw `pytest`/`ruff` output) rather than assuming it's unrelated to your change.
- The gate's JSON report is the source of truth for **how many tests ran** at any given time — that number changes as the suite grows, so it is intentionally not hardcoded in this document or in `CHANGELOG.md`.
- A PASS means the code and its own test suite are internally consistent on the machine that ran it. It does not exercise a real AI CLI end-to-end and needs no network access.

## Limitations and scope

- This is a **local, single-machine** validation sequence. It says nothing about behavior on a different OS, Python version, or with real CLIs installed, beyond what the `support_classification` block in the JSON report captures for the machine that ran it.
- `support_classification` (`posix: LOCAL_CONTROLLED_ONLY`, `windows: NOT_GUARANTEED`, plus which one is `effective` on the host that ran the gate) reflects **test-suite coverage**, not a live capability probe: POSIX is `LOCAL_CONTROLLED_ONLY` because process-tree lifecycle tests run and are asserted only there; Windows is `NOT_GUARANTEED` for the same reason — the classification is about what is tested, not a claim that Windows is known to fail.
- Passing the gate is a precondition for sharing locally, not a substitute for the warnings in [Safety and validation](../../README.md#safety-and-validation) and [SECURITY.md](../../SECURITY.md). `skip_permissions`, the unauthenticated dashboard, SSH's lack of remote-termination confirmation, and the in-process-only workspace lease all still apply regardless of gate status.
- The gate is synthetic and offline by construction — no real CLI subprocess is exercised. It validates the execution-lifecycle, router, lease, registry, SSH-builder and verifier *logic*, not a live end-to-end run against Codex/Claude Code/Cursor/etc.
- The gate does not set `ATHENA_MOIRAS_SHADOW`; the MCP server activates the sampler only through explicit opt-in at startup. Adapter unit tests use an injected compatible contract, while the separate integration test requires Moiras `0.1.x` / schema `1.0`. Whether enabled or tested, its advisory remains observation-only and never participates in the PASS/FAIL control path above.
- On POSIX, `setsid()`/`setpgid()` escape handling is positive and conservative: seeing an escaped descendant blocks tree confirmation, but not seeing one is not proof of universal containment. The `LOCAL_CONTROLLED_ONLY` classification remains intentional.

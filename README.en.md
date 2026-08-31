# Athena-MCP

[Português](README.md) · [English](README.en.md)

MCP server (stdio, JSON-RPC) for **governed execution of AI agent CLIs** on
your machine: ordered attempts with controlled fallback, workspace leases,
deadlines with reliable cancellation, and deterministic plus advisory result
verification.

> **Status:** Athena v1 is technically closed and verified for local,
> single-user use. POSIX (macOS/Linux). Windows is not supported in this
> version.

## V1 distribution status

- The public runtime exposes seven MCP tools while preserving the separate
  authority boundaries of Zeus, Nike, Aegis, Chronos, and Evidence Gate.
- The `Athena-beta` checkout already contains the functional v1 runtime. On
  2026-08-31, its remaining differences from the main checkout were limited to
  documentation and additional installation/CAS tests, with no runtime-code
  change.
- The current protected-aware validation reports `716 passed, 3 deselected`;
  the historical runtime evidence baseline remains `5319763`, with
  `715 passed, 3 deselected`.

## What it does

1. **Governed routing and failover** — `run_combo` executes ordered attempts;
   fallback occurs only after the previous attempt has terminated and the risk
   policy approves the transition.
2. **Claim verification** — optional `verification` reruns allowed commands
   and checks referenced files deterministically, followed by a non-blocking
   advisory layer.
3. **Observable lifecycle** — every execution has an `execution_id`, explicit
   states (`completed`, `failed`, `cancelled`, `timed_out`, and others), query
   operations, and idempotent cancellation.
4. **Sanitized failures** — timeouts and failures return tool results with
   `isError: true` and a sanitized payload rather than an opaque protocol error.

## MCP tools (7)

| Tool | Description |
|---|---|
| `run_combo` | Runs an ordered sequence of attempts with governed fallback |
| `ask_provider` | Runs one provider under Aegis governance |
| `get_execution` | Reads the sanitized state of an execution |
| `list_executions` | Lists recent executions |
| `cancel_execution` | Requests cooperative cancellation |
| `submit_task` | Submits a durable task (TASK-0/FLOW-1) |
| `get_task` | Reads durable task state |

## Installation

Requirements: Python 3.11 or newer, pip, and Git.

```bash
# 1. Sibling checkouts
git clone git@github.com:JoaoPauloNA/aegis.git ../aegis
git clone git@github.com:JoaoPauloNA/athena.git
cd athena

# 2. One virtual environment for Athena and Aegis
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ../aegis
python -m pip install -e ".[dev]"

# 3. Protected-aware verification
python harness/p0_gate.py
python -m pytest tests -m "not regression" -q --ignore=tests/test_api_mode.py
```

Example MCP client registration:

```json
{
  "mcpServers": {
    "athena": { "command": "/path/to/athena/.venv/bin/athena-mcp" }
  }
}
```

## Known limitations

- **POSIX only:** the bridge relies on POSIX process groups and PTYs; Windows
  termination is not guaranteed (`TERMINATION_UNCONFIRMED`).
- **Single user:** local stdio process, without network exposure,
  authentication, or multi-user support.
- **No production dashboard:** MCP tools are the operational interface.
  OLIMPO-0 is an opt-in loopback HTTP library for observation and validated
  configuration publication through preview plus CAS. It does not execute,
  cancel, or authorize tasks.
- **No strong sandbox:** CLIs run with the current user's permissions inside
  the configured workspace. Use dedicated working directories.
- **Automatic fallback:** `authenticated_external` and `unknown` profiles
  never fall back automatically because Aegis fails closed.
- Inline human approval (`REQUIRES_HUMAN_APPROVAL`) is reserved by the Aegis
  contract and is not currently emitted.

## Curated publication and security

Athena uses a **modular public monorepo**. Zeus, Nike, Chronos, Evidence Gate,
Clio, Harmonia, Capsule, and Iris have explicit boundaries but remain in the
core until an independent consumer or release cycle justifies extraction.

Public material may contain code, contracts, schemas, tests, documentation,
and sanitized templates. Complete proprietary prompts belong in a separate
private repository. Prompts filled with real data, conversations, private
responses, personal paths, tokens, keys, cookies, and OAuth sessions never
belong in Git, including private repositories.

Portuguese is canonical and English is the first maintained translation.
Additional languages require demonstrated demand and editorial review that
prevents versions from drifting apart.

## Architecture summary

```text
MCP client -> mcp_stdio (JSON-RPC) -> mcp_server (thin tool layer)
    -> router (combos/fallback through aegis.decision.evaluate)
        -> bridge (POSIX subprocess/PTY under workspace lease)
        -> verifier (deterministic -> advisory)
    -> registry (sanitized execution get/list/cancel)
```

Module import boundaries are machine-checked with `import-linter`; the core
does not import code from `legado/`.

## Documentation

- `CHANGELOG.md` — change history.
- `docs/backlog.md` — recorded backlog.
- `contexto/gerencia_athena-mcp.md` — current technical handoff.

## Independent ecosystem repositories

| Repository | Role |
|---|---|
| **Athena-MCP** (this repository) | Orchestrator and aggregator using versioned public contracts |
| Aegis (private) | Independent risk and permission gate |
| Aletheia | Claimed-versus-verified episodes that feed Themis |
| Moiras | Shadow observation |
| Themis (private) | Reputation and scoring |
| Argos (private) | Observational browser QA |
| athena.dev (private) | Product and website |

## Internal modules

Zeus (eligibility), Nike (runtime/provider resolution), Chronos (governed
lifecycle), Evidence Gate, Clio (four logging levels), Harmonia (parallelism
and write sets), Capsule (minimal sealed environment), Iris (preflight),
Olimpo (opt-in loopback observation and CAS configuration), Flow/Tasks,
Lease, configuration loader, bridge, and dormant SSH transport.

## Tests

```bash
.venv/bin/python harness/p0_gate.py
.venv/bin/python -m pytest tests -m "not regression" -q --ignore=tests/test_api_mode.py
# 716 passed, 3 deselected — current protected-aware suite
# runtime evidence baseline: 5319763 (715 passed, 3 deselected)
```

`tests/test_api_mode.py` and `athena/api_mode.py` are protected user files.
They are hash-verified and excluded from this suite.

## Terminal v1 classifications (ADR-0001)

| Item | Classification |
|---|---|
| Seven-tool core and internal modules | `IMPLEMENTED_AND_VERIFIED` |
| IAProxy zchat/kimi | `OPTIONAL_NOT_CONFIGURED` |
| Content Gate | `OPTIONAL_FUTURE` |
| External acceptance | `EXTERNAL_ACCEPTANCE_PENDING` |
| Olimpo O-2 through O-5 | `OPTIONAL_FUTURE` |
| Metis | `DEFERRED_BY_ADR` |
| SSH | `INTENTIONALLY_CLOSED` |

Optional items do not block the technical closure of v1. See
`docs/adr/ADR-0001-v1-scope-and-deferrals.md`.

## License

See [LICENSE](LICENSE).

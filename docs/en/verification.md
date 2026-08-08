# Verification — claimed vs verified

> Why Athena has a verifier, what it does today, and the path toward fully
> deterministic verification.

## Motivation

Agent CLIs routinely report "done, tests passing" when that is not what
happened. Evaluation evidence from 2026 is sobering: a large share of
"passes" in widely cited benchmarks corresponded to tasks that were never
actually solved, and the academic literature has named the phenomenon
(*"Confident and Wrong"*, *"Silent Semantic Failures"*). Harnesses built by
agent vendors are structurally incentivized to mark success — a neutral
verification layer does not have that conflict of interest.

Athena's verifier exists to measure, locally and per-provider, the distance
between **claimed** (what the agent says it did) and **verified** (what can
actually be confirmed).

## Two layers, different rules

| Layer | How it works | Role |
|---|---|---|
| **Advisory verifier** (implemented) | A cheap model (free tier first, e.g. opencode free) reads the executor's report plus project evidence and returns true/false. Anti-collusion: the verifier is never the same provider as the executor. FALSE → back for correction; FALSE twice → escalates to the orchestrator/human. | Triage. Never blocks alone, never produces a public number. |
| **Deterministic verifier** (implemented) | Re-runs exactly what the report claims (whitelisted test/lint commands, no shell, per-command timeout), compares real exit codes, and checks that files claimed as created actually exist. **No model anywhere in the chain** — AI judging AI would destroy the credibility of the number. | The layer that produces trustworthy `claimed vs verified` metrics. Short-circuits the advisory layer when conclusive. Controlled by `ATHENA_VERIFY_MODE=auto|deterministic|advisory`. |

The advisory layer stays useful even after deterministic checks land: many
tasks have no automatable oracle (prose, configuration, exploration), and
cheap triage avoids spending paid-model quota on verification.

## Roadmap

- [x] Persist verdicts per CLI → personal reliability ranking (local
      claimed-vs-verified rate), via `list_reliability` and the dashboard
      Confiabilidade card.
- [x] Feed reliability scores back into `recommend` (trust-weighted routing,
      30% weight, warnings for providers with ≥50% verified-false reports).
- [x] `verify=true` support in `run_combo` (FALSE report triggers failover).
- [ ] `verify=true` support in `deliberate`.
- [ ] Longer term: hidden-oracle task suites (tests the agent never sees
      while declaring success), enabling a public, comparable false-success
      rate per CLI.

---

*Português: ver `docs/pt-BR/verificacao.md`.*

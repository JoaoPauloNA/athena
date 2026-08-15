# Verification — claimed vs verified

> Why Athena has a verifier, what it does today, and the path toward fully
> deterministic verification.

## Motivation

An executor report and the observable state of a project can disagree: a
command may not have run, a cited file may not exist, or a failure may have
been omitted. Athena treats the report as a claim to inspect, not as ground
truth. This document makes no prevalence claim about that mismatch outside
Athena's own local evidence.

Athena's verifier exists to measure, locally and per-provider, the distance
between **claimed** (what the agent says it did) and **verified** (what can
actually be confirmed) — for the specific claims a report makes, not as a
general proof that the work is correct.

## Two layers, different rules

| Layer | How it works | Role |
|---|---|---|
| **Deterministic verifier** (`athena/dverify.py`, implemented) | No model anywhere in the chain. Re-runs exactly what the report claims to have run — a fixed whitelist of test/lint commands (`pytest`, `ruff`, `npm test`, …), token-split via `shlex` (never a shell), capped at 3 commands with a per-command timeout — and compares real exit codes; separately checks that files the report claims to have created/edited actually exist. Skips re-running a command if the report already admits it failed nearby (an honest report about a failure is not treated as a lie). | The layer that produces the most trustworthy signal, but only for whitelisted commands and cited files. Silent on anything else — no verdict, not a pass. Short-circuits the advisory layer when conclusive. Controlled by `ATHENA_VERIFY_MODE=auto\|deterministic\|advisory`. |
| **Advisory verifier** (`athena/verifier.py`, implemented) | A cheap model (free tier first, e.g. opencode free) reads the executor's report plus objective project evidence (`git status`, `git diff --stat`, cited-file existence) and returns true/false. Anti-collusion: the verifier is never the same provider as the executor. FALSE can trigger one corrective retry or conditional combo fallback; a repeated FALSE escalates. | Triage for whatever the deterministic layer can't decide (no automatable oracle: prose, configuration, exploration). A model judging a model: its verdict can drive the configured workflow, but is not proof and must not be reported as an objective correctness metric. |

## Scope and limits

- Both layers only judge what the report itself makes checkable: commands it cites and files it cites. A report that claims something outside those two categories is not verified either way.
- The deterministic layer's "true" means "the commands it re-ran exited 0 and the cited files exist" — not "the change is correct" or "nothing else broke."
- The advisory layer is model-based triage, not an oracle; treat its FALSE as a strong signal to look closer, and its TRUE as "nothing contradictory found," not a guarantee.
- `run_combo(verify=true)` and `ask_provider(verify=true)` use this pipeline. **`deliberate` does not run any verification** — see the roadmap below.
- A verification phase that cannot confirm its own subprocess termination reports `TERMINATION_UNCONFIRMED` rather than guessing a verdict, and that blocks fallback/lease release the same way an unconfirmed executor attempt does (see [Architecture](architecture.md#execution-lifecycle)).

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

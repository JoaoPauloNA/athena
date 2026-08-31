# Roadmap finish-to-start — Athena-MCP

## SNAPSHOT V1 TERMINAL (2026-08-31)

- **HEAD corrente:** verificar via Git; na abertura desta reconciliação o repositório estava limpo e alinhado em `ac000d8`.
- **Runtime baseline:** `5319763` — P0 `PASS`; suíte protegida `715 passed, 3 deselected`; sete tools MCP; beta equivalente ao runtime e smoke-verified.
- **ADR-0001:** escopo v1 terminal — Content Gate `OPTIONAL_FUTURE`; Metis `DEFERRED_BY_ADR`; IAProxy zchat/kimi `OPTIONAL_NOT_CONFIGURED`; aceite externo `EXTERNAL_ACCEPTANCE_PENDING`; SSH `INTENTIONALLY_CLOSED`; Olimpo O-2..O-5 `OPTIONAL_FUTURE`.
- **OLIMPO-0:** biblioteca HTTP opt-in em loopback para observação e publicação de configuração validada por preview + CAS; não executa, cancela nem autoriza tarefas.
- **WIP de engenharia:** vazio após o fechamento v1. Itens opcionais acima são estados terminais, não bloqueios ativos.
- **Arquivos protegidos:** preservados e verificados somente por SHA-256.
> **Histórico abaixo:** hashes, filas e bloqueios anteriores permanecem como checkpoints datados; não substituem este snapshot terminal.




Fonte da fila: relatório de melhoria de fluxo e desempenho de 2026-08-28. Regra operacional: WIP=1; contrato antes do código; uma fatia só libera a seguinte após saída terminal verificada.

| Ordem | Fatia | Estado nesta base | Gate de saída |
|---:|---|---|---|
| 0 | BASE-0 — contexto operacional | CONCLUÍDA EM 2026-08-28 | cinco arquivos de contexto presentes, não vazios e estruturalmente válidos; estado Git e versão consistentes |
| 1 | PERF-0 — benchmark e SLO | CONCLUÍDA EM 2026-08-28 | harness reproduzível, medição por estágio e limite de regressão; validação gerencial independente aprovada |
| 2 | PERF-1 — supervisor | CONCLUÍDA EM 2026-08-28 | bridge orientado a eventos, árvore terminada e overhead p95 dentro da meta aprovada |
| 3 | INT-ALE-0 — Aletheia | CONCLUÍDA EM 2026-08-30 | contrato real corrigido e testes cross-repo sem mock de forma |
| 4 | CFG-SEC-0 — snapshot | CONCLUÍDA EM 2026-08-28 | bytes únicos, hashes, containment, escrita atômica e cache |
| 5 | TASK-0 — contrato e durabilidade | CONCLUÍDA EM 2026-08-28 | `submit_task`, idempotência, handle e estado local sobrevivem a desconexão/reinício |
| 6 | CAP-0 — Cápsula/Aegis/Iris | CONCLUÍDA EM 2026-08-28 | primeira execução impossível sem Selo válido e ambiente mínimo |
| 7 | ROUTE-0 — Zeus/Nike | CONCLUÍDA EM 2026-08-29 | uma implementação determinística decide; cliente passa a advisory |
| 8 | FLOW-1 — ciclo de um agente | CONCLUÍDA EM 2026-08-29 | tarefa real fecha até Evidence Gate/Chronos e entrega durável |
| 9 | CLIO-0 — observabilidade | CONCLUÍDA EM 2026-08-29 | quatro níveis, fila limitada e overhead comprovado |
| 10 | MULTI-0 — Harmonia/Lease | CONCLUÍDA EM 2026-08-29 | paralelismo seguro, write-set, worktree e conflitos comprovados |
| 11 | OLIMPO-0 — controle | CONCLUÍDA EM 2026-08-29 | UI configura e observa sem autoridade operacional |
| 12 | MCP-2026 — adapter | CONCLUÍDA EM 2026-08-29 | compatibilidade anterior preservada e nova negociação validada |
| 13 | THEMIS-1 — reputação verificável | CONCLUÍDA EM 2026-08-30 | eixos separados, eventos imutáveis, incerteza e contrato Nike compatível |
| 14 | ARGOS-1 — browser QA real | CONCLUÍDA EM 2026-08-30 | navegador Chromium real, screenshot/console/status e escopo fail-closed |

Código rastreado anterior à criação desta fila existe em áreas relacionadas a configuração, Zeus/Nike e Evidence Gate. Isso não inicia retroativamente `CFG-SEC-0`, `ROUTE-0` ou outra fatia: cada uma exige contrato, escopo e gate próprios na ordem acima.

M-19 foi decidida explicitamente pelo CEO e fechada em ROUTE-0. Em 2026-08-30 o CEO autorizou alterações em Aletheia, Themis e Argos; INT-ALE-0 foi fechada em 2026-08-30. M-20 a M-30 continuam propostas aguardando decisão. Nenhuma fatia autoriza por si só commit, push, beta, release, deploy, credenciais, ação destrutiva ou infraestrutura paga.

- **ADR-0001** (`docs/adr/ADR-0001-v1-scope-and-deferrals.md`): escopo v1 fechado; classificações opcionais e diferidas são terminais e não reabrem WIP.

## Fechamento terminal e checkpoints históricos

`INT-ALE-0 — Aletheia` foi concluída em 2026-08-30 após passo de correção mínima, reparo do gate P0 (`duplicate in-flight request id` → `-32602`) e validação independente. Evidência exata: P0 lint/boundaries/p0 `PASS`; suíte Athena `-m "not regression"` excluindo `test_api_mode.py` `715 passed, 3 deselected`; cross-repo Aletheia `24 passed`; suíte Aletheia `97 passed`; `test_duplicate_and_invalid_long_ids_do_not_launch_handlers` `5/5`; hashes protegidos inalterados.

PERF-0 foi concluída em 2026-08-28 após validação gerencial independente: `git diff --check: PASS`; `Ruff on PERF-0 files: PASS`; `focused pytest: 20 passed`; smoke real com `10 samples`, `2 warmups` e guardrail `PASS`; `bridge-over-direct p95: 10.900625 ms` sob teto de caracterização de 30 ms; `incremental MCP-over-bridge p95: 0.853251 ms` sob teto de 5 ms; cleanup MCP com `exit 0`, `not forced`, `not alive` e nenhum processo `python -m athena` restante; `harness/p0_gate.py` com `lint PASS`, `boundaries PASS` e `p0 PASS`; suíte integral com `327 passed, 3 deselected`.

Essas medições e seus tetos descrevem a caracterização histórica de PERF-0 nesta máquina e seus guardrails de regressão; não são a evidência de fechamento posterior de PERF-1.

PERF-1 foi concluída em 2026-08-28 após validação gerencial independente fora do sandbox: `git diff --check: PASS`; Ruff dos Python alterados `PASS`; bridge `14 passed`; gate P0 com lint, boundaries e p0 `PASS`; suíte integral `331 passed, 3 deselected`; benchmark real com `30 samples`, `3 warmups` e guardrail `PASS`; p95 bridge-over-direct de `0.320792 ms` e p95 MCP incremental sobre o bridge de `0.5685 ms`, ambos sob teto de 5 ms; cleanup MCP com exit 0, não forçado, processo encerrado e `33 terminal runs`; nenhum filho de teste PERF-1 e nenhum novo `python -m athena` restante. Processos Athena-beta MCP long-running do desktop eram preexistentes e não foram tocados.

O sentinel fecha conservadoramente quando descendentes ordinários preservam o FD interno herdado, mas não garante detectar processo que, deliberadamente ou por política própria, feche todos os FDs herdados desconhecidos.

`CFG-SEC-0 — snapshot`, TASK-0, CAP-0, ROUTE-0, FLOW-1, CLIO-0, MULTI-0, OLIMPO-0, MCP-2026, INT-ALE-0, THEMIS-1 e ARGOS-1 permanecem fechadas. Não há fatia ativa sob WIP=1.

## Estado terminal v1 (2026-08-31)

**Sem WIP de engenharia ativo.** Escopo obrigatório autorizado e verificado: sete tools, Zeus/Nike/Chronos/EG-3A/Clio/Harmonia/Capsule/Iris/OLIMPO-0/Flow/Tasks (runtime baseline `5319763`).

Estados terminais fora do runtime obrigatório: Content Gate `OPTIONAL_FUTURE` até CG-0; Metis `DEFERRED_BY_ADR`; Olimpo O-2..O-5 `OPTIONAL_FUTURE`; zchat/kimi `OPTIONAL_NOT_CONFIGURED`; aceite externo `EXTERNAL_ACCEPTANCE_PENDING`; SSH `INTENTIONALLY_CLOSED`. Reabertura exige nova decisão canônica.

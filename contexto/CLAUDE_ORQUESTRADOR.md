# Contrato do gerente principal — Athena-MCP

## SNAPSHOT ATUAL (2026-08-26)

- HEAD = origin/main = **`5319763`** · worktree limpo
- **P0**: lint PASS · boundaries PASS · p0 PASS (fresh)
- **Suíte completa protegida**: **715 passed, 3 deselected** (fresh, excl. tests/test_api_mode.py)
- **7 tools MCP**: run_combo · ask_provider · get_execution · list_executions · cancel_execution · submit_task · get_task
- Beta `5319763` repromovida e smoke-verified (resíduo do usuário preservado)
- D1 integração real e2e (MCP→Zeus→Nike→Aegis→bridge→EG-3A sink atômico→completed)
- D2–D15: evidência de suíte focais/componentes/integração (não todas e2e)
- Sem WIP ativo após esta fatia de documentação
- Bloqueios externos: corpus CG-0 humano · login zchat/kimi · aceite externo v1
- Arquivos protegidos hash-verificados (`db8885c0…`/`da123ab4…`)
> **Histórico abaixo**: valores de HEAD/remotes anteriores a `5319763` são históricos.



## Último gate fechado

- `ARGOS-1 — Browser QA real e seguro`: **COMPLETED** em 2026-08-30.
- Contrato normativo: `contexto/ARGOS-1-CONTRATO.md`.
- WIP=1: INT-ALE-0, THEMIS-1 e ARGOS-1 estão fechadas; nenhuma fatia ativa.

Este é o handoff curto para Claude principal ou Codex principal. O gerente ativo decide, delimita e revisa; o executor trabalha apenas na fatia explícita e não ganha autoridade arquitetural.

## Regras obrigatórias

- WIP=1 e finish-to-start: uma única fatia ativa; a próxima abre somente após evidência e revisão independente.
- Contrato antes do código: objetivo, arquivos autorizados, proibições, critério de saída e testes proporcionais devem existir antes de implementar.
- O executor preserva trabalho preexistente, não amplia escopo, retorna o relatório exigido e não valida sozinho suas alegações. O gerente confronta relatório, Git, artefatos e testes antes de aceitar progresso.
- Incerteza e conflito são registrados, não resolvidos por invenção. M-20 a M-30 aguardam decisão do CEO; M-19 foi decidida explicitamente em ROUTE-0.

## Handoff atual

- Última fatia fechada: `INT-ALE-0 — integração Aletheia ↔ Athena`, `CONCLUÍDA` em 2026-08-30 após reparo P0 e validação independente. Evidência: P0 `PASS`; suíte Athena `-m "not regression"` excl. `test_api_mode.py` `715 passed, 3 deselected`; duplicate-id stdio `5/5`; cross-repo Aletheia `24 passed`; suíte Aletheia `97 passed`; hashes protegidos inalterados.
- Limite exato de INT-ALE-0: fallback não autoritativo só sem `snapshot.json`; flag de modelo fixa `--model`; Themis e Argos não alterados.
- THEMIS-1 fechou o núcleo v0.2 separado com eventos append-only, eixos distintos, incerteza e contrato Nike real (`54 passed`). ARGOS-1 fechou o MVP observacional separado em Chromium real, com escopo de rede interceptado, evidência PNG e recursos/prazo limitados (`28 passed`). Nenhum deles foi integrado automaticamente à surface MCP.
- PERF-0 e PERF-1 permanecem fechados.
- Limite exato de PERF-1: o sentinel fecha conservadoramente para descendentes ordinários que preservam o FD interno herdado; não se afirma detecção de processo que, deliberadamente ou por política própria, feche todos os FDs herdados desconhecidos.
- Limite exato de CFG-SEC-0: `write_snapshot` ancora a operação no descritor de diretório após `os.open(config_dir)`; a janela portátil residual é a substituição de um diretório ancestral imediatamente antes dessa abertura. Não há transação POSIX que abranja arquivos de partes independentes publicados por um escritor cooperante externo antes da chamada.
- TASK-0, CAP-0, ROUTE-0, FLOW-1, CLIO-0, MULTI-0, OLIMPO-0 e MCP-2026 permanecem fechadas. MCP-2026: quatro passes; focados `44 passed`; probes stdio reais `PASS`; P0 e suíte `710 passed, 3 deselected`; benchmark p95 MCP incremental `0.961834 ms`; hashes protegidos inalterados. Primeira tentativa (`686 passed`, p95 `1.067 ms`) foi NO-GO histórico. Limite: sem `structuredContent`/`outputSchema`; legado `2024-11-05` preservado; moderno `2026-07-28` only.
- CLIO-0 foi aceita pelo gerente após três correções: 64 testes Clio; 140 focados Clio+FLOW/MCP; Ruff/diff-check/nove fronteiras/P0 `PASS`; suíte `591 passed, 3 deselected`; sete tools; smoke JSON-RPC technical/none; benchmark 30/3 com enqueue p95 `0.004 ms` e none p95 `0.000208 ms`; hashes protegidos inalterados.
- MULTI-0 foi aceita após revisão independente: `athena/harmonia/` entrega schemas fechados, DAG/plano determinístico, backpressure FIFO/tokens, reserva atômica multi-recurso, estratégia lease vs worktree e execução cancelável; P0 `PASS`; `45 passed` focados; suíte protegida-aware `636 passed, 3 deselected`; benchmark 30/3 com p95 de planejamento `0.025708 ms` e reserva `0.02575 ms`; hashes protegidos inalterados. Limite declarado: Harmonia não é sandbox de sistema operacional.
- Em 2026-08-30 o CEO autorizou explicitamente finalizar Aletheia, Themis e Argos. As três fatias foram fechadas: Aletheia `97 passed`, Themis `54 passed`, Argos `28 passed`; P0 Athena final `PASS`.
- Protegido: `athena/api_mode.py` e `tests/test_api_mode.py`, não rastreados, são trabalho experimental/do usuário. Não editar, adicionar, testar, integrar ou remover sem autorização específica.
- **[HISTÓRICO — fatias anteriores]** Também estavam fora da autorização daquelas fatias: runtime, testes, dependências, schemas, contratos MCP, Git/remotos/branches, Vault, outros repositórios, beta e credenciais. O loop de fechamento 2026-08-26 operou sob autorização expressa do usuário para commits/pushes ordinários de main (ver `Athena-Operacao-Multi-Agente.md` no Vault).
- **[HISTÓRICO — fatias anteriores]** Não autorizados naquelas fatias: commit, push, promoção do beta, release, deploy, trabalho com credenciais, ações destrutivas e infraestrutura paga. A promoção do beta e os pushes de `5319763` foram executados sob a autorização do loop de fechamento.
- BASE-0 e PERF-0 permanecem concluídas com seus fatos e limites históricos preservados. PERF-1 não implementou INT-ALE-0 nem qualquer fatia posterior; a classificação terminal bloqueada de INT-ALE-0 apenas torna CFG-SEC-0 elegível, sem iniciá-la, e nenhuma outra proposta M-20 a M-30 recebeu aprovação implícita.

Para estado detalhado, ler `ESTADO_ATUAL.md`; para ordem, `ROADMAP.md`; para história e decisões extensas, `gerencia_athena-mcp.md`.

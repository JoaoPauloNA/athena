# Contrato do gerente principal — Athena-MCP

## SNAPSHOT V1 TERMINAL (2026-08-31)

- **Runtime baseline de evidência:** `5319763` — P0 lint/boundaries/p0 `PASS`; suíte histórica `715 passed, 3 deselected`; sete tools MCP. Suíte corrente após hardening de instalação: `716 passed, 3 deselected`.
- **Commits documentais anteriores:** `a96c2e1` e `ee13dad`. Eles reconciliam documentação; não substituem a baseline de runtime.
- O repositório estava limpo e alinhado com `origin/main` quando inspecionado. **O HEAD corrente deve ser sempre verificado diretamente pelo Git**; este documento não fixa o hash do commit que o altera.
- D1 integração real e2e (MCP→Zeus→Nike→Aegis→bridge→EG-3A sink atômico→completed).
- D2–D15: evidência de suítes focais, componentes e integração; nem todas são e2e.
- Sem WIP de engenharia ativo após o fechamento técnico v1.
- ADR-0001 fecha como não bloqueantes: Content Gate `OPTIONAL_FUTURE`; Metis `DEFERRED_BY_ADR`; zchat/kimi `OPTIONAL_NOT_CONFIGURED`; aceite externo `EXTERNAL_ACCEPTANCE_PENDING`; SSH `INTENTIONALLY_CLOSED`.
- Arquivos protegidos permaneceram fora do escopo e foram preservados.
> **Histórico abaixo:** hashes, contagens e gates anteriores permanecem somente como checkpoints datados; não representam o HEAD corrente.

## Último gate fechado

- **Fechamento técnico Athena v1:** **FECHADO** em 2026-08-31 sob ADR-0001, após validação fresca do runtime, beta, repositórios independentes e documentação canônica.
- O commit corrente deve ser consultado pelo Git, não embutido neste arquivo.
- **Checkpoints históricos já fechados:** reconciliação documental, INT-ALE-0, THEMIS-1 e ARGOS-1. Nenhum reabre WIP.

Este é o handoff curto para Claude principal ou Codex principal. O gerente ativo decide, delimita e revisa; o executor trabalha apenas na fatia explícita e não ganha autoridade arquitetural.

## Regras obrigatórias

- WIP=1 e finish-to-start: uma única fatia ativa; a próxima abre somente após evidência e revisão independente.
- Contrato antes do código: objetivo, arquivos autorizados, proibições, critério de saída e testes proporcionais devem existir antes de implementar.
- O executor preserva trabalho preexistente, não amplia escopo, retorna o relatório exigido e não valida sozinho suas alegações. O gerente confronta relatório, Git, artefatos e testes antes de aceitar progresso.
- Incerteza e conflito são registrados, não resolvidos por invenção. M-20 a M-30 aguardam decisão do CEO; M-19 foi decidida explicitamente em ROUTE-0.

## Handoff atual

- Última fatia fechada: **fechamento técnico Athena v1**, concluído em 2026-08-31 sob ADR-0001 após validação proporcional e reconciliação canônica.
- **Checkpoint histórico — INT-ALE-0:** integração Aletheia ↔ Athena concluída após reparo P0 e validação independente; fallback não autoritativo somente sem `snapshot.json` e flag de modelo fixa `--model`.
- **Checkpoints históricos — THEMIS-1 e ARGOS-1:** Themis v0.2 separado com `54 passed`; Argos observacional separado com `28 passed` e smoke real. Esses checkpoints antecedem a reconciliação documental atual.
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

## Última fatia fechada (2026-08-31)

**Fechamento técnico Athena v1.** Runtime baseline de evidência: `5319763` (715 testes, P0 fresh, 7 tools); validação fresca repetiu os mesmos gates. ADR-0001 torna itens opcionais terminais sem ampliar o runtime. Regra vigente: saída de GLM/OX exige revisão independente; o HEAD corrente é sempre consultado no Git.

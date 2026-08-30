# Contrato FLOW-1 — ciclo durável de um agente

Estado: **COMPLETED** em 2026-08-29, após aceitação gerencial independente.

## Fechamento verificado

- Ciclo real entre três processos passou: `submit_task`, execução `run_combo`
  com ROUTE-0/CAP-0 e verificação determinística, seguido de `get_task` após
  reinício. A projeção terminal persistida foi `completed`, Evidence Gate
  `pass`, Chronos `CLOSED` e entrega `awaiting_human_review`.
- Migração SQLite v1 -> v2, concorrência, repetição, falhas de execução/store,
  sanitização e fronteiras foram cobertas; teste focado final: `178 passed`.
- Gate final protegido-aware: Ruff e diff-check passaram; suíte não-regressão
  `527 passed, 3 deselected`; exatamente sete tools; hashes protegidos
  inalterados.
- Benchmark real 30/3 passou: p95 bridge `1.031333 ms`, p95 MCP incremental
  `1.322376 ms`, ambos sob 5 ms; 33 execuções terminais, processo encerrado sem
  força. Nenhum commit, push ou beta foi realizado.

## Objetivo

Fechar o primeiro ciclo operacional de uma tarefa já submetida: vínculo durável,
roteamento ROUTE-0, autorização CAP-0, execução de um único agente, verificação
determinística, Evidence Gate, decisão Chronos e projeção terminal consultável.
Não implementar correção automática nem paralelismo nesta fatia.

## Fluxo canônico

1. `submit_task` continua apenas criando a tarefa durável em `queued`.
2. `run_combo` e `ask_provider` aceitam `task_handle` opcional. Sem ele, o
   comportamento já aceito permanece igual.
3. Com `task_handle`, a composição de produção exige também `verification` não
   vazia e faz transição atômica `queued -> running` antes de qualquer runner.
4. ROUTE-0 escolhe o provider; CAP-0 sela e autoriza a tentativa; Iris/bridge
   executa exatamente um agente.
5. O verifier produz os fatos determinísticos. Resultado de execução e achados
   viram um envelope interno sanitizado; comandos, prompts, cwd, stdout, stderr,
   valores de ambiente e conteúdo da tarefa não entram na projeção durável.
6. Evidence Gate emite `validation_status` e sempre mantém
   `delivery_status=awaiting_human_review`, conforme EG-3A vigente.
7. Chronos registra uma tentativa. PASS fecha o ciclo de correção; qualquer
   outro veredito nesta fatia termina em revisão humana, pois FLOW-1 não inventa
   novo critério nem reexecuta automaticamente.
8. A store grava a projeção final antes de o MCP declarar a entrega concluída.
   `get_task` permite consultar essa projeção após desconexão ou reinício.

## Estados e projeção

- Estados FLOW-1 permitidos: `queued`, `running`, `awaiting_human_review`.
- `get_task` preserva os campos TASK-0 e pode acrescentar apenas:
  `execution_id`, `execution_status`, `validation_status`, `delivery_status`,
  `chronos_action`, `attempts_used` e `reason_codes` sanitizados.
- A transição deve usar revisão/estado esperado. Handle ausente, já iniciado ou
  terminal abstém antes do runner com reason code estável.
- A mesma tarefa terminal nunca é executada novamente por repetição da chamada.
- Falha ao iniciar a transição impede execução. Falha ao persistir o fechamento
  não pode ser apresentada como entrega durável bem-sucedida.

## Fronteiras

- `mcp_server` recebe um controlador FLOW por protocolo/injeção; não importa
  diretamente Evidence Gate, Chronos ou SQLite.
- Um adapter interno de FLOW pode compor TASK-0, verifier, Evidence Gate e
  Chronos. `mcp_runtime` injeta esse adapter na composição de produção.
- Evidence Gate valida; não autoriza execução. Chronos decide fechamento,
  reabertura ou revisão; não executa. Aegis continua única autorização.
- Nenhuma IA, rede, descoberta ou instalação entra no controle FLOW-1.

## Superfície e compatibilidade

- Permanecem exatamente sete tools MCP; nenhuma tool nova.
- `task_handle` é opcional em `run_combo`/`ask_provider`. Quando presente,
  `verification` objetiva é obrigatória e ao menos um file/command claim deve
  existir. Sem `task_handle`, não se cria tarefa implícita.
- Campos e comportamento anteriores permanecem reconhecidos fora do modo FLOW.
- Erros públicos usam apenas códigos estáveis: tarefa ausente, não executável,
  verificação ausente, transição/store indisponível e fechamento não persistido.

## Escopo permitido

`contexto/FLOW-1-CONTRATO.md` e checkpoints; `athena/tasks/` para transições e
projeção durável; novo adapter interno `athena/flow/`; plumbing mínimo em
`mcp_runtime`, `mcp_stdio`, `mcp_server` e contratos; testes focados, E2E real e
documentação técnica mínima. Migração SQLite deve ser explícita, idempotente e
compatível com banco TASK-0 v1 existente.

## Proibições

Não criar worker/background queue, retry automático, segundo agente, Harmonia,
Clio, Olimpo, aprovação humana autenticada, nova tool, LLM, provider, rede,
dependência ou escrita fora do state dir isolado. Não alterar Aletheia, Themis ou
Argos. Não ler/editar/testar/importar `athena/api_mode.py` nem
`tests/test_api_mode.py`. Não tocar `Aegis/build/`. Sem commit, push, beta,
release, deploy, credenciais ou ação destrutiva.

## Aceitação

1. Contrato ACTIVE precedeu código e WIP=1 foi preservado.
2. Tarefa submetida liga-se a uma execução ROUTE-0/CAP-0 real sem nova tool.
3. Store v1 existente migra sem perder handle, idempotência ou timestamps.
4. Claim determinístico passando produz Evidence Gate `pass`, Chronos `CLOSED`
   e entrega durável `awaiting_human_review`.
5. Claim falhando, execução falhando ou veredito inconclusivo nunca vira PASS e
   termina em revisão humana durável.
6. Handle ausente, desconhecido, running ou terminal nunca alcança o runner.
7. Repetição concorrente do mesmo handle executa no máximo uma vez.
8. `get_task` após novo processo preserva somente a projeção sanitizada.
9. Sete tools, ROUTE-0 e CAP-0 permanecem verdes; nenhum payload sensível vaza.
10. Diff-check, Ruff, boundaries, P0, suíte integral, testes adversariais e
    smoke JSON-RPC real de submit -> run -> get após reinício passam.

## Limite declarado

FLOW-1 não recupera automaticamente processo morto durante `running`. Essa
política exige lease/heartbeat durável e pertence a MULTI-0. O teste terminal
deve provar persistência após fechamento normal, sem alegar crash recovery.

## Rollback

Restaurar somente plumbing/adapters/migração FLOW-1, preservando TASK-0, CAP-0 e
ROUTE-0. Uma regressão reabre FLOW-1 e bloqueia CLIO-0.

# Estado atual — Athena-MCP

## SNAPSHOT V1 TERMINAL (2026-08-31)

- **Runtime baseline (evidência de testes): `5319763`** — commits documentais não alteram essa evidência
- Reconciliação documental anterior: `a96c2e1` · repositório limpo e alinhado na produção do handoff
- HEAD atual: **verificar sempre via Git** (não fixado em documento)
- **P0**: lint PASS · boundaries PASS · p0 PASS (fresh)
- **Suíte completa protegida**: **715 passed, 3 deselected** (fresh, excl. tests/test_api_mode.py)
- **7 tools MCP**: run_combo · ask_provider · get_execution · list_executions · cancel_execution · submit_task · get_task
- Beta `5319763` repromovida e smoke-verified (resíduo do usuário preservado)
- D1 integração real e2e (MCP→Zeus→Nike→Aegis→bridge→EG-3A sink atômico→completed)
- D2–D15: evidência de suíte focais/componentes/integração (não todas e2e)
- Sem WIP de engenharia ativo após o fechamento técnico v1
- Estados terminais não bloqueantes: Content Gate `OPTIONAL_FUTURE`; Metis `DEFERRED_BY_ADR`; zchat/kimi `OPTIONAL_NOT_CONFIGURED`; aceite externo `EXTERNAL_ACCEPTANCE_PENDING`; SSH `INTENTIONALLY_CLOSED`
- Arquivos protegidos hash-verificados (`db8885c0…`/`da123ab4…`)
> **Histórico abaixo**: valores de HEAD/remotes anteriores a `5319763` são históricos.



Fotografia verificada diretamente em 2026-08-29. Esta classificação descreve o checkout local, não o beta, deploys ou outros repositórios.

- **ADR-0001** (`docs/adr/ADR-0001-v1-scope-and-deferrals.md`): escopo v1 terminal; as classificações opcionais e diferidas são estados finais e não defeitos do núcleo.

## Base Git

> **[HISTÓRICO — snapshot BASE-0]** O bloco de hashes abaixo reflete o momento
> da fatia BASE-0. Estado atual: verificar sempre via Git; na reconciliação
> documental de 2026-08-30 o repositório estava limpo e alinhado com
> runtime baseline `5319763` e reconciliação documental `a96c2e1`.

- Branch: `main`.
- HEAD: `d69ba39a242fd5348a585dd94e83e83b5d1f489a`.
- `origin/main`: `fc58ed9926e2f7bf37601691f40ded5267ff6ac9`.
- Relação observada: `main` está seis commits à frente de `origin/main`.
- Versão em `pyproject.toml`: `0.2.0`.
- Antes das mudanças documentais de BASE-0, o worktree tinha somente `athena/api_mode.py` e `tests/test_api_mode.py` não rastreados. Na entrega de BASE-0, somaram-se os quatro novos arquivos de contexto autorizados. Esse é um fato histórico de BASE-0; posteriormente, os bytes validados de PERF-0 acrescentaram `harness/benchmark_orchestration.py`, `tests/test_benchmark_orchestration.py` e a alteração de `harness/README.md`. Os dois arquivos experimentais/do usuário permanecem preservados; não pertencem à implementação comprovada e não foram alterados.

## IMPLEMENTED

- O pacote expõe por stdio/JSON-RPC exatamente sete tools: as cinco originais (`run_combo`, `ask_provider`, `get_execution`, `list_executions`, `cancel_execution`) e `submit_task`/`get_task`.
- TASK-0 entrega fila SQLite durável e preguiçosa, idempotência SHA-256 separada por domínio, projeção sanitizada e schemas públicos fechados para as duas tools; não há worker, execução em background nem retry engine.
- O runtime rastreado compõe execução local com bridge, router, registro e lease em memória, perfis, verificação e cancelamento.
- ROUTE-0 integra Zeus/Nike deterministicamente no caminho MCP de produção: `run_combo` trata receitas do cliente como advisory, `ask_provider` continua direto mas sujeito a configuração/elegibilidade, e ausência ou adulteração de contexto/configuração/registro abstém antes do runner.
- O núcleo modular rastreado contém contratos e testes para `execution`, `registry`, `lease`, `profiles`, `transport`, `bridge`, `router`, `verifier`, `mcp_server` e `zeus`; as fronteiras centrais estão declaradas no `pyproject.toml` para verificação pelo import-linter.
- EG-3A possui finalização e sink interno rastreados, opt-in, ativados somente com as duas configurações explícitas exigidas pela composição atual. Não adiciona tool MCP.
- FLOW-1 liga uma tarefa durável a uma execução ROUTE-0/CAP-0, verificação determinística, Evidence Gate e Chronos; persiste projeção terminal sanitizada consultável após reinício, mantendo entrega em revisão humana.
- CLIO-0 registra eventos técnicos FLOW em SQLite local fora do caminho quente, com quatro níveis, precedência anti-elevação, fila e writers limitados, retenção e modo `none` sem storage comum.
- MULTI-0 entrega Harmonia determinística interna, DAG e grupos limitados, backpressure/tokens, leases atômicos FIFO com heartbeat/TTL, isolamento por worktree sintética e verificação de write-set. Não adiciona tool MCP nem constitui sandbox de sistema operacional.
- OLIMPO-0 entrega biblioteca HTTP opt-in em loopback, composição explícita, frontend responsivo local, CSP/CSRF, projeções sanitizadas e publicação de configuração validada por preview + CAS. Não inicia com o MCP e não executa, cancela nem autoriza tarefas.
- MCP-2026 entrega adapter dual-era stdio para `2026-07-28`: prevalidação antes de reserva, `server/discover` só com `_meta` moderno completo, validação JSON genérica limitada, envelopes modernos com `resultType: complete` e `serverInfo`, cancelamento in-flight-only com lock até escrita, identidade `0.2.0` e legado `initialize`/`2024-11-05` preservado. Sete tools inalteradas; sem `structuredContent`/`outputSchema`.
- INT-ALE-0 fecha a ponte pública Aletheia ↔ Athena: `ask_provider` retorna `ProviderResult` estruturado; modelo chega como argv `--model` separado; timeout vira deadline real do lifecycle; snapshot corrupto falha fechado; fallback só com snapshot ausente; modelo/timeout validados antes da execução.

## PARTIAL

- `transport/` contém SSH fechado e testável, porém não tem consumidor na composição de produção atual.
- Moiras shadow depende de injeção e opt-in; Chronos fecha o ciclo de um agente em FLOW-1, mas recuperação de processo morto e fluxo multiagente permanecem futuros.
- EG-3A finaliza artefatos localmente quando configurado, mas revisão humana permanece obrigatória e o Evidence Gate não autoriza execução.

## PLANNED

- A fila válida é a de `ROADMAP.md`. INT-ALE-0, THEMIS-1 e ARGOS-1 foram concluídas em 2026-08-30; não há fatia ativa sob WIP=1.
- M-20 a M-30 são propostas aguardando decisão do CEO; não são arquitetura aprovada nem funcionalidade ativa.
- M-19 foi decidida explicitamente em ROUTE-0: Zeus/Nike determinísticos no runtime; modelos Luna Low a Terra Medium ficam apenas para sugestão offline futura no Olimpo, nunca no caminho quente.

## DEFECT/RISK

- Registro e lease usados pelo runtime são voláteis. EOF abandona/cancela trabalho não terminal em vez de apenas desanexar o cliente.
- O servidor usa `ThreadPoolExecutor` com limite de workers, mas fila interna sem backpressure explícito. A fronteira MCP ainda aceita receitas com comando, `cwd` e ambiente do cliente; ROUTE-0 impede que a ordem dessas receitas escolha o provider, e CAP-0 restringe autorização e ambiente antes do bridge.
- PERF-1 removeu o `ps` repetido do caminho quente do bridge e passou a usar espera orientada à conclusão, sinais monotônicos limitados, inspeção atrasada/esparsa e sentinel POSIX herdado. O sentinel fecha conservadoramente para descendentes ordinários que preservam o FD interno herdado, mas não comprova detecção de processo que, deliberadamente ou por política própria, feche todos os FDs herdados desconhecidos.
- **[HISTÓRICO — BASE-0]** Os seis commits locais ainda não estavam em `origin/main`. Não se infere publicação, promoção do beta ou deploy a partir do estado local.
- BASE-0 não reexecutou a suíte integral porque sua autorização era somente documental. Depois, a validação independente de PERF-0 registrou uma nova suíte integral verde, detalhada abaixo; isso não altera o fato histórico de BASE-0.

## Checkpoint PERF-0 — benchmark e SLO (2026-08-28)

- PERF-0 está `CONCLUÍDA` após validação gerencial independente executada fora do sandbox do Codex2, restrito por socket, contra os bytes atuais.
- Evidência exata do fechamento: `git diff --check: PASS`; `Ruff on PERF-0 files: PASS`; `focused pytest: 20 passed`; `real smoke: 10 samples, 2 warmups, guardrail PASS`; `bridge-over-direct p95: 10.900625 ms (30 ms characterization ceiling)`; `incremental MCP-over-bridge p95: 0.853251 ms (5 ms ceiling)`; `MCP cleanup: exit 0, not forced, not alive; no remaining python -m athena process`; `harness/p0_gate.py: lint PASS, boundaries PASS, p0 PASS`; `full non-regression suite: 327 passed, 3 deselected`.
- Os valores de 30 ms e 5 ms são tetos de caracterização desta máquina para detectar regressão. Não constituem nem aprovam o SLO futuro proposto de 5 ms para o bridge.
- Com o gate fechado, PERF-1 é a próxima fatia elegível da fila finish-to-start e permanece `NÃO INICIADA`; este checkpoint não a abre nem implementa.

## Checkpoint PERF-1 — supervisor orientado a eventos (2026-08-28)

- PERF-1 está `CONCLUÍDA` após validação gerencial independente fora do sandbox contra os bytes atuais.
- Evidência exata: `git diff --check: PASS`; Ruff dos Python alterados de PERF-1 `PASS`; testes focados do bridge `14 passed`; `harness/p0_gate.py` com lint, boundaries e p0 `PASS`; suíte integral `331 passed, 3 deselected`; benchmark real com `30 samples`, `3 warmups` e guardrail `PASS`; `bridge-over-direct p95: 0.320792 ms` sob teto de 5 ms; `incremental MCP-over-bridge p95: 0.5685 ms` sob teto de 5 ms; cleanup MCP com `exit 0`, `not forced`, `not alive` e `33 terminal runs`; nenhum filho de teste PERF-1 restante; nenhum novo `python -m athena` restante. Processos Athena-beta MCP long-running do desktop eram preexistentes e não foram tocados.
- O fast path de sucesso não repete inspeções da tabela de processos. A supervisão aguarda conclusão e sinais limitados de timeout/cancelamento; inspeções de descendentes são atrasadas, esparsas e forçadas em eventos relevantes de teardown.
- A garantia do sentinel é deliberadamente estreita: ele fecha de modo conservador para descendentes ordinários que preservam o FD interno herdado. Não se afirma detecção de processo que, deliberadamente ou por política de fechamento de descritores, encerre todos os FDs herdados desconhecidos.
- Registro histórico de PERF-1: INT-ALE-0 esteve bloqueada por proteção do CEO naquele checkpoint. A proteção foi revogada explicitamente em 2026-08-30 e INT-ALE-0 foi depois implementada e fechada; o estado atual está no checkpoint próprio abaixo.

## Checkpoint CFG-SEC-0 — snapshot seguro (2026-08-28)

- CFG-SEC-0 está `CONCLUÍDA` após validação gerencial independente contra os bytes atuais do worktree (implementação presente, não commitada, em `athena/config_loader.py`, `athena/bridge/runner.py` e testes correspondentes).
- Evidência exata: `git diff --check: PASS`; ruff nos arquivos alterados `PASS`; `pytest tests/test_config_loader.py`: `47 passed`; conjunto focado (`test_config_loader.py`+`test_cfg0a_states.py`+`test_bridge.py`): `77 passed`; `harness/p0_gate.py`: `lint PASS`, `boundaries PASS`, `p0 PASS`; suíte integral: `369 passed, 3 deselected`; `test_benchmark_orchestration.py`: `20 passed`; benchmark real com `30 samples`, `3 warmups`, guardrail `PASS`, `bridge_over_direct p95: 0.441751 ms`, `incremental_mcp_over_bridge p95: 0.442125 ms`, ambos sob teto de 5 ms; cleanup MCP `exit 0`, não forçado, não vivo, `33 terminal runs`.
- Verificado: nenhum processo órfão de teste; nenhum arquivo temporário residual; hashes de `athena/api_mode.py` e `tests/test_api_mode.py` inalterados; escopo do `git status` sem expansão.
- Reforço confirmado por leitura do diff: schemas estritos com allowlist de campos, rejeição de valores de segredo (incluindo em `base_url`), exigência de `secret_ref` no formato `scheme:item`, parsing JSON com limites de profundidade/itens/bytes e UTF-8 estrito, leitura de partes contida por `dir_fd`/`O_NOFOLLOW`, publicação atômica do snapshot por arquivo temporário exclusivo com fsync e `os.replace` no mesmo `dir_fd`, e cache de snapshot com isolamento profundo (freeze/thaw).
- `TASK-0 — contrato e durabilidade` foi revalidada em 2026-08-28: schemas públicos fechados, formato/Ruff/72 focados/P0/suíte, smoke JSON-RPC de reinício e benchmark 30/3 passaram; p95 bridge `0.385166 ms` e MCP incremental `0.4995 ms`, ambos sob 5 ms. CAP-0 foi aceita após correção e validação gerencial independente: Aegis `195 passed`; Athena focado `109 passed`; oito contratos de importação; suíte não-regressão `457 passed, 3 deselected`; benchmark 30/3 com p95 bridge `1.202625 ms` e MCP incremental `1.862125 ms`, ambos sob 5 ms; hashes protegidos inalterados. Esse era o checkpoint imediatamente anterior à abertura de ROUTE-0.

## Checkpoint ROUTE-0 — soberania Zeus/Nike (2026-08-29)

- ROUTE-0 está `CONCLUÍDA` após correções e validação gerencial independente contra os bytes atuais não commitados.
- Evidência exata: `git diff --check: PASS`; Ruff protegido-aware `PASS`; testes focados finais `159 passed`; gate P0 com lint, boundaries e p0 `PASS`; suíte integral não-regressão `475 passed, 3 deselected`; smoke JSON-RPC real escolheu o provider interno apesar da primeira receita enganosa e preservou CAP-0; sete tools preservadas; benchmark isolado com `30 samples`, `3 warmups`, p50 `0.303208 ms`, p95 `0.525458 ms` e máximo `0.69025 ms`, sob teto de 5 ms.
- Adulteração de configuração ou registro após aquecimento do cache abstém antes do runner; contexto ausente/inválido, receita divergente/conflituosa e provider direto inelegível retornam reason codes estáveis e sanitizados.
- Hashes protegidos permaneceram `db8885c0...6d796` e `da123ab4...8150`; os arquivos não foram lidos, editados, importados nem testados. Nenhum commit, push ou beta foi realizado.
- FLOW-1 foi concluída posteriormente; seu contrato e evidências estão no checkpoint abaixo.

## Checkpoint FLOW-1 — ciclo durável de um agente (2026-08-29)

- FLOW-1 está `CONCLUÍDA` após validação independente dos bytes atuais não commitados.
- E2E real em três processos comprovou `submit_task -> run_combo -> get_task` após reinício, com execução `completed`, Evidence Gate `pass`, Chronos `CLOSED` e entrega durável `awaiting_human_review`.
- Evidência final: Ruff e diff-check `PASS`; focados `178 passed`; suíte protegida-aware `527 passed, 3 deselected`; sete tools preservadas; benchmark real 30/3 com p95 bridge `1.031333 ms` e MCP incremental `1.322376 ms`, guardrail `PASS`, cleanup não forçado e 33 execuções terminais.
- Hashes protegidos permaneceram `db8885c0...6d796` e `da123ab4...8150`; nenhum commit, push ou beta foi realizado. CLIO-0 foi aberta posteriormente por contrato próprio.

## Checkpoint CLIO-0 — observabilidade não bloqueante (2026-08-29)

- CLIO-0 está `CONCLUÍDA` após três passes de correção e validação gerencial independente dos bytes não commitados.
- Quatro níveis e precedência foram fechados; `complete` sem protetor falha fechado, `none` não cria writer/storage comum, e falhas/saturação permanecem fora do resultado da tarefa.
- Evidência: 64 testes Clio; 140 focados Clio+FLOW/MCP; Ruff/diff-check/nove fronteiras/P0 `PASS`; suíte `591 passed, 3 deselected`; sete tools; smoke JSON-RPC technical/none; benchmark 30/3 com enqueue p95 `0.004 ms` e none p95 `0.000208 ms`.
- Hashes protegidos inalterados; sem commit, push ou beta. MULTI-0 foi aberta posteriormente por contrato próprio.

## Checkpoint MCP-2026 — adapter dual-era stdio (2026-08-29)

- MCP-2026 está `CONCLUÍDA` após quatro passes, revisão independente e gate integral final.
- Histórico NO-GO preservado: a primeira execução (`10 passed` focados, suíte `686 passed`, p95 MCP incremental `1.067 ms`) não fechou porque versão incompatível podia reservar execução antes do erro; o segundo NO-GO exigiu prevalidação, `clientInfo` opcional, envelopes completos, linha limitada e cancelamento sem race.
- Evidência final: `git diff --check: PASS`; Ruff nos Python alterados `PASS`; focados MCP `44 passed`; probes stdio reais discover/versão/list/call/cancel/legacy `PASS`; `harness/p0_gate.py` lint/boundaries/p0 `PASS`; suíte protegida-aware `710 passed, 3 deselected`; sete tools em ordem determinística; benchmark 30/3 com p95 bridge `0.475251 ms` e MCP incremental `0.961834 ms`, guardrail `PASS`, cleanup não forçado e 33 execuções terminais; hashes protegidos inalterados; nenhum `python -m athena` órfão.
- Limite declarado: sem `structuredContent`/`outputSchema`; sem HTTP/SSE/OAuth/subscriptions/elicitation/sampling/Tasks; legado permanece em `2024-11-05` via `initialize`; moderno aceita somente `2026-07-28`; `MAX_INPUT_LINE_BYTES=65536`.
- Nenhum commit, push ou beta foi realizado.

## Checkpoint INT-ALE-0 — integração Aletheia (2026-08-30)

- INT-ALE-0 está `CONCLUÍDA` após passo de correção mínima e validação gerencial independente contra os bytes atuais não commitados.
- Evidência exata: Ruff `PASS`; cross-repo Aletheia `24 passed`; suíte Aletheia integral `97 passed`; P0 lint/boundaries/p0 `PASS`; suíte Athena `-m "not regression"` excluindo `test_api_mode.py` `715 passed, 3 deselected`; `test_mcp_stdio.py::test_duplicate_and_invalid_long_ids_do_not_launch_handlers` `5/5` consecutivos; hashes protegidos inalterados; nenhum processo órfão de CLI sintética.
- Verificado: duplicate in-flight JSON-RPC id retorna `-32602`; snapshot corrupto/inválido, caminho malformado e symlink quebrado falham sem execução; fallback não autoritativo só quando `snapshot.json` está genuinamente ausente; modelo e timeout validados antes do bridge; flag de modelo determinística `--model`.
- Limite declarado: Themis e Argos não foram alterados; campanhas reais com CLIs de produção permanecem dependentes de configuração local do usuário.
- Nenhum commit, push ou beta foi realizado.

## Checkpoint THEMIS-1 — reputação verificável (2026-08-30)

- THEMIS-1 está `CONCLUÍDA` no repositório separado `Athena/Themis`, sem alteração do runtime Athena.
- O motor v0.2 separa capacidade, honestidade, confiabilidade do harness e eficiência; `HONEST_FAILURE`, `FALSE_SUCCESS` e `HARNESS_ERROR` deixam de colapsar no mesmo sinal.
- Eventos possuem identidade modelo + versão + provider, store append-only bloqueado contra symlink e corrupção, intervalo de Wilson, suficiência de identidade/tarefa/amostra, importador Aletheia read-only e calibração determinística por veredito exato.
- Evidência independente: Ruff e formato `PASS`; suíte `54 passed`; contrato público Nike real aceita somente projeções `valid=true`. Sem commit ou push.

## Checkpoint ARGOS-1 — Browser QA observacional (2026-08-30)

- ARGOS-1 está `CONCLUÍDA` como projeto separado em `Athena/Argos`, sem Git inicializado e sem integração no runtime Athena.
- Brave/Edge Chromium roda com perfil temporário, CDP em loopback, interceptação fail-closed de toda requisição, downloads negados, popups fechados e nenhuma ação de clique/digitação/autenticação.
- O relatório mede HTTP, título, console/page errors e screenshot full-page PNG com containment, limite, SHA-256 e veredito `PASS/FAIL/BLOCKED`; URLs de relatório removem userinfo, query e fragment.
- Evidência independente: Ruff/formato `PASS`; suíte `28 passed`, incluindo página saudável, console error, 404, redirect externo, subrecurso externo e timeout real; P0 Athena final `PASS`; nenhum processo/perfil Argos órfão. Sem commit, push, extensão publicada ou beta.
- Limite declarado: extensão distribuída, abas existentes, ações de usuário e análise multimodal permanecem futuras e exigem permissões/aprovação próprias.

## EXTERNAL/HUMAN

- M-19 já foi decidida pelo CEO em ROUTE-0; M-20 a M-30 continuam dependentes de decisão do CEO.
- Estado do `Athena-beta`, clientes registrados, outros repositórios e serviços externos não foi promovido nem revalidado nesta fatia.
- Commit, push, promoção do beta, release, deploy, trabalho com credenciais, ações destrutivas e infraestrutura paga não foram autorizados.
- Os dois arquivos não rastreados permanecem sob decisão do usuário: foram apenas identificados e preservados, sem staging, teste ou integração.

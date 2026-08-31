# Gerência Técnica — Athena-MCP

## SNAPSHOT V1 TERMINAL (2026-08-31)

- **Runtime baseline de evidência:** `5319763`; HEAD/origin/main corrente deve ser consultado diretamente via Git.
- **Runtime baseline:** `5319763` — P0 `PASS`; suíte protegida `715 passed, 3 deselected`; sete tools MCP; beta equivalente ao runtime e smoke-verified.
- **ADR-0001:** escopo v1 terminal — Content Gate `OPTIONAL_FUTURE`; Metis `DEFERRED_BY_ADR`; IAProxy zchat/kimi `OPTIONAL_NOT_CONFIGURED`; aceite externo `EXTERNAL_ACCEPTANCE_PENDING`; SSH `INTENTIONALLY_CLOSED`; Olimpo O-2..O-5 `OPTIONAL_FUTURE`.
- **OLIMPO-0:** biblioteca HTTP opt-in em loopback para observação e publicação de configuração validada por preview + CAS; não executa, cancela nem autoriza tarefas.
- **WIP de engenharia:** vazio após o fechamento v1. Itens opcionais acima são estados terminais, não bloqueios ativos.
- **Arquivos protegidos:** preservados e verificados somente por SHA-256.
> **Histórico abaixo:** hashes, filas e bloqueios anteriores permanecem como checkpoints datados; não substituem este snapshot terminal.




## Checkpoint CAP-0 — COMPLETED (2026-08-28)

- Contrato criado em `contexto/CAP-0-CONTRATO.md` antes do código.
- Entregues Cápsula, Selo Aegis, Iris local e ambiente mínimo no caminho MCP real, sem mudar as sete tools.
- Após correção de fechamento de tipos e validação gerencial independente: Aegis `195 passed`; Athena focado `109 passed`; suíte não-regressão `457 passed, 3 deselected`; Ruff, diff-check e oito contratos de importação passaram; benchmark 30/3 teve p95 bridge `1.202625 ms` e MCP incremental `1.862125 ms`, ambos sob 5 ms. Naquele checkpoint, ROUTE-0 tornou-se a próxima elegível; seu fechamento posterior está registrado abaixo.

## Checkpoint ROUTE-0 — COMPLETED (2026-08-29)

- Contrato criado em `contexto/ROUTE-0-CONTRATO.md` antes do código.
- Decisão M-19 fechada: Zeus/Nike são determinísticos no runtime; nenhuma LLM participa do caminho quente.
- `run_combo` é autônomo com receitas advisory; `ask_provider` é a superfície direta limitada. Configuração/registro ausentes, inválidos ou adulterados abstêm antes do runner.
- Aceitação independente final: diff-check/Ruff/P0 `PASS`; testes focados finais `159 passed`; suíte integral `475 passed, 3 deselected`; caminho JSON-RPC real e casos adversariais `PASS`; sete tools preservadas; benchmark ROUTE 30/3 p95 `0.525458 ms` sob 5 ms; hashes protegidos inalterados. Nenhum commit, push ou beta.
- FLOW-1 é a próxima fatia elegível e permanece não iniciada até contrato próprio.

## Checkpoint FLOW-1 — COMPLETED (2026-08-29)

- Contrato criado em `contexto/FLOW-1-CONTRATO.md` antes do código.
- Escopo: ligar `submit_task` a uma execução opcionalmente identificada por `task_handle`, atravessar ROUTE-0/CAP-0, verifier, Evidence Gate e Chronos, e persistir projeção terminal sanitizada consultável por `get_task`.
- Sete tools permanecem; sem retry automático, multiagente, Clio/Olimpo, LLM ou recuperação de crash em `running`. Aletheia, Themis, Argos e os dois arquivos protegidos permanecem fora do escopo.
- Aceitação independente: E2E real `submit_task -> run_combo -> get_task` em três processos; focados `178 passed`; Ruff/diff-check `PASS`; suíte protegida-aware `527 passed, 3 deselected`; sete tools preservadas; benchmark 30/3 com p95 bridge `1.031333 ms` e MCP incremental `1.322376 ms`, guardrail `PASS`, 33 execuções terminais e cleanup não forçado; hashes protegidos inalterados.
- CLIO-0 é a próxima fatia elegível e permanece não iniciada até contrato próprio. Nenhum commit, push ou beta foi realizado.

## Checkpoint CLIO-0 — COMPLETED (2026-08-29)

- Contrato criado em `contexto/CLIO-0-CONTRATO.md` antes do código.
- Escopo: quatro níveis, evento fechado e sanitizado, produtor não bloqueante,
  fila limitada, writer local em lotes, retenção e benchmark p95 <= 1 ms.
- `complete` sem protetor aprovado falha fechado; `none` não cria evento nem
  storage comum. Sete tools e payloads MCP permanecem.
- Aceitação gerencial após três correções: 64 testes Clio; 140 focados Clio+FLOW/MCP; Ruff/diff-check/nove fronteiras/P0 `PASS`; suíte `591 passed, 3 deselected`; sete tools; smoke JSON-RPC technical/none; benchmark 30/3 com enqueue p95 `0.004 ms` e none p95 `0.000208 ms`; hashes protegidos inalterados.
- MULTI-0 é a próxima elegível e permanece não iniciada até contrato próprio. Nenhum commit, push ou beta.

## Checkpoint MULTI-0 — COMPLETED (2026-08-29)

- Contrato criado em `contexto/MULTI-0-CONTRATO.md` antes do código.
- Escopo: Harmonia determinística, DAG de subtarefas, backpressure, tokens de
  recursos, leases granulares atômicos/FIFO com heartbeat/TTL e estratégia
  híbrida de worktree apenas em repositório sintético temporário.
- Sete tools/schemas MCP permanecem; Olimpo/MCP-2026 não foram iniciados.
- Aceitação gerencial independente: diff-check/Ruff/P0 `PASS`; `45 passed` focados; suíte protegida-aware `636 passed, 3 deselected`; benchmark Harmonia 30/3 com p95 de planejamento `0.025708 ms` e reserva `0.02575 ms`; hashes protegidos inalterados. A primeira passagem global mostrou interferência transitória; os casos passaram isoladamente e a repetição integral fechou verde.
- Limite explícito: o núcleo coordena, autoriza e verifica write-set, mas não substitui sandbox de sistema operacional. `OLIMPO-0` é a próxima elegível e permanece não iniciada até contrato próprio. Nenhum commit, push ou beta.

## Checkpoint OLIMPO-0 — COMPLETED (2026-08-29)

- Contrato criado em `contexto/OLIMPO-0-CONTRATO.md` antes do código.
- Sequência WIP=1: O-0 adapter HTTP local/schemas; O-1 frontend isolado; O-2 E2E, visual e desempenho.
- M-20 a M-30, instalação de modelos/CLIs/proxies, autoridade operacional, rede externa e nova tool MCP permanecem fora do escopo.
- O-0 aceito após correção de limites HTTP, headers duplicados, query fechada e boundary de exceções: Ruff/diff-check `PASS`, `24 passed`, hashes protegidos inalterados. Ressalva para O-2: métodos HTTP não suportados do handler real devem responder JSON sanitizado, não HTML padrão. O-1 é o único WIP.
- Fechamento: O-2 eliminou HTML de erro, corrigiu CSP com nonce, CSRF, symlinks, origens e acoplamento SQLite privado. Evidência: `40 passed` backend/HTTP; frontend `16 passed`, typecheck/build; P0 `PASS`; suíte protegida-aware `676 passed, 3 deselected`; benchmark 30/3 p95 `0.506084 ms`; navegador real desktop e 390x844 sem overlay, console errors ou overflow global; hashes protegidos inalterados.
- Limites: Olimpo é opt-in/loopback, não sandbox nem autoridade operacional; M-20 a M-30 permanecem pendentes. MCP-2026 era a próxima elegível e foi aberta posteriormente. Nenhum commit, push ou beta.

## Checkpoint MCP-2026 — COMPLETED (2026-08-29)

- Contrato criado em `contexto/MCP-2026-CONTRATO.md` antes do código.
- Escopo: adapter dual-era stdio para `2026-07-28` com prevalidação antes de reserva, `server/discover` gated, validação `_meta` JSON genérica limitada, envelopes modernos completos, cancelamento in-flight-only, identidade `0.2.0` e legado `initialize`/`2024-11-05` preservado.
- Sete tools/schemas inalterados; sem `structuredContent`/`outputSchema`; sem HTTP/SSE/OAuth/subscriptions/elicitation/sampling/Tasks.
- Histórico NO-GO: primeira tentativa (`10 passed`, suíte `686 passed`, p95 `1.067 ms`) falhou revisão por reserva antes de erro de versão; segundo NO-GO por envelope/`clientInfo`/race/linha limitada.
- Aceitação final após quarto passe e revisão independente: diff-check/Ruff/P0 `PASS`; focados MCP `44 passed`; probes stdio reais `PASS`; suíte protegida-aware `710 passed, 3 deselected`; benchmark 30/3 p95 bridge `0.475251 ms` e MCP incremental `0.961834 ms`, guardrail `PASS`, cleanup não forçado; hashes protegidos inalterados e nenhum processo órfão. Nenhum commit, push ou beta.
- A fila finish-to-start BASE-0..MCP-2026 está esgotada; novas fatias exigem contrato próprio e decisão do CEO para M-20 a M-30.

## Identidade do produto
- Objetivo: núcleo de execução governada do ecossistema Athena. Despacha tarefas para CLIs de agentes com máquina de estados, deadlines, fallback controlado e verificação determinística mais advisory do resultado.

## Autorização CEO — Aletheia, Themis e Argos (2026-08-30)

- O CEO revogou explicitamente a proteção anterior e autorizou finalizar os três projetos.
- WIP=1 permanece: `INT-ALE-0` foi reaberta primeiro sob `INT-ALE-0-CONTRATO.md`; Themis e Argos não podem ser editados simultaneamente.

### Fechamento da autorização (2026-08-30)

- `INT-ALE-0` concluída: adapter estruturado, modelo/timeout reais, configuração fail-closed; Aletheia `97 passed` e integração `24 passed`.
- `THEMIS-1` concluída no repositório separado: score v0.2 com eixos distintos, incerteza, eventos append-only e contrato Nike; `54 passed`.
- `ARGOS-1` concluída como projeto separado: Chromium observacional, interceptação de escopo, evidência PNG e limites de prazo/recursos; `28 passed`.
- Gate final do Athena-MCP: lint, boundaries e P0 `PASS`; hashes protegidos inalterados. Nenhum commit, push, promoção de beta, release ou deploy foi realizado.
- WIP=1 está vazio. Integração Themis/Argos na surface MCP, extensão distribuída e análise multimodal não foram aprovadas implicitamente.
- A autorização permite implementação e testes, mas não autoriza commit, push, beta, release, deploy, credenciais ou ações destrutivas.
- Escopo atual: dez pacotes modulares com fronteiras de importação verificadas por máquina: `execution/`, `registry/`, `lease/`, `profiles/`, `transport/`, `bridge/`, `router/`, `verifier/`, `mcp_server/` e `zeus/`.
- **Zeus** (2026-08-24, `9482791`): módulo de recomendação de especialista (agente/persona/modelo). Determinístico por (entrada + versão do registro); abstenção fail-closed com reason codes; Themis só desempata com evidência suficiente; **nunca executa** — Aegis autoriza, Athena orquestra, Moiras observa. Gate adversarial: 19 testes em `tests/test_zeus.py`.
- Aegis é a fonte da classificação de perfil de serviço e da política de fallback. O repositório privado é `JoaoPauloNA/aegis`; a distribuição é `athena-aegis`; o pacote de importação Python continua `aegis`. O commit da renomeação no Aegis é `4e3cc20`.
- Fora de escopo confirmado: `skip_permissions` não integra o núcleo novo. `RiskOutcome.REQUIRES_HUMAN_APPROVAL` permanece reservado no contrato do Aegis e não é emitido; não existe mecanismo de pausa/aprovação humana implementado.

## Estado comprovado
- Estado Git diretamente verificado em 2026-08-28: `main`/HEAD em `d69ba39a242fd5348a585dd94e83e83b5d1f489a`, `origin/main` em `fc58ed9926e2f7bf37601691f40ded5267ff6ac9`; a branch local está seis commits à frente.
- Antes das mudanças documentais de BASE-0, o worktree continha somente dois arquivos não rastreados, `athena/api_mode.py` e `tests/test_api_mode.py`. Na entrega de BASE-0, somaram-se os quatro novos arquivos de contexto autorizados. Esse registro histórico permanece válido; posteriormente, os bytes validados de PERF-0 acrescentaram `harness/benchmark_orchestration.py`, `tests/test_benchmark_orchestration.py` e a alteração de `harness/README.md`. Os dois arquivos experimentais/do usuário permanecem preservados, não são implementação aceita e não foram alterados, adicionados ou testados por BASE-0.
- A versão diretamente verificada em `pyproject.toml` é `0.2.0`. Após TASK-0, a superfície MCP possui sete tools: as cinco originais e `submit_task`/`get_task`.
- Registro histórico: em 2026-08-22, `origin/main` e `main` local estavam em `9ab50eb`, incluindo Fix A e Fix C, e o `Athena-beta` foi reportado como repromovido. Esse registro não descreve o HEAD atual nem comprova o estado atual do beta.
- Última atualização: 2026-08-28.
- A CI foi validada em `d3e191a028f0de751994af2bf4c28566f7ff7837` e o handoff documental da fatia anterior foi publicado em `62fb03b` em `origin/main`. O remoto monolítico antigo foi reconciliado por force-push autorizado.
- Preservar as branches locais antigas `athena-release-20260814` e `fix/p0-audit-20260815`; não as excluir sem decisão explícita.
- A fatia de CI está **FECHADA**. O GitHub Actions run `32435500126` passou em Ubuntu e macOS, com Python 3.11 e 3.12.
- A P1 de transporte stdio/JSON-RPC foi publicada em `origin/main` nos commits `76e45ff` e `8336583`. A decisão da fatia seguinte (`transport/` sem consumidor) foi registrada em `4e13975`.

## Checkpoint BASE-0 — contexto operacional (2026-08-28)

- Criada a base mínima `contexto/INDEX.md`, `contexto/ESTADO_ATUAL.md`, `contexto/ROADMAP.md` e `contexto/CLAUDE_ORQUESTRADOR.md`; este arquivo permanece como handoff técnico extenso e histórico.
- BASE-0 foi concluída após validação estrutural. A fila finish-to-start vigente é BASE-0, PERF-0, PERF-1, INT-ALE-0, CFG-SEC-0, TASK-0, CAP-0, ROUTE-0, FLOW-1, CLIO-0, MULTI-0, OLIMPO-0 e MCP-2026. Nenhuma fatia posterior foi iniciada por este checkpoint.
- Conflito preservado: há código rastreado anterior relacionado a configuração e Zeus/Nike, enquanto a fila de 2026-08-28 ainda exige fatias próprias CFG-SEC-0 e ROUTE-0. A presença do código não satisfaz nem abre esses gates.
- Registro histórico de BASE-0: M-20 a M-30 aguardavam decisão e M-19 não podia ser aprovada implicitamente. M-19 recebeu depois aceite explícito do CEO e foi fechada em ROUTE-0; M-20 a M-30 continuam pendentes.
- Commit, push, promoção do beta, release, deploy, trabalho com credenciais, ações destrutivas e infraestrutura paga não foram autorizados nesta fatia.
- Próxima fatia exata após a validação de BASE-0: `PERF-0 — benchmark e SLO`.

## Checkpoint PERF-0 — benchmark e SLO (2026-08-28)

- PERF-0 foi concluída após validação gerencial independente executada fora do sandbox do Codex2, restrito por socket, contra os bytes atuais.
- Evidência exata: `git diff --check: PASS`; `Ruff on PERF-0 files: PASS`; `focused pytest: 20 passed`; `real smoke: 10 samples, 2 warmups, guardrail PASS`; `bridge-over-direct p95: 10.900625 ms (30 ms characterization ceiling)`; `incremental MCP-over-bridge p95: 0.853251 ms (5 ms ceiling)`; `MCP cleanup: exit 0, not forced, not alive; no remaining python -m athena process`; `harness/p0_gate.py: lint PASS, boundaries PASS, p0 PASS`; `full non-regression suite: 327 passed, 3 deselected`.
- Os p95 e os tetos de 30 ms e 5 ms são caracterização desta máquina e guardrails de regressão. Eles não constituem nem aprovam o SLO futuro proposto de 5 ms para o bridge.
- PERF-1 é a próxima fatia elegível na ordem finish-to-start e permanece `NÃO INICIADA`. WIP=1 continua vigente; o fechamento de PERF-0 não abre automaticamente PERF-1 nem qualquer linha posterior.
- Este fechamento documental não altera decisões, propostas, fronteiras de proteção, história, arquivos do usuário ou fatos de BASE-0; também não autoriza commit, push, tag, release, beta, remotos, Vault ou outros repositórios.

## Checkpoint PERF-1 — supervisor orientado a eventos (2026-08-28)

- PERF-1 está `CONCLUÍDA` após validação gerencial independente fora do sandbox contra os bytes atuais. Evidência exata: `git diff --check: PASS`; Ruff dos Python alterados de PERF-1 `PASS`; testes focados do bridge `14 passed`; `harness/p0_gate.py` com lint, boundaries e p0 `PASS`; suíte integral `331 passed, 3 deselected`; benchmark real com `30 samples`, `3 warmups` e guardrail `PASS`; `bridge-over-direct p95: 0.320792 ms` sob teto de 5 ms; `incremental MCP-over-bridge p95: 0.5685 ms` sob teto de 5 ms; cleanup MCP com `exit 0`, `not forced`, `not alive` e `33 terminal runs`; nenhum filho de teste PERF-1 restante; nenhum novo processo `python -m athena` restante. Processos Athena-beta MCP long-running do desktop eram preexistentes e não foram tocados.
- O bridge substituiu o ciclo de `poll()`/sleep e os snapshots repetidos de `ps` no caminho quente por espera de conclusão orientada a evento, sinais monotônicos limitados e inspeções de descendentes atrasadas, esparsas e forçadas em eventos relevantes de teardown. O caminho rápido validado não chama repetidamente a tabela de processos.
- Um sentinel POSIX interno herdável cobre a corrida em que um descendente ordinário chama `setsid()`, fecha stdout/stderr e sobrevive à saída rápida do pai: quando descendentes ordinários preservam o FD interno herdado, a execução fecha conservadoramente como `termination_unconfirmed`. A fronteira é exata e limitada: não se afirma detectar processo que, deliberadamente ou por política própria, feche todos os FDs herdados desconhecidos.
- Timeout e cancelamento preservam saída/estado parcial e encerram a árvore possuída nos casos validados; PTY, pipes, lease e lifecycle permanecem cobertos. Não foram adicionadas filas, timers ou buffers sem limite; limites de buffer continuam em fatia posterior.
- O fechamento de PERF-1 tornou `INT-ALE-0 — Aletheia` elegível sem implementá-la. Sua classificação atual é `TERMINAL BLOCKED BY CEO PROTECTION`; não é falha nem implementação. Essa saída terminal permite que a linha independente do Athena-MCP avance sob WIP=1: `CFG-SEC-0 — snapshot` é a próxima elegível e permanece `NÃO INICIADA`. Nenhuma outra proposta M-20 a M-30 foi aprovada silenciosamente.

## Classificação terminal INT-ALE-0 — Aletheia (2026-08-28)

- Evidência fresca obtida somente por leitura: HEAD e upstream do repositório Aletheia estão ambos em `c7d2c6115887f91c4560897cc03a7681adc24f3f`; o worktree está limpo.
- `athena_compat.py` agora retorna um objeto de resultado com `stdout` e `stderr`. A formulação anterior de incompatibilidade por retorno em string crua está obsoleta e não deve ser repetida.
- O mesmo `athena_compat.py`, contudo, descarta explicitamente as entradas de modelo e timeout e aplica timeout fixo ao subprocesso. Portanto, o gate declarado — contrato público real corrigido e testes cross-repo sem mocks de forma — não está integralmente satisfeito; não se declara integração completa.
- A proteção explícita do CEO contra modificação de Aletheia permanece vigente. Leitura e testes são permitidos; edição não. `INT-ALE-0` fica `TERMINAL BLOCKED BY CEO PROTECTION`, sem classificação de falha e sem implementação.
- Pré-requisito de retomada: revogação explícita da proteção pelo CEO ou autorização estreita e explícita para editar Aletheia.
- WIP=1 permanece vigente. A saída terminal bloqueada permite prosseguir apenas na linha independente do Athena-MCP; `CFG-SEC-0 — snapshot` é a próxima fatia elegível e continua `NÃO INICIADA`.

## Checkpoint CFG-SEC-0 — snapshot seguro (2026-08-28)

- CFG-SEC-0 está `CONCLUÍDA` após validação gerencial independente executada contra os bytes atuais do worktree (implementação já presente, não commitada, em `athena/config_loader.py`, `athena/bridge/runner.py`, `tests/test_config_loader.py`, `tests/test_bridge.py`, `tests/test_cfg0a_states.py`, `harness/README.md`).
- Reparo de ambiente prévio à validação: o interpretador `.venv/bin/python3.12` era um symlink pendente para um app desinstalado; o symlink foi reapontado para `/opt/homebrew/bin/python3.12` preservando `site-packages` (inclui `aegis` editable, `pytest`, `ruff`) intactos. Ação local, reversível, sem efeito em dados do usuário ou em Git.
- Evidência exata do fechamento: `git diff --check: PASS`; `ruff check` nos arquivos alterados de CFG-SEC-0: `PASS`; `pytest tests/test_config_loader.py`: `47 passed`; `pytest tests/test_config_loader.py tests/test_cfg0a_states.py tests/test_bridge.py`: `77 passed`; `harness/p0_gate.py`: `lint PASS`, `boundaries PASS`, `p0 PASS`; suíte integral `pytest tests -m "not regression"`: `369 passed, 3 deselected`; `pytest tests/test_benchmark_orchestration.py`: `20 passed`; benchmark real (`harness/benchmark_orchestration.py --samples 30 --warmups 3 --guardrail --bridge-ceiling-ms 5 --mcp-incremental-ceiling-ms 5`): guardrail `passed`, `bridge_over_direct p95: 0.441751 ms`, `incremental_mcp_over_bridge p95: 0.442125 ms`, ambos sob teto de 5 ms; cleanup MCP com `mcp_exit_code: 0`, `mcp_process_alive: false`, `terminal_runs_validated: 33`, `forced: false`.
- Verificação pós-validação: nenhum processo órfão de teste (só os `athena-mcp` do `Athena-beta` long-running do desktop, preexistentes); nenhum arquivo temporário `.tmp`/`.snapshot.json.*` residual; hashes de `athena/api_mode.py` (`db8885c0…8b6d796`) e `tests/test_api_mode.py` (`da123ab4…d98150`) inalterados antes e depois da validação; `git status` mostra apenas o escopo já preexistente (config_loader, bridge/runner, testes correspondentes, docs de harness/contexto) sem expansão de escopo.
- Conteúdo do reforço de segurança confirmado por leitura do diff: schemas estritos de provider/função com allowlist de campos; rejeição de qualquer valor de segredo embutido (chave, senha, token, `Authorization`, credenciais em `base_url`, incluindo userinfo e query string), exigindo `secret_ref` no formato `scheme:item`; parsing JSON com limite de profundidade/itens/bytes, UTF-8 estrito e rejeição de chave duplicada; leitura de partes ancorada por `dir_fd`/`O_NOFOLLOW` com bloqueio de symlink e de arquivo não regular; publicação atômica do `snapshot.json` por arquivo temporário exclusivo (`O_EXCL`, `0o600`), fsync do arquivo e do diretório, e `os.replace` dentro do mesmo `dir_fd`, com validação do candidato pelos bytes exatos antes da troca e preservação do snapshot anterior válido em caso de falha; `ConfigSnapshotCache` isola o objeto retornado por congelamento profundo (`MappingProxyType`/tupla) e descongelamento por cópia, prevenindo mutação cruzada entre chamadas.
- Limite exato registrado: `write_snapshot` ancora toda a operação no descritor de diretório após `os.open(config_dir)`; a janela portátil residual é a substituição de um diretório ancestral imediatamente antes dessa abertura — nenhuma consulta de caminho é confiada depois desse ponto. Não há transação POSIX que abranja arquivos de partes independentes publicados por um escritor cooperante externo antes da chamada.
- Este checkpoint não abre TASK-0 nem qualquer fatia posterior; apenas fecha CFG-SEC-0 na fila finish-to-start. `TASK-0 — contrato e durabilidade` torna-se a próxima fatia elegível sob WIP=1.

## Fechamento da fatia de CI

| Commit | Evidência entregue |
|---|---|
| `2383257` | Gate P0 atual e matriz Python 3.11/3.12 |
| `566acc2` | Job obsoleto do Moiras removido e pendência registrada no backlog |
| `873dbc1` | Proteção contra uso do pacote público `aegis` |
| `c55bc7c` | Dependência de runtime declarada como `athena-aegis` |
| `8482706` | Checkout privado do Aegis no CI |
| `146c777` | Diagnóstico limitado das falhas do gate P0 |
| `d3e191a` | Matriz restrita a sistemas POSIX |

- O CI usa a deploy key somente leitura intitulada `athena-mcp-ci-read-only`: a chave pública está cadastrada no Aegis como deploy key somente leitura; a chave privada existe apenas no secret do GitHub Actions do Athena chamado `AEGIS_DEPLOY_KEY`. Nenhum material de chave deve ser registrado neste documento.
- O checkout privado usa SSH com `persist-credentials: false`.
- O CI instala o Aegis privado em modo editable a partir de `.ci/aegis`, instala o Athena com `--no-deps` e instala explicitamente `import-linter`, `pytest` e `ruff`.
- Fragilidade conhecida: cada nova dependência de runtime do Athena exigirá uma etapa explícita de instalação no CI. Não corrigir nesta fatia encerrada.
- Windows está explicitamente sem suporte por enquanto e foi removido da matriz. O bridge não garante encerramento da árvore de processos no Windows, produzindo `TERMINATION_UNCONFIRMED` onde os testes esperavam `TIMED_OUT`; além disso, testes do import-linter invocavam um executável indisponível naquele sistema. O porte para Windows é uma fatia futura separada.
- O gate agora emite diagnóstico de falha limitado, evitando saída sem limite.

## Fronteiras e decisões vigentes
- O import-linter aplica o bulkhead entre módulos por contratos `layers` e `forbidden`. Módulos do núcleo não podem importar `aegis` diretamente fora de `profiles`; a integração usa a fachada pública `aegis.decision.evaluate`.
- `AUTHENTICATED_EXTERNAL` e `UNKNOWN` nunca autorizam fallback automático.
- A reconstrução modular substituiu o núcleo acoplado; o legado permanece apenas como referência de comportamento e protocolo, não como fonte para cópia direta.
- O fechamento de cada fatia deve apresentar evidência verificável antes de liberar a seguinte. Não abrir trabalho paralelo fora do sequenciamento finish-to-start sem autorização explícita.

## Fechamento da P1 — transporte stdio/JSON-RPC

| Commit | Evidência entregue |
|---|---|
| `76e45ff` | Transporte stdio/JSON-RPC modular, contrato explícito, runtime fora de `athena.mcp_server`, entrypoint `athena-mcp` e testes de protocolo |

- A implementação seguiu o princípio “contrato antes do código”: o novo pacote `athena/mcp_stdio/` introduz `PreparedToolCall`, `StdioTransport` e `MCPApplicationContract` antes da montagem concreta.
- A composição concreta do runtime foi colocada em `athena/mcp_runtime.py`, fora do pacote fechado `athena.mcp_server`, para respeitar o import-linter. O pacote `mcp_server` continua sem importar `bridge`, `lease` ou `transport`.
- A superfície pública do transporte expõe exatamente cinco tools modulares: `run_combo`, `ask_provider`, `get_execution`, `list_executions` e `cancel_execution`. Nenhuma tool exclusiva do legado foi reintroduzida.
- Chamadas longas (`run_combo` e `ask_provider`) agora registram a execução antes do despacho para manter `get_execution` e `cancel_execution` responsivos durante o trabalho em background.
- O encerramento por EOF abandona execuções não finalizadas com `client_abandoned`; falhas internas do worker retornam erro MCP genérico e finalizam a execução como `failed` sem vazar detalhe interno no stderr.
- Evidência local da P1 em 2026-08-21:
  - `.venv/bin/python -m pytest tests/test_mcp_server.py tests/test_mcp_stdio.py -q` → `15 passed`
  - `.venv/bin/python -m ruff check athena/mcp_server athena/mcp_stdio athena/mcp_runtime.py athena/__main__.py tests/test_mcp_server.py tests/test_mcp_stdio.py` → `All checks passed`
  - `.venv/bin/python harness/p0_gate.py` → `lint: PASS`, `boundaries: PASS`, `p0: PASS`

## Próxima fatia recomendada

## Fechamento da investigação — `transport/` sem consumidor

- Veredito: **manter por enquanto; não é lacuna real do fluxo atual**. O pacote `athena.transport` está prematuro em relação aos contratos públicos atuais, mas encapsula uma fronteira de SSH remota já reconstruída e testada.
- Evidência do veredito:
  - `athena/mcp_runtime.py` monta o runtime exclusivamente com `LocalBridgeRunner`; não há composição com `RemoteExecutor` nem `SSHCommandBuilder`.
  - `athena/bridge/contracts.py` define `RunRequest` explicitamente como execução local (`cwd`, `env`, `use_pty`, lease e teardown local); não existe campo de transporte remoto, host, identidade SSH ou runner remoto.
  - `athena/router/contracts.py` e `athena/router/orchestration.py` operam apenas sobre `ComboAttempt.request: RunRequest`; portanto o router atual não tem como selecionar SSH.
  - `athena/mcp_server/contracts.py` e `athena/mcp_stdio/application.py` expõem `run_combo`/`ask_provider` sem qualquer parâmetro remoto equivalente ao `ssh_host` do legado.
  - `git grep` no código modular encontrou `RemoteExecutor`, `SSHCommandBuilder`, `build_ssh_command` e `execute_remote` apenas dentro de `athena.transport` e `tests/test_transport.py`; não há consumidor de produção fora do próprio pacote.
  - No legado, a execução remota existia dentro de `providers.ask_provider(..., ssh_host=...)`, isto é, em um contrato público diferente do modular atual.
- Implicação arquitetural: usar SSH no núcleo novo exigiria uma fatia própria de contrato, não só “ligar o fio”. Seria necessário decidir como o transporte remoto entra em `RunRequest`/`ComboRequest`, no surface MCP (`ask_provider`/`run_combo`) e na composição do bridge, mantendo as fronteiras do import-linter.
- Decisão desta fatia: **não implementar código adicional agora** e não remover o pacote nesta etapa. Remoção agora descartaria uma reconstrução já validada (`59f9e1b`) sem pressão funcional imediata; integração agora exigiria ampliação de escopo além da investigação autorizada.
- O backlog adiado de compartilhamento/documentação/MLX permanece fora de WIP nesta etapa.
- Manter WIP=1. A próxima fatia deve ser escolhida explicitamente após este fechamento.

## Pendências e riscos

| Item | Estado / impacto | Direção |
|---|---|---|
| Dependências futuras de runtime | `--no-deps` impede instalação transitiva no CI | Adicionar instalação explícita quando uma nova dependência for introduzida |
| Windows | Sem suporte; terminação de árvore de processos e execução do import-linter não atendem ao gate | **D-WIN decidida 2026-08-24: macOS e Linux oficiais; meta tripla-SO rejeitada formalmente** |
| Branches históricas | Preservam referências anteriores à reconciliação | Não excluir `athena-release-20260814` nem `fix/p0-audit-20260815` |
| Fix B — coerção defensiva na fronteira MCP | **Fechada 2026-08-24: validação estrita mantida** (matriz sintética S-04 não detectou cliente real enviando strings) | Reabrir só se `harness/s04_synthetic_server.py` registrar `overall_timeout_s:string` em uso real |
| Mensagem por subtipo de falha | `FallbackBlocked` e `ComboDeadlineExceeded` hoje voltam com a mesma forma de `AllAttemptsFailed` | Só diferenciar se surgir necessidade comprovada; hoje seria complexidade sem demanda |
| Integração Moiras | **D-MO decidida 2026-08-24: integração shadow implementada** (`athena/observation/`, opt-in via `ATHENA_MOIRAS_SHADOW=1`; validação cross-repo no repo Moiras, `c0acf53`) | Manter opt-in; nunca conceder autoridade de execução/cancelamento/fallback ao observer |
| `Athena-beta` — histórico | Repromovido para `9ab50eb` (inclui Fix C) em 2026-08-22, fast-forward limpo a partir de `fb12d73`. Confirmado ao vivo via probe JSON-RPC direto no binário `Athena-beta/.venv/bin/athena-mcp`: `run_combo` com `sleep 5`/`overall_timeout_s=2` retornou `result` com `isError: true` e payload sanitizado (`state: timed_out`, `exit_code: -15`, `duration_s: 2.04`, `expired_deadline: absolute_deadline`) — não mais `-32000`. Nota: o cliente MCP deste chat mostra `isError:true` como "Error: tool failed" genérico sem expor o payload; a validação real foi feita lendo a resposta JSON-RPC crua, não pela UI do cliente | Nenhuma — só reabrir se `main` avançar de novo sem repromoção |

## Fechamento — Fix C (falha de combo como `isError`, não erro de protocolo)
- Sintoma relatado: `run_combo` com `overall_timeout_s=180` via Codex Desktop nunca devolveu stdout, exit_code nem estado sanitizado; sonda interrompida por circuit breaker manual.
- Reprodução com caso mínimo (7 variantes, `overall_timeout_s=5`): `sleep 300` em pipe e em PTY, com neto vivo, com stdout parcial, e com filho/neto que ignoram SIGTERM. **Nenhuma travou** — resposta em 5,0 s (5,5–5,6 s quando houve SIGKILL após a graça), filho morto de verdade, zero órfãos. O mecanismo de deadline e a propagação `overall_timeout_s → remaining → _effective_deadlines → bridge` estavam corretos.
- Causa raiz: o núcleo produzia o resultado certo (`state=TIMED_OUT`, `exit_code=-15`, stdout parcial, `duration_s`, `expired_deadline=ABSOLUTE`) e o registro ficava `finalized: true`, mas `MCPApplication.call` deixava `AllAttemptsFailed` subir e o servidor convertia em `{"error":{"code":-32000}}`, descartando tudo. Do lado do cliente, indistinguível de "não retornou nada". Lacuna de contrato introduzida pela P1 — no canal de resposta, não no deadline.
- Correção (commit `2284b5d`): um único `except AllAttemptsFailed` na fronteira; `FallbackBlocked` e `ComboDeadlineExceeded` herdam dela e são cobertos por construção — sem tratamento por subtipo. Resposta passa a ser resultado de tool com `isError: true` e payload sanitizado (`execution_id`, `state`, `exit_code`, `stdout`, `stderr`, `duration_s`, `expired_deadline`, `error`), na mesma forma do caminho de sucesso. `last_result is None` é tolerado: campos nulos e `error` com a mensagem da exceção. Erros de protocolo (`-32602`, `-32700`) inalterados.
- Regressão: `tests/test_mcp_stdio.py` — processo real morto de verdade (`sh -c 'echo parcial; sleep 300'`, timeout 3 s), retorno dentro do prazo, `state: timed_out`, exit code negativo, stdout parcial, duração positiva, deadline absoluto, ausência dos PIDs de shell e filho.
- **Validação com o caso original (cx2, não só sintético):** `run_combo` executando `cx2 exec` com `overall_timeout_s=30` retornou em 30,0 s com `state: timed_out`, `exit_code: -15`, stderr parcial preservado (banner do Codex e o prompt), zero processos `codex`/`cx2` órfãos. O sintoma relatado está resolvido para o caso real, não apenas para o sonda.
- Achado colateral (não é bug do Athena): o `cx2` imprime "Reading additional input from stdin..." — ele lê stdin além do prompt posicional. Com `stdin=DEVNULL` do bridge recebe EOF e segue; em host que deixe stdin aberto, isso pode contribuir para a impressão de travamento.

## Fechamento — Fix A (schema das tools MCP)
- Sintoma: `run_combo` com `overall_timeout_s` explícito falhava com `overall_timeout_s must be a positive finite number or None`, mesmo com valor válido.
- Causa raiz: o schema anunciava união de tipos (`["number","null"]`, `["string","null"]`). Hosts MCP descartam o `type` quando é união — comprovado: campos de tipo simples (`attempts`, `execution_id`, `verification`) sobrevivem, os de união chegam como `{}`. Sem tipo, o cliente serializa como string e a validação estrita rejeita, corretamente.
- Reprodução determinística (dois checkouts, Athena-MCP e Athena-beta): número JSON passa a validação; string `"15"` reproduz o erro exato.
- Correção: `overall_timeout_s` → `{"type": "number", "exclusiveMinimum": 0}` e `profile` → `{"type": "string"}`, nos dois tools (`run_combo` e `ask_provider`). Opcionalidade segue expressa por ausência em `required`. Commit `8845353`.
- Regressão: `tests/test_mcp_stdio_schema.py` — roundtrip real de `run_combo` via stdio com timeout numérico + asserção de que `tools/list` declara `type` simples (impede a união de voltar).
- Não alterado: `athena/router/contracts.py` — a validação estava correta desde sempre.

## Fechamento — teste multi-provider controlado (2026-08-22)
- Fatiamento concluído sem alteração de código: `cx2`/Codex2 como tentativa primária e Claude CLI como fallback, através de `run_combo` no binário ativo `Athena-beta` em `9ab50eb`.
- O caso controlado usou diretório temporário, perfil `text_generation`, teto global de 90 s, primeiro executor `cx2 exec` em sandbox read-only com deadline absoluto de 0,5 s, e Claude em modo `plan`, sem escrita. O resultado JSON-RPC bruto foi sucesso de protocolo e retorno final `completed`, `exit_code: 0`, `stdout: ATHENA_CLAUDE_FALLBACK_OK` e duração do fallback de 3,65 s. O marcador exclusivo prova que o fallback real ocorreu após a tentativa do Codex2.
- A sonda sintética complementar (`sleep 4`, deadline de 3 s) retornou `timed_out`, `exit_code: -15`, `duration_s: 3,03` e `expired_deadline: absolute_deadline`; portanto o deadline do bridge no beta permanece efetivo.
- O cliente de teste deve manter stdin aberto até receber a resposta: fechá-lo logo após escrever JSON-RPC aciona corretamente `client_abandoned` e cancela o trabalho antes do dispatch.
- Ressalva operacional: o entrypoint editável do `Athena-beta`, iniciado com cwd do checkout `Athena-MCP`, importou o pacote do checkout principal por precedência de `sys.path`. A prova válida foi repetida com o processo iniciado em `Athena-beta`, confirmando import de `Athena-beta/athena/mcp_runtime.py`. Qualquer futura sonda do beta deve fixar esse cwd; isso não exige reconfigurar os servidores já registrados.
- Pós-condição: nenhum processo `cx2`, `codex` ou `claude` órfão da prova permaneceu. Só processos preexistentes de integração Chrome/desktop foram observados.

## Fechamento — auditoria e publicação (2026-08-22)
- A auditoria identificou `origin/main=eafd8df13590375378a07e80f2cbd372a9ec04bf` atrás do `main local=9ab50eb95276152089160abcabd7e10172e7a859`, com quatro commits pendentes: Fix A, Fix C e seus registros de contexto.
- Com autorização do João Paulo, os quatro commits foram publicados por fast-forward. Confirmação pós-push: `origin/main=9ab50eb95276152089160abcabd7e10172e7a859`.

## Handoff
- Estado-base para qualquer continuação: CI fechada e verde no run `32435500126`, P1 publicada em `origin/main` (`76e45ff`, `8336583`), investigação de `transport/` concluída como “manter, sem integração agora”, Fix A (`8845353`) e Fix C (`2284b5d`/`9ab50eb`) fechados, publicados e confirmados ao vivo via `Athena-beta`.

## Fechamento — onda v1 da auditoria integral (2026-08-24)

- **S-01** (`a3320d0`): handoff de 2026-08-22 commitado e publicado; worktree limpo.
- **S-03** (`42a12ca`): README.md + README as-built criados do zero (a raiz não os possuía), CHANGELOG iniciado, versão **0.2.0** aplicada por SemVer em `pyproject.toml`. Gate P0 verde após a mudança.
- **S-04/S-05 — Fix B fechado como validação estrita mantida**: servidor MCP sintético temporário (`harness/s04_synthetic_server.py`) com o mesmo schema, registrando apenas `typeof` dos argumentos (nunca valores). Matriz executada [AO VIVO 2026-08-24]: 4 chamadas com `overall_timeout_s` number/integer/string e `profile` string → contadores confirmam que strings são detectáveis e que nenhum cliente real registrado na máquina as envia espontaneamente (as ocorrências string na matriz foram injetadas de propósito como casos de teste). O validador estrito de `ComboRequest` permanece correto; coerção só entra se o contador registrar `overall_timeout_s:string` ou `profile:<não-string>` em uso real. Servidor sintético é temporário, sem registro permanente.
- **S-06** (`b9eaa97`, 2026-08-24): beta repromovido para a HEAD publicada (fast-forward `698ae47`→`b9eaa97`). Probes de smoke executados ao vivo no binário do beta com cwd do checkout: (1) `tools/list` declara exatamente 5 tools com tipos simples (`overall_timeout_s: {type: number, exclusiveMinimum: 0}`); (2) `run_combo` com `sleep 5`/`overall_timeout_s=2` retornou em ~2,03 s `isError:true` com payload sanitizado (`state: timed_out`, `exit_code: -15`, `expired_deadline: absolute_deadline`). **v1 candidata interna fechada.**
- **EG-1/EG-GATE** (`74bb51b`, 2026-08-25): motor Evidence Gate offline em `athena/evidence_gate/` — validação determinística de Result Envelopes (schema → cobertura critério↔check → evidência autorizada → consistência status×exit), veredito PASS/FAIL/INCONCLUSIVE/ESCALATE com precedência da política v0.1. Conjunto adversarial RESERVADO com zero falso PASS (13 testes).
- **EG-3A internal sink — correção local validada (`31bed1f`, 2026-08-27)**: o `mcp_server` recebe contratos tipados de finalizador e sink sem importar `evidence_gate` nem implementação de filesystem. Com `ATHENA_EG3A=1`, a composição só ativa a finalização quando `ATHENA_EG3A_SINK_DIR` também aponta para um diretório absoluto explícito; o sink grava JSON local por replace atômico, com nome estável e metadados sanitizados. `run_combo` e `ask_provider` não recebem campo `evidence_gate` com a feature ligada ou desligada. Falha ou configuração inválida do sink fecha a finalização, preserva o resultado já concluído e não expõe texto de exceção. Evidência local: 307 testes de não regressão verdes, 3 regressões lentas desmarcadas e gate P0 integral verde. EG-3B segue reservado; EG-4A continua advisory, incapaz de emitir ou converter resultado em `PASS`, e a entrega permanece `awaiting_human_review`. Fonte canônica: `Projetos/Carreira/Athena/Evidence-Gate-Integracao-EG3-EG4.md` no Vault.
- **D-SSH/D-WIN/D-HA** (2026-08-24, decisão do guardião): SSH permanece **dormente** (pacote fechado/testado, sem consumidor); **macOS e Linux são os sistemas oficiais** — meta tripla-SO rejeitada formalmente; `REQUIRES_HUMAN_APPROVAL` permanece **reservado** no Aegis até existir desenho de pausa/retomada. Revisar apenas se surgir demanda real.
- Os dois servidores MCP registrados na máquina do usuário (`athena-mcp-beta` no Claude Desktop, `athena` no Codex Desktop) apontam para o binário do `Athena-beta`.
- Não reabrir a fatia de CI para resolver fragilidades ou suporte a Windows; registrar e sequenciar esses trabalhos separadamente.
- Se algum fluxo futuro precisar SSH remota no núcleo novo, abrir uma fatia própria de contrato antes de qualquer implementação.
- O teste multi-provider foi fechado em 2026-08-22 com Codex2 como tentativa 1 e Claude como fallback confirmado. Não reexecutar por rotina; abrir nova fatia apenas se houver mudança de bridge, provider ou política de fallback.

### Checkpoint G4-0 — contrato gerencial (2026-08-27)

- A configuração só ativa quando `ATHENA_CONFIG_DIR` é definido explicitamente. Com a variável definida, snapshot ausente ou inválido impõe modo somente leitura/fail-closed, sem despacho; sem a variável, o caminho legado atual permanece temporariamente disponível por compatibilidade.
- A compatibilidade legada é exceção de migração limitada por gate, não arquitetura final. Ela só termina após gate separado comprovar configuração válida dos clientes existentes e rollback verificado.
- As cinco tools MCP — `run_combo`, `ask_provider`, `get_execution`, `list_executions` e `cancel_execution` — e seus schemas atuais permanecem inalterados.
- A primeira integração Gate 4 pode apenas autorizar, filtrar ou reordenar tentativas fornecidas pelo cliente. Execução `agent_cli` ou API integralmente gerada por configuração fica adiada até existir contrato interno de request de primeira classe para prompt, capacidades, domínio e risco; é proibido inferir prompt de arrays de comando.
- `CFG-1` a `CFG-4`, Zeus e Nike são primitivas/módulos, não integração de produção. SSH permanece dormente.
- A correção EG-3A explicitamente sequenciada foi implementada, validada e commitada localmente em `31bed1f`: sink interno opt-in, nenhum `payload["evidence_gate"]`, cinco tools e schemas preservados.
- Gate 4 permanece **NO-GO**. Esta correção não iniciou CFG/Gate4, não ativou SSH e não autoriza nova integração sem gate separado.
- Checkpoint documental G4-0 fechado em 2026-08-27: os documentos canônicos do Vault permanecem a fonte das restrições de configuração e roteamento; o fechamento local de EG-3A não altera esse contrato.

## Modelo operacional dos chats-gerentes — mente principal e braço executor (2026-08-26)

### Decisão de papéis

- O **Codex principal** e o **Claude principal** são os dois chats-gerentes do Athena. Eles mantêm estratégia, arquitetura, decisões, priorização, desenho de prompts, revisão crítica e aprovação técnica.
- Este arquivo é o ponto de sincronização entre os dois chats. Antes de orientar uma nova fatia, ambos devem reler `contexto/INDEX.md`, `contexto/ESTADO_ATUAL.md`, `contexto/ROADMAP.md`, `contexto/CLAUDE_ORQUESTRADOR.md` e este `gerencia_athena-mcp.md`.
- **Hermes Agent + `z-ai/glm-5.3-flash` via OpenRouter** passa a ser o braço executor preferencial para leitura extensa, documentação, alterações delimitadas, implementação, testes e trabalho repetitivo com alto consumo de tokens.
- Esse braço substitui **Codex2 e Claude2 como mão de obra padrão**, mas não substitui o Codex principal nem o Claude principal como gerentes e mentes de decisão.
- Esta é uma regra operacional dos gerentes; não significa que Hermes ou GLM já integrem o runtime do Athena-MCP.

### Evidência do piloto

- Piloto de auditoria somente leitura concluído em 2026-08-26 com o GLM 5.3 Flash em configuração padrão, sem ajuste explícito de thinking ou esforço.
- Consumo observado aproximado: 337 mil tokens de entrada, 10.994 tokens de saída e custo de cerca de **US$ 0,0114** para a tarefa; incluindo sondas curtas anteriores, o painel indicou **US$ 0,0144**.
- O relatório foi tecnicamente útil, mas limitado pelo contexto entregue: sua recomendação seguinte divergiu da decisão canônica já registrada para EG-3A/EG-4A. Portanto, baixo custo e bom volume não concedem autoridade arquitetural.
- **Auditoria somente leitura** e **escrita documental + Canvas sob revisão de gerente** estão aprovadas. O piloto documental exigiu uma correção semântica: o primeiro Canvas misturou comportamento implementado e planejado no mesmo nó e citou um commit inexistente; a revisão independente detectou o problema e a correção foi validada. Implementação com testes, recuperação limitada e continuidade em sessão longa ainda precisam de pilotos próprios antes de virarem rotas aprovadas.

### Contrato de despacho

1. O chat-gerente define uma única fatia com objetivo, arquivos permitidos, proibições, critério de saída e teto de custo.
2. O prompt do executor deve ser em inglês e mandar ler primeiro o contexto canônico relacionado à tarefa.
3. O Hermes/GLM executa somente a fatia autorizada, preserva alterações existentes e não amplia silenciosamente o escopo.
4. A execução deve retornar exatamente os 10 tópicos padronizados: feito, arquivos alterados, arquivos analisados, não alterado, testes, resultados, pendências, riscos, status `OK`/`FALHA` e próximo passo.
5. Um dos chats-gerentes confronta o relatório com Git, testes, artefatos e contexto antes de aceitar progresso ou emitir nova tarefa.
6. Divergência com o contexto canônico, relatório vazio, ausência de evidência ou três falhas pela mesma causa interrompem o fluxo e retornam a decisão ao gerente.

### Autoridade do braço executor

- **Pode, quando o prompt autorizar explicitamente:** ler arquivos, produzir documentação, editar somente o escopo nomeado, implementar código delimitado, executar testes proporcionais e realizar correções limitadas pela mesma causa.
- **Não pode decidir sozinho:** arquitetura, contrato público ou MCP, migração, exclusão de dados, credenciais e segurança, publicação, commit, push, release, deploy, custo recorrente, expansão de escopo ou mudança de prioridade/WIP.
- O executor nunca supera o Vault canônico, o código real, os testes ou a decisão dos chats-gerentes. Quando houver conflito, deve registrar `BLOCKED` com a fonte divergente.

### Orçamento e roteamento

- Saldo declarado para o experimento em 2026-08-26: **US$ 20 no OpenRouter** e **US$ 20 na API da OpenAI**. Esses valores são informação operacional declarada pelo usuário, não conciliação financeira automática.
- O limite inicial da chave de teste do OpenRouter é **US$ 1, sem renovação automática**. Nunca registrar a chave, token ou outro segredo neste arquivo.
- Meta de avaliação: verificar se o Hermes/GLM sustenta a mão de obra por aproximadamente **US$ 20–30/mês**. Isso ainda é hipótese de orçamento, não compromisso recorrente.
- Custo final observado da sessão documental, incluindo correção no mesmo contexto: **US$ 0,136**. A correção incremental custou aproximadamente **US$ 0,022** sobre os US$ 0,114 da primeira entrega.
- O teto escrito no prompt não é controle financeiro efetivo: o executor ultrapassou em 14% o limite indicativo de US$ 0,10 porque o custo acumulado não estava disponível ao modelo durante a execução. Limites futuros devem ser aplicados externamente no Hermes, OpenRouter ou middleware; o prompt apenas informa intenção.
- A API da OpenAI permanece como fallback ou verificador independente quando a dificuldade justificar; não deve ser consumida automaticamente só por estar disponível.
- Modelos de fronteira dos chats principais ficam reservados para decisões, arquitetura, prompts, impasses e revisão de alto valor.

### Próximo gate

- A rota de **documentação + Canvas** está aprovada com revisão posterior obrigatória por um chat-gerente e validações determinísticas de JSON, IDs, arestas, hashes e referências Git.
- Próximo piloto ainda não aprovado: **implementação delimitada + testes**, preservando WIP=1, sem commit/push e com revisão independente antes de aceitar o relatório.

### Checkpoint TASK-0 (2026-08-28)

- Contrato escrito em `contexto/TASK-0-CONTRATO.md`; pacote `athena/tasks/` (contrato + validação + `SQLiteTaskStore`) implementado e integrado a `athena/mcp_server/` (contracts.py, server.py, __init__.py), `athena/mcp_stdio/application.py` e `athena/mcp_runtime.py`. Sete tools expostas (`run_combo`, `ask_provider`, `get_execution`, `list_executions`, `cancel_execution`, `submit_task`, `get_task`).
- `tests/test_tasks.py` (29 testes, novo) cobre as 15 evidências de aceitação; `tests/test_mcp_server.py` e `tests/test_mcp_stdio.py` atualizados apenas nas asserções de nome/contagem de tools e na allowlist de imports do núcleo fechado.
- Exceção pontual autorizada aplicada em `tests/test_eg3a_production.py`: somente a lista canônica de `tools/list` passou de cinco para sete tools; a lógica do Evidence Gate não mudou.
- Correção adicional de contrato: `submit_task` agora anuncia schema explícito e fechado, incluindo o objeto `task`; `get_task` anuncia schema superior fechado. Os `maxLength` anunciados são hints em caracteres e o runtime permanece autoritativo em bytes UTF-8; a regressão estrutural cobre ambos os schemas e as sete tools.
- Revalidação: `git diff --check` e Ruff passaram; 72 testes focados, `harness/p0_gate.py` e a suíte não-regressão (`393 passed, 3 deselected`, com `tests/test_api_mode.py` excluído) passaram. Smoke JSON-RPC real em dois processos comprovou submit no processo A e get no processo B. Benchmark 30 amostras/3 warmups: p95 bridge `0.385166 ms`, MCP incremental `0.4995 ms`, ambos sob 5 ms.
- Hashes protegidos de `athena/api_mode.py` e `tests/test_api_mode.py` confirmados inalterados. TASK-0 está `COMPLETED`; CAP-0 é a próxima elegível e não foi iniciada. Nenhum commit/push realizado.


## Loop de fechamento 2026-08-26 (OX Alpha)

- Worktree reconciliado em 3 commits publicados (`7e8ce8d`, `c72f926`, `851087f`); HEAD == origin/main
- Wiring experimental EG-3A com `eg_reports_store` REJEITADO pela revisão e descartado (`git checkout 851087f --` nos 3 arquivos); arquitetura vigente: AtomicJsonFileSink + payload público intacto
- D1 verificado de ponta a ponta: execução completed + stdout literal + sink atômico em ATHENA_EG3A_SINK_DIR + payload público sem evidence_gate
- D2–D15 verificados (suítes focais + rejeições estáveis)
- 7 tools MCP; proteções api_mode hash-verified

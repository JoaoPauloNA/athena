# Gerência Técnica — Athena-MCP

## Identidade do produto
- Objetivo: núcleo de execução governada do ecossistema Athena. Despacha tarefas para CLIs de agentes com máquina de estados, deadlines, fallback controlado e verificação determinística mais advisory do resultado.
- Escopo atual: dez pacotes modulares com fronteiras de importação verificadas por máquina: `execution/`, `registry/`, `lease/`, `profiles/`, `transport/`, `bridge/`, `router/`, `verifier/`, `mcp_server/` e `zeus/`.
- **Zeus** (2026-08-24, `9482791`): módulo de recomendação de especialista (agente/persona/modelo). Determinístico por (entrada + versão do registro); abstenção fail-closed com reason codes; Themis só desempata com evidência suficiente; **nunca executa** — Aegis autoriza, Athena orquestra, Moiras observa. Gate adversarial: 19 testes em `tests/test_zeus.py`.
- Aegis é a fonte da classificação de perfil de serviço e da política de fallback. O repositório privado é `JoaoPauloNA/aegis`; a distribuição é `athena-aegis`; o pacote de importação Python continua `aegis`. O commit da renomeação no Aegis é `4e3cc20`.
- Fora de escopo confirmado: `skip_permissions` não integra o núcleo novo. `RiskOutcome.REQUIRES_HUMAN_APPROVAL` permanece reservado no contrato do Aegis e não é emitido; não existe mecanismo de pausa/aprovação humana implementado.

## Estado comprovado
- Publicação remota concluída em 2026-08-22: `origin/main` e `main` local estão em `9ab50eb`, incluindo Fix A e Fix C. O `Athena-beta` foi corretamente repromovido e permanece ativo localmente.
- Última atualização: 2026-08-21.
- A CI foi validada em `d3e191a028f0de751994af2bf4c28566f7ff7837` e o handoff documental da fatia anterior foi publicado em `62fb03b` em `origin/main`. O remoto monolítico antigo foi reconciliado por force-push autorizado.
- Preservar as branches locais antigas `athena-release-20260814` e `fix/p0-audit-20260815`; não as excluir sem decisão explícita.
- A fatia de CI está **FECHADA**. O GitHub Actions run `32435500126` passou em Ubuntu e macOS, com Python 3.11 e 3.12.
- A P1 de transporte stdio/JSON-RPC foi publicada em `origin/main` nos commits `76e45ff` e `8336583`. A decisão da fatia seguinte (`transport/` sem consumidor) foi registrada em `4e13975`.

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
- **D-SSH/D-WIN/D-HA** (2026-08-24, decisão do guardião): SSH permanece **dormente** (pacote fechado/testado, sem consumidor); **macOS e Linux são os sistemas oficiais** — meta tripla-SO rejeitada formalmente; `REQUIRES_HUMAN_APPROVAL` permanece **reservado** no Aegis até existir desenho de pausa/retomada. Revisar apenas se surgir demanda real.
- Os dois servidores MCP registrados na máquina do usuário (`athena-mcp-beta` no Claude Desktop, `athena` no Codex Desktop) apontam para o binário do `Athena-beta`.
- Não reabrir a fatia de CI para resolver fragilidades ou suporte a Windows; registrar e sequenciar esses trabalhos separadamente.
- Se algum fluxo futuro precisar SSH remota no núcleo novo, abrir uma fatia própria de contrato antes de qualquer implementação.
- O teste multi-provider foi fechado em 2026-08-22 com Codex2 como tentativa 1 e Claude como fallback confirmado. Não reexecutar por rotina; abrir nova fatia apenas se houver mudança de bridge, provider ou política de fallback.

# Gerência Técnica — Athena-MCP

## Identidade do produto
- Objetivo: núcleo de execução governada do ecossistema Athena. Despacha tarefas para CLIs de agentes com máquina de estados, deadlines, fallback controlado e verificação determinística mais advisory do resultado.
- Escopo atual: nove pacotes modulares com fronteiras de importação verificadas por máquina: `execution/`, `registry/`, `lease/`, `profiles/`, `transport/`, `bridge/`, `router/`, `verifier/` e `mcp_server/`.
- Aegis é a fonte da classificação de perfil de serviço e da política de fallback. O repositório privado é `JoaoPauloNA/aegis`; a distribuição é `athena-aegis`; o pacote de importação Python continua `aegis`. O commit da renomeação no Aegis é `4e3cc20`.
- Fora de escopo confirmado: `skip_permissions` não integra o núcleo novo. `RiskOutcome.REQUIRES_HUMAN_APPROVAL` permanece reservado no contrato do Aegis e não é emitido; não existe mecanismo de pausa/aprovação humana implementado.

## Estado comprovado
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
| Windows | Sem suporte; terminação de árvore de processos e execução do import-linter não atendem ao gate | Tratar em fatia futura independente |
| Branches históricas | Preservam referências anteriores à reconciliação | Não excluir `athena-release-20260814` nem `fix/p0-audit-20260815` |
| Transporte remoto SSH | Pacote existe, mas sem consumidor no fluxo modular atual | Só abrir integração se houver decisão explícita de ampliar o contrato público |
| Fix B — coerção defensiva na fronteira MCP | Aberta, decidida como fatia separada. Hoje `_combo()`/`_ask()` repassam o valor cru: cliente que envie `"15"` em vez de `15` é rejeitado pela validação estrita de `ComboRequest`. O Fix A (commit `8845353`) removeu a causa conhecida, mas não protege contra cliente mal-comportado | Decidir depois se a fronteira do transporte aceita string numérica e converte, mantendo `ComboRequest` estrito. Não misturar com o Fix A |
| `Athena-beta` — repromoção pendente de novo | Está em `fb12d73`; `main` avançou para `2284b5d` (Fix C). O servidor registrado no Claude Desktop segue devolvendo `-32000` em falha de combo até ser repromovido | Repromover a partir de `main` — responsabilidade do guardião |
| Mensagem por subtipo de falha | `FallbackBlocked` e `ComboDeadlineExceeded` hoje voltam com a mesma forma de `AllAttemptsFailed` | Só diferenciar se surgir necessidade comprovada; hoje seria complexidade sem demanda |
| `Athena-beta` — histórico | Repromovido para `fb12d73` (inclui Fix A) em 2026-08-21. Confirmado ao vivo: `run_combo` com `overall_timeout_s` numérico via o servidor registrado (`claude_desktop_config.json` → `Athena-beta/.venv/bin/athena-mcp`) retornou `state: completed` sem erro de validação | Nenhuma — só reabrir se `main` avançar de novo sem repromoção |

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

## Handoff
- Estado-base para qualquer continuação: CI fechada e verde no run `32435500126`, P1 publicada em `origin/main` (`76e45ff`, `8336583`), investigação de `transport/` concluída como “manter, sem integração agora”, e Fix A do schema MCP fechado e publicado (`8845353`/`fb12d73`) — confirmado ao vivo via `Athena-beta`, já repromovido.
- Não reabrir a fatia de CI para resolver fragilidades ou suporte a Windows; registrar e sequenciar esses trabalhos separadamente.
- Se algum fluxo futuro precisar SSH remota no núcleo novo, abrir uma fatia própria de contrato antes de qualquer implementação.
- Pendência real e não decidida: Fix B (coerção defensiva de `overall_timeout_s`/`profile` string→número na fronteira do transporte) — não iniciar sem decisão explícita, não é urgente.
- Nenhuma fatia aberta agora. Próxima decisão é escolher entre Fix B, backlog de compartilhamento/docs/MLX, ou item 7 (Windows) do sequenciamento maior.

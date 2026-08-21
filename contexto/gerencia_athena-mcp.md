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
- A P1 de transporte stdio/JSON-RPC está **FECHADA localmente** no commit `76e45ff`, com gate local verde. Ainda não houve push desse commit.

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
- Nova prioridade ainda não consolidada neste documento após o fechamento da P1.
- Antes de abrir a próxima fatia, decidir explicitamente se o próximo foco será:
  - investigação do módulo `transport/` sem consumidor no fluxo atual; ou
  - retomada do backlog adiado de compartilhamento/documentação/MLX, caso continue prioritário.
- Manter WIP=1. Não abrir a próxima fatia sem registrar a escolha aqui primeiro.

## Pendências e riscos

| Item | Estado / impacto | Direção |
|---|---|---|
| Dependências futuras de runtime | `--no-deps` impede instalação transitiva no CI | Adicionar instalação explícita quando uma nova dependência for introduzida |
| Windows | Sem suporte; terminação de árvore de processos e execução do import-linter não atendem ao gate | Tratar em fatia futura independente |
| Branches históricas | Preservam referências anteriores à reconciliação | Não excluir `athena-release-20260814` nem `fix/p0-audit-20260815` |
| Próxima fatia | Prioridade após P1 ainda não escolhida | Registrar explicitamente a próxima abertura antes de editar código novamente |

## Handoff
- Estado-base para qualquer continuação: CI fechada e verde no run `32435500126`, handoff da CI publicado em `62fb03b` em `origin/main`, e P1 fechada localmente em `76e45ff` com gate local verde.
- Não reabrir a fatia de CI para resolver fragilidades ou suporte a Windows; registrar e sequenciar esses trabalhos separadamente.
- Antes da próxima alteração de código, escolher explicitamente a próxima fatia e atualizar esta gerência com a decisão.

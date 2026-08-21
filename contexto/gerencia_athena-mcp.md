# Gerência Técnica — Athena-MCP

## Identidade do produto
- Objetivo: núcleo de execução governada do ecossistema Athena. Despacha tarefas para CLIs de agentes com máquina de estados, deadlines, fallback controlado e verificação determinística mais advisory do resultado.
- Escopo atual: nove pacotes modulares com fronteiras de importação verificadas por máquina: `execution/`, `registry/`, `lease/`, `profiles/`, `transport/`, `bridge/`, `router/`, `verifier/` e `mcp_server/`.
- Aegis é a fonte da classificação de perfil de serviço e da política de fallback. O repositório privado é `JoaoPauloNA/aegis`; a distribuição é `athena-aegis`; o pacote de importação Python continua `aegis`. O commit da renomeação no Aegis é `4e3cc20`.
- Fora de escopo confirmado: `skip_permissions` não integra o núcleo novo. `RiskOutcome.REQUIRES_HUMAN_APPROVAL` permanece reservado no contrato do Aegis e não é emitido; não existe mecanismo de pausa/aprovação humana implementado.

## Estado comprovado
- Última atualização: 2026-08-20.
- A CI foi validada em `d3e191a028f0de751994af2bf4c28566f7ff7837`, que permanece em `origin/main`; o commit de documentação `dd23be9` vem em seguida apenas na `main` local e pode exigir um push normal futuro. O remoto monolítico antigo foi reconciliado por force-push autorizado.
- Preservar as branches locais antigas `athena-release-20260814` e `fix/p0-audit-20260815`; não as excluir sem decisão explícita.
- A fatia de CI está **FECHADA**. O GitHub Actions run `32435500126` passou em Ubuntu e macOS, com Python 3.11 e 3.12.
- P1 ainda não começou, mas está liberada pelo fechamento da fatia de CI. Manter WIP=1 e não iniciar sem autorização explícita.

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

## Próxima fatia recomendada
- P1: portar o transporte stdio/JSON-RPC da referência em `legado` para o novo `MCPServer`.
- Tratar o legado como referência: suas dependências e acoplamentos não correspondem à arquitetura modular atual.
- Critérios a definir na abertura da fatia: contrato do transporte, integração com o `MCPServer`, testes de protocolo e preservação das fronteiras de importação.
- Não iniciar P1 sem autorização explícita.

## Pendências e riscos

| Item | Estado / impacto | Direção |
|---|---|---|
| Dependências futuras de runtime | `--no-deps` impede instalação transitiva no CI | Adicionar instalação explícita quando uma nova dependência for introduzida |
| Windows | Sem suporte; terminação de árvore de processos e execução do import-linter não atendem ao gate | Tratar em fatia futura independente |
| Branches históricas | Preservam referências anteriores à reconciliação | Não excluir `athena-release-20260814` nem `fix/p0-audit-20260815` |
| Transporte MCP | P1 liberada, ainda não iniciada | Aguardar autorização explícita para o port stdio/JSON-RPC |

## Handoff
- Estado-base para qualquer continuação: CI fechada e verde no run `32435500126`, validado em `d3e191a028f0de751994af2bf4c28566f7ff7837`; o commit de documentação `dd23be9` está apenas na `main` local e pode exigir um push normal futuro.
- Não reabrir a fatia de CI para resolver fragilidades ou suporte a Windows; registrar e sequenciar esses trabalhos separadamente.
- Próxima decisão humana: autorizar ou não a abertura da P1 de transporte stdio/JSON-RPC.

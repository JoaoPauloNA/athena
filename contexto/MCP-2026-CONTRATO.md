# MCP-2026 — contrato do adapter dual-era

Status: `CONCLUÍDA` em 2026-08-29 após revisão independente e gate integral.
Contrato criado antes do código.

## Objetivo

Adicionar suporte estrito ao MCP moderno `2026-07-28` no transporte stdio sem
quebrar clientes legados. O mesmo processo poderá atender a abertura moderna,
identificada por `_meta` em cada requisição, ou a abertura legada por
`initialize`. A fatia não muda a autoridade de roteamento do Athena.

## Escopo autorizado

- implementar `server/discover` e erro moderno `-32022` com versões suportadas;
- validar os campos `_meta` obrigatórios `protocolVersion` e
  `clientCapabilities`, com limites; `clientInfo` é opcional/SHOULD, mas deve ser
  validado quando presente;
- responder `tools/list` e `tools/call` modernos com `resultType: complete`;
- anunciar exatamente as sete tools atuais, em ordem determinística;
- declarar schemas 2020-12 e, quando seguro, `outputSchema`/`structuredContent`;
- preservar o fluxo legado `initialize`/`notifications/initialized` e seus
  envelopes atuais;
- corrigir a identidade anunciada de `0.0.0` para a versão do pacote `0.2.0`;
- aceitar cancelamento moderno por `notifications/cancelled` sem emitir resposta
  posterior para a requisição cancelada;
- criar testes focados, probes stdio reais e benchmark de regressão.

Arquivos preferenciais: `athena/mcp_stdio/`, testes MCP novos ou existentes,
harness específico e estes quatro documentos operacionais. Alterações em outra
área exigem justificativa no relatório e não podem ampliar autoridade.

## Compatibilidade e seleção de era

- Requisição com `_meta.io.modelcontextprotocol/protocolVersion` seleciona
  semântica moderna e stateless para aquela requisição.
- `initialize` sem metadados modernos seleciona semântica legada no processo.
- Um processo pode atender ambas as eras; comportamento ambíguo falha fechado.
- Versão moderna desconhecida retorna `-32022`, mensagem sanitizada e
  `data.supported`/`data.requested`; não cai silenciosamente no legado.
- `server/discover` é obrigatório e retorna versão, capacidade `tools`, identidade
  e cache explícito. Não anuncia recursos, prompts, Tasks ou extensões ausentes.
- O cliente MCP pode declarar identidade e capacidades, mas isso nunca escolhe
  provider, agente, autoridade, perfil, comando ou permissão.

## Fora do escopo e proibições

- Não adicionar nem remover tools MCP; continuam exatamente sete.
- Não implementar Streamable HTTP, autorização HTTP, sessão, OAuth, SSE,
  subscriptions, elicitation, sampling ou a extensão MCP Tasks.
- Não modificar Aletheia, Themis, Argos, outros repositórios ou `Aegis/build/`.
- Não ler, editar, importar, lintar ou testar `athena/api_mode.py` e
  `tests/test_api_mode.py`; validar somente seus SHA-256 conhecidos.
- Não instalar dependências, modelos ou CLIs; não alterar credenciais.
- Não fazer commit, push, promoção do beta, release ou deploy.

## Segurança e desempenho

- JSON-RPC continua uma mensagem por linha; stdout contém somente mensagens MCP.
- Metadados, strings, mapas e mensagens recebem limites definidos; erros não
  refletem conteúdo não confiável nem exceções internas.
- `clientInfo` é informativo, nunca fonte de decisão de segurança.
- Nenhum handshake, arquivo, rede ou subprocesso adicional entra no caminho
  moderno normal. O overhead incremental deve ficar sob o guardrail local de
  5 ms p95 em 30 amostras e 3 warmups.

## Gate de saída

1. Contrato e implementação revisados contra a especificação oficial
   `2026-07-28`.
2. Probes stdio reais comprovam: descoberta; versão incompatível sem qualquer
   reserva/efeito colateral; lista, chamada e ping modernos; cancelamento; e
   cliente legado completo no mesmo binário.
3. Sete tools, ordem, schemas e resultados permanecem determinísticos; nenhuma
   extensão inexistente é anunciada.
4. Testes focados, Ruff, `git diff --check`, gate P0 e suíte protegida-aware
   passam; hashes protegidos permanecem idênticos.
5. Benchmark 30/3 fica dentro do teto e não deixa processo órfão.
6. Contexto operacional registra evidência, limites e estado terminal.

Falha em compatibilidade, segurança, hashes ou processo residual reabre a fatia.

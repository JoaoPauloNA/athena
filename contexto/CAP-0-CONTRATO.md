# Contrato CAP-0 — Cápsula de Execução, Selo Aegis e Iris mínima

Estado: **COMPLETED** (iniciada e validada em 2026-08-28, sob `ROADMAP.md` item 6).

## Objetivo e autoridade

Tornar impossível, no caminho normal `build_stdio_server()` → MCP
`run_combo`/`ask_provider` → router → bridge, iniciar a primeira tentativa ou
qualquer fallback sem uma Cápsula imutável e um Selo de Execução Aegis válido,
de curta duração e ligado ao plano exato da tentativa. Os `attempts` recebidos
do cliente permanecem legado/advisory até ROUTE-0; CAP-0 limita e autoriza o
plano recebido, mas não torna o cliente soberano.

Iris é somente a fronteira local determinística imediatamente anterior ao
bridge. Ela não seleciona, não autoriza, não altera plano e não decide
fallback. O primitive `LocalBridgeRunner` pode continuar público para testes
unitários isolados, mas a composição MCP de produção usa Iris.

## Contratos públicos Aegis

`ExecutionAuthorizationRequest` e `ExecutionSeal` são dataclasses públicas,
congeladas e fechadas. A requisição liga, no mínimo: versão de schema/contrato
e política; digest SHA-256 do plano canônico; identidades de tarefa, execução
e tentativa; provider/agente e modo de acesso; identidades de comando e cwd;
nomes de ambiente permitidos e digest de seus valores; declaração de rede;
escopos de recurso/escrita; orçamento de tempo; fallback; testes; e log level.

O selo acrescenta `issued_at`, `expires_at`, identificador único e MAC. Não
contém credenciais, valores brutos de ambiente, prompt-master nem chave de
assinatura. Coleções aninhadas são normalizadas para tuplas imutáveis.

## Canonicalização, confiança e HMAC

O formato canônico é JSON UTF-8 determinístico (`sort_keys=True`, separadores
compactos, ASCII desabilitado), com tipos estritos e sem números não finitos.
Digests usam SHA-256 com separação de domínio. O MAC usa HMAC-SHA256 da stdlib,
com chave injetada e pertencente ao runtime, nunca aceita pela entrada MCP.
Comparação usa `hmac.compare_digest`.

O emissor Aegis aceita somente a ação `execute_local_cli`, política conhecida,
modo `local_cli`, rede `declared_offline` e dados completos. TTL é curto e
limitado. Verificação é fail-closed contra dado ausente/malformado, selo
expirado ou futuro, adulteração, chave errada, drift de plano ou política e
ação/política desconhecida. `REQUIRES_HUMAN_APPROVAL` permanece reservado e
nunca é emitido.

## Códigos de razão

Resultados usam apenas códigos sanitizados: `APPROVED`, `UNKNOWN_ACTION`,
`UNKNOWN_POLICY`, `MALFORMED_REQUEST`, `MALFORMED_SEAL`,
`UNSUPPORTED_ACCESS_MODE`, `UNSUPPORTED_NETWORK_POLICY`, `TTL_INVALID`,
`SEAL_EXPIRED`, `SEAL_FROM_FUTURE`, `SIGNATURE_INVALID`, `PLAN_DRIFT`,
`POLICY_DRIFT`, `IDENTITY_MISMATCH`, `CAPSULE_MISSING`, `CAPSULE_INVALID`,
`CAPSULE_CONSUMED`, `ENVIRONMENT_NAME_DENIED` e
`ENVIRONMENT_NAME_SECRET_LIKE`. Erros terminais não ecoam comando, cwd, nome
recusado, valor, MAC ou chave.

## Cápsula Athena e tentativa única

`athena/capsule` contém contexto mínimo imutável, plano canônico exato e selo.
O digest cobre comando, cwd canônico, nomes e digest dos valores de ambiente,
provider/acesso, deadlines, timeout global efetivo, lease/teardown, PTY, rede,
recursos, permissões/escrita, fallback e testes. Tentativas sem limite absoluto
explícito recebem orçamento CAP-0 interno de 300 segundos antes da emissão.
Qualquer mutação invalida a
verificação.

Cada tentativa recebe `attempt_id`, plano e selo próprios. Um registro Iris em
memória consome atomicamente o identificador da cápsula antes da delegação,
impedindo replay no mesmo processo. Isso não é persistência nem recuperação
após reinício; FLOW-1 é dona dessas capacidades.

## Ambiente mínimo e limitações

No caminho CAP de produção, o filho recebe somente a base explícita `PATH`,
`LANG`, `LC_ALL`, `LC_CTYPE`, `TERM`, `TMPDIR`, `HOME` e `USER` quando
presentes, mais nomes seguros explicitamente pedidos e autorizados no plano, e
`PWD` calculado. `PATH` possui fallback local determinístico se ausente. Nomes
são ASCII portáveis, comparados após normalização Unicode/case, sem duplicatas
normalizadas. Nomes não autorizados ou semelhantes a segredo (`TOKEN`,
`SECRET`, `PASSWORD`, `PASSWD`, `API_KEY`, `APIKEY`, `AUTH`, `AUTHORIZATION`,
`CREDENTIAL`, `PRIVATE_KEY`, entre outros) são recusados antes do spawn sem
ecoar nomes ou valores.

Manter `HOME` permite compatibilidade normal de CLIs e também acesso aos
arquivos normais do usuário. CAP-0 não oferece isolamento de filesystem. A
política de rede é registrada e ligada ao selo, mas não há enforcement pelo
sistema operacional; somente `declared_offline` é suportada. `secret_ref`,
resolução de credenciais, transporte de prompt por pipe, sandbox/worktree e
enforcement de rede são futuros.

## Arquivos permitidos

Aegis: arquivos públicos sob `aegis/` necessários aos contratos,
canonicalização, emissão/verificação; testes focados; README/CHANGELOG somente
se indispensáveis.

Athena: este contrato e checkpoints operacionais; novos pacotes
`athena/capsule/` e `athena/iris/`; plumbing estritamente necessário em
`mcp_runtime`, `mcp_server`, `mcp_stdio`, `router` e `bridge`; fronteiras de
import se necessárias; testes focados.

## Não objetivos e proteções

Não implementar ROUTE-0, FLOW-1, Harmonia, scheduling multiagente, aprovação
humana, recuperação persistente, resolução de segredos, sandbox de rede,
Olimpo ou nova tool MCP. Não alterar Aletheia, Themis ou Argos. Não ler,
alterar, importar, testar, adicionar, remover ou integrar os dois arquivos
protegidos. Não tocar `Aegis/build/`. Sem dependências, rede, credenciais,
commit, push, tag, release, beta, deploy ou formatação ampla.

## Aceitação

1. Contrato e ACTIVE precedem código.
2. Aegis cobre todos os casos positivos/negativos exigidos e não serializa segredo.
3. O fluxo MCP autoriza antes de toda tentativa.
4. Cápsula inválida ou consumida nunca alcança runner, inclusive fallback.
5. Fallbacks têm tentativa, plano e selo distintos.
6. Ambiente filho é mínimo e não herda credenciais do pai.
7. Env secret-like/não autorizado é negado antes de spawn sem vazamento.
8. Comando local válido conclui; sete tools/schemas ficam deep-equal.
9. Hashes protegidos e `Aegis/build/` ficam inalterados.
10. Sem órfãos/temporários; benchmark 30/3 abaixo de 5 ms p95 em ambas métricas.
11. Diff-check, Ruff, boundaries, P0, focados e suítes integrais passam.

## Rollback

Sem migração ou estado persistente CAP: remover novos pacotes/testes e
restaurar somente o plumbing CAP, preservando mudanças preexistentes. Em falha
de aceitação, CAP-0 permanece ACTIVE e ROUTE-0 não é liberada.

## Fechamento (2026-08-28)

- Aegis: `195 passed`; Athena focado: `109 passed`; Athena não-regressão: `457 passed, 3 deselected`, com o teste protegido explicitamente excluído.
- Diff-check, Ruff protegido-aware e oito contratos de import passaram.
- Benchmark gerencial final 30/3: bridge p95 `1.202625 ms` e MCP incremental p95 `1.862125 ms`, ambos sob 5 ms; cleanup não forçado e 33 execuções terminais.
- Custo isolado CAP prepare/verify/consume: p95 0,310375 ms (30 amostras, 3 warmups).
- A correção final fechou as coleções dos contratos contra strings, bytes, mappings, não-sequências e itens inválidos; flags booleanas e digest do ambiente também são validados. Hashes protegidos e resíduo preexistente `Aegis/build/` permaneceram inalterados. Nenhum commit, push ou publicação foi realizado.

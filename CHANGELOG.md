# CHANGELOG

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versionamento por [SemVer](https://semver.org/lang/pt-BR/).

## [0.2.0] - 2026-08-24

### Adicionado
- **Zeus** (`9482791`): registro versionado de agentes/personas, roteador determinístico com abstenção fail-closed, lifecycle completo; ZEUS-GATE com 19 testes adversariais. Persona Router é componente interno.
- **Nike** (`add734e`): `NikeSelector` — desempate por score Themis válido sobre o ZeusRouter; score inválido é ignorado com reason code.
- **Evidence Gate** (`74bb51b`): motor offline determinístico de validação de Result Envelopes; EG-GATE com zero falso PASS em conjunto reservado.
- **Chronos** (`015a59f`): ciclo de correção governado mínimo (circuit breaker 3 falhas, reabertura só com novos critérios).
- **Observação sombra Moiras** (`2758071`, `7d28e03`): contrato sanitizado allowlist + emitter opt-in (`ATHENA_MOIRAS_SHADOW=1`); validação cross-repo no repo Moiras.
- Piloto Argos (`378adeb`): evidência determinística de página local.
- Servidor MCP sintético (`harness/s04_synthetic_server.py`) para matrizes de compatibilidade.
- Transporte stdio/JSON-RPC modular (`athena/mcp_stdio/`) com entrypoint `athena-mcp` e composição concreta fora do pacote fechado (`mcp_runtime.py`).
- Execuções de longa duração registradas antes do despacho: `get_execution` e `cancel_execution` responsivos durante trabalho em background; abandono pelo cliente finaliza execuções não terminais como `client_abandoned`.
- Documentação as-built inicial (README EN/PT-BR) descrevendo o núcleo modular real.

### Corrigido
- **Fix A** (`8845353`): schema das tools anuncia tipos simples (`number`/`string`) — hosts MCP descartavam `type` em uniões, causando rejeição indevida de valores válidos.
- **Fix C** (`2284b5d`): falha terminal de combo volta como resultado da tool com `isError: true` e payload sanitizado (estado, exit code, saídas parciais, deadline expirado) — antes virava erro de protocolo `-32000` descartando todo o diagnóstico.

### Mudado
- Integração de risco/permissão consumida exclusivamente pela fachada pública `aegis.decision.evaluate` do pacote privado `athena-aegis`; perfis locais são shims de compatibilidade.
- CI restrita a POSIX (Ubuntu/macOS × Python 3.11/3.12) com checkout privado do Aegis via deploy key somente leitura.

### Removido
- Suporte a Windows da matriz de CI (teardown de árvore de processos e import-linter não atendem o gate; porte é fatia futura).

## [0.1.0]

Release histórica do monólito original (dashboard, 12 tools, recommend/ratings). Preservada como referência em `legado/`; ver `legado/CHANGELOG.md`.

[0.2.0]: https://github.com/JoaoPauloNA/athena
[0.1.0]: https://github.com/JoaoPauloNA/athena

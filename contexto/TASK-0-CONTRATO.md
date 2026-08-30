# Contrato TASK-0 — submissão durável de tarefa

Estado: **COMPLETED** (validada em 2026-08-28, sob `ROADMAP.md` item 5).

## Objetivo

Adicionar submissão de tarefa durável e idempotente cujo handle e estado
sanitizado sobrevivem à desconexão do cliente MCP e ao reinício do processo
Athena. Esta fatia **não executa** tarefas enfileiradas. Conectar tarefas à
orquestração pertence a `FLOW-1`. Não se afirma execução em background,
execução exactly-once, nem resumo de comandos arbitrários.

## Semântica pública (MCP)

Duas tools novas somam-se às cinco existentes (`run_combo`, `ask_provider`,
`get_execution`, `list_executions`, `cancel_execution`), que permanecem
inalteradas: **`submit_task`** e **`get_task`**. Total: sete tools.

Os schemas anunciados por `tools/list` são fechados no nível público. Seus
`maxLength` são limites em caracteres como hint interoperável; os limites
normativos de tamanho permanecem os bytes UTF-8 aplicados pela validação de
runtime.

### `submit_task`

Entrada:
- `idempotency_key`: string obrigatória não vazia, máx. 256 bytes UTF-8.
- `task`: objeto obrigatório, serialização canônica máx. 64 KiB, schema
  explícito e fechado:
  - `task_type` (obrigatório): identificador canônico `^[a-z][a-z0-9_.-]{0,127}$`.
  - `input` (obrigatório): string, máx. 32 KiB UTF-8.
  - `project_ref` (opcional): string não vazia, máx. 1024 bytes UTF-8.
  - `constraints` (opcional): objeto dentro dos limites globais.
  - `expected_output` (opcional): objeto dentro dos limites globais.
  - `priority` (opcional): inteiro 0..9, default 5.
- Campos de nível superior fora do schema são rejeitados.
- Limites globais recursivos (aplicados a `task`, `constraints`,
  `expected_output`): profundidade máx. 32, máx. 10.000 itens, números não
  finitos (`NaN`/`Infinity`) rejeitados, nomes de chave que pareçam segredo
  (senha/token/secret/authorization/apikey, normalizados) rejeitados em
  qualquer nível.
- Nunca ecoar `task`, `idempotency_key` ou valores de possível segredo em
  mensagens de erro.
- **Limitação documentada**: chaves JSON duplicadas no objeto `task` não são
  detectáveis depois que o transporte stdio (`json.loads` padrão) já
  materializou um `dict`; esta fatia não amplia o parser do transporte para
  não alterar comportamento não relacionado. Apenas a validação do
  `athena/tasks` (que já recebe um `dict`) é reforçada.

Saída: `task_handle` (id opaco aleatório), `state` (`"queued"`),
`created` (bool), `revision` (int), `created_at`/`updated_at` (UTC ISO-8601
estável).

Idempotência:
- Armazena somente o digest SHA-256, com separação de domínio, da
  `idempotency_key` — nunca a chave bruta.
- Mesma chave + `task` canonicamente byte-idêntico → mesmo `task_handle`,
  `created=false`.
- Mesma chave + `task` diferente → erro sanitizado `IDEMPOTENCY_CONFLICT`.
- Submissões concorrentes com a mesma chave convergem para uma única linha
  e um único handle.

### `get_task`

Entrada: `task_handle` obrigatório.
Saída: `found` (bool); se `found`, apenas `task_handle`, `task_type`,
`state`, `priority`, `revision`, `created_at`, `updated_at`. Nunca expõe
`input`, `constraints`, `expected_output`, `project_ref`, hashes, caminhos
de banco de dados ou material de idempotência.

## Armazenamento durável

- `sqlite3` da stdlib apenas, sem nova dependência.
- Pacote fechado `athena/tasks/` com protocolo explícito
  (`TaskStoreContract`) e implementação SQLite (`SQLiteTaskStore`).
- Localização do DB injetável; override em runtime via `ATHENA_STATE_DIR`;
  default seguro e preguiçoso `~/.athena/state`.
- `initialize`, `ping` e `tools/list` não criam arquivo algum — a store é
  construída sem tocar o sistema de arquivos; diretório/arquivo só são
  criados na primeira chamada real de `submit_task`/`get_task`.
- Diretório de estado `0700`; arquivo de DB `0600`.
- Diretório de estado symlink ou arquivo de DB symlink/não regular são
  rejeitados (`TASK_STORE_UNAVAILABLE`, sanitizado). Limitação documentada:
  `sqlite3.connect` da stdlib não aceita `O_NOFOLLOW`; a verificação é
  checar-então-usar (`os.path.islink`/`stat`) antes de conectar, não uma
  transação POSIX atômica — mesma classe de resíduo já registrada para
  `write_snapshot` em `CFG-SEC-0`.
- `PRAGMA busy_timeout` limitado; `journal_mode=WAL` e `synchronous=FULL`
  para durabilidade resistente a crash.
- Tabela de versão de schema; versão desconhecida ou mais nova é recusada
  fail-closed.
- SQL inteiramente parametrizado.
- JSON canônico da tarefa é armazenado apenas internamente (nunca
  retornado por `get_task`).
- Conexões abertas e fechadas deterministicamente por operação
  (context manager), sem conexão global mutável de módulo.
- Thread-safe sob `ThreadPoolExecutor`: restrição por lock de instância
  mais `UNIQUE` constraint na coluna de digest de idempotência.

## Integração

- `athena/mcp_server/contracts.py`: novo `TaskStoreContract` (importado de
  `athena.tasks`), novos métodos `submit_task`/`get_task` em
  `MCPServerContract`, novo campo opcional `task_store` em
  `MCPServerDependencies` (default `None`, para não quebrar testes
  existentes fora do escopo autorizado que constroem
  `MCPServerDependencies` sem essa store).
- `athena/mcp_server/server.py`: `MCPServer.submit_task`/`get_task`
  delegam ao `task_store`; se ausente, retornam `TASK_STORE_UNAVAILABLE`
  sanitizado sem detalhe interno.
- `athena/mcp_stdio/application.py`: schemas `tools/list` para as duas
  tools novas; validação/canonicalização de argumentos JSON usando
  `athena.tasks` antes de despachar.
- `athena/mcp_runtime.py`: compõe `SQLiteTaskStore()` (resolução
  preguiçosa de `ATHENA_STATE_DIR`) e injeta em `MCPServerDependencies`.
- EOF continua sem tocar tarefas duráveis enfileiradas; `abandon_nonterminal`
  (execuções `run_combo`/`ask_provider`) permanece inalterado.

## Arquivos permitidos

`contexto/TASK-0-CONTRATO.md`; `contexto/INDEX.md`, `ESTADO_ATUAL.md`,
`ROADMAP.md`, `CLAUDE_ORQUESTRADOR.md`; `contexto/gerencia_athena-mcp.md`
(somente checkpoints); pacote novo `athena/tasks/`;
`athena/mcp_server/{contracts.py,server.py,__init__.py}`;
`athena/mcp_stdio/contracts.py` (se necessário — não usado nesta
implementação); `athena/mcp_stdio/application.py`; `athena/mcp_runtime.py`;
testes novos focados de TASK-0; `tests/test_mcp_server.py` e
`tests/test_mcp_stdio.py` apenas onde a contagem/schema exato de tools
precisa mudar; `pyproject.toml` somente se uma fronteira de import exigir.

## Proibições

Aletheia, Themis, Argos, bridge, config loader, benchmark, router, Zeus,
Aegis, Chronos, Moiras, Evidence Gate, lease, profiles — não tocados.
`athena/api_mode.py` e `tests/test_api_mode.py` não tocados; hashes
verificados antes/depois. Sem novas dependências, commit, push, tag,
promoção de beta, release, deploy, credenciais, serviço de rede, deleção ou
fatias subsequentes (`CAP-0` não é iniciada por este contrato).

## Códigos de falha

`INVALID_TASK`, `TASK_TOO_LARGE`, `IDEMPOTENCY_CONFLICT`,
`TASK_STORE_UNAVAILABLE`. `get_task` modela ausência como `found=false`,
não como erro — `TASK_NOT_FOUND` não é emitido nesta fatia.

## Testes de aceitação (resumo — ver seção ACCEPTANCE do prompt operador)

1–15 conforme especificado: primeira submissão `created=true`; replay exato
mesmo handle `created=false`; mesma chave/task diferente conflito; convergência
concorrente; `get_task` sanitizado; handle desconhecido seguro; limites e
variantes rejeitadas; chave de idempotência bruta ausente dos bytes do DB;
`task`/erros não vazam entrada; permissões e symlink/não-regular rejeitados;
processo B recupera tarefa enfileirada do processo A via mesmo
`ATHENA_STATE_DIR`; cinco tools antigas preservadas, sete no total;
`ping`/`tools/list` não criam DB; hashes protegidos inalterados; sem
resíduo órfão/temporário.

## Fechamento (2026-08-28)

- Correção pontual autorizada em EG3A atualizou somente a expectativa de
  `tools/list` de cinco para as sete tools canônicas; a lógica do Evidence
  Gate permaneceu inalterada.
- Correção de schema público: `submit_task` agora anuncia objeto superior e
  `task` fechados, campos/tipos/limites declarados e `get_task` anuncia objeto
  superior fechado. A regressão estrutural comprova os dois schemas e as sete
  tools; o teste separado confirma que o limite UTF-8 de idempotência continua
  autoritativo além do hint em caracteres.
- Revalidação: `git diff --check`, Ruff, testes focados (72),
  `harness/p0_gate.py`, suíte não-regressão (`393 passed, 3 deselected`, com
  `tests/test_api_mode.py` excluído), smoke JSON-RPC real em dois processos e
  benchmark de 30 amostras/3 warmups passaram. p95 bridge incremental:
  `0.385166 ms`; p95 MCP incremental: `0.4995 ms`, ambos sob 5 ms.
- Os hashes protegidos permaneceram `db8885c0…8b6d796` e
  `da123ab4…d98150`. Nenhum commit, push ou publicação foi realizado.

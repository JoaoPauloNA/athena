# Athena-MCP

Servidor MCP (stdio, JSON-RPC) para **execução governada de CLIs de agentes de IA** na sua máquina: combos ordenados com fallback controlado, lease de workspace, deadlines com cancelamento confiável e verificação determinística + advisory do resultado.

> **Status:** beta modular para uso local e single-user. POSIX (macOS/Linux). Windows não é suportado nesta versão.

## O que faz

1. **Roteamento com failover governado** — `run_combo` executa tentativas ordenadas; o fallback só ocorre após término confirmado da tentativa anterior e aprovação da política de risco.
2. **Verificação de alegações** — `verification` opcional re-executa comandos permitidos e confere arquivos citados (determinístico), com camada advisory não bloqueante.
3. **Ciclo de vida observável** — toda execução tem `execution_id`, estados explícitos (`completed`, `failed`, `cancelled`, `timed_out`…), consulta (`get_execution`, `list_executions`) e cancelamento idempotente (`cancel_execution`).
4. **Falha sanitizada** — timeouts e falhas voltam como resultado da tool com `isError: true` e payload sanitizado (estado, exit code, stdout/stderr parciais, duração, deadline expirado) — nunca como erro de protocolo opaco.

## Tools MCP (7)

| Tool | Descrição |
|---|---|
| `run_combo` | Executa sequência de tentativas com fallback governado |
| `ask_provider` | Executa um provider único com governança Aegis |
| `get_execution` | Consulta estado sanitizado de uma execução |
| `list_executions` | Lista execuções recentes |
| `cancel_execution` | Solicita cancelamento cooperativo |
| `submit_task` | Submete tarefa durável (TASK-0/FLOW-1) |
| `get_task` | Consulta estado de tarefa durável |

## Instalação

Requisitos: Python ≥ 3.11, pip, Git.

```bash
# 1. Dependência privada de risco/política (athena-aegis)
git clone git@github.com:JoaoPauloNA/aegis.git ../aegis
pip install -e ../aegis

# 2. Athena-MCP (sem resolver dependências automaticamente)
git clone git@github.com:JoaoPauloNA/athena.git
cd athena
python -m venv .venv && source .venv/bin/activate
pip install -e . --no-deps

# 3. Verificação
python -m pytest tests -m "not regression"   # suíte deve passar
```

Registro no cliente MCP (exemplo Claude Desktop):

```json
{
  "mcpServers": {
    "athena": { "command": "/caminho/para/athena/.venv/bin/athena-mcp" }
  }
}
```

## Limitações conhecidas

- **POSIX-only**: o bridge usa grupos de processo e PTY POSIX; encerramento em Windows não é garantido (`TERMINATION_UNCONFIRMED`).
- **Single-user**: processo stdio local por usuário; sem rede, sem autenticação, sem multiusuário.
- **Sem dashboard de produção**: as tools MCP são a interface operacional. O adaptador **Olimpo** (OLIMPO-0) existe como app local opt-in read-only em loopback para configuração/observação — não é dashboard completo de produto.
- **Sem sandbox forte**: as CLIs executadas rodam com as permissões do seu usuário no workspace informado — use diretórios de trabalho dedicados.
- **Fallback automático**: perfis `authenticated_external` e `unknown` nunca fazem fallback automático (política fail-closed do Aegis).
- Aprovação humana inline (`REQUIRES_HUMAN_APPROVAL`) está reservada no contrato do Aegis e ainda não é emitida.

## Arquitetura (resumo)

```
cliente MCP → mcp_stdio (JSON-RPC) → mcp_server (camada fina das tools)
    → router (combos/fallback via aegis.decision.evaluate)
        → bridge (subprocess/PTY POSIX, sob lease de workspace)
        → verifier (determinístico → advisory)
    → registry (execuções sanitizadas: get/list/cancel)
```

Fronteiras de import entre módulos são verificadas por máquina (`import-linter`; ver `pyproject.toml`). O núcleo não importa código de `legado/`.

## Documentação

- `CHANGELOG.md` — histórico de mudanças.
- `docs/backlog.md` — pendências registradas.
- `contexto/gerencia_athena-mcp.md` — handoff técnico corrente.

## Licença

Ver [LICENSE](LICENSE).


## Ecossistema: repositórios independentes

| Repositório | Papel |
|---|---|
| **Athena-MCP** (este) | Orquestrador/agregador — recebe projetos independentes via contratos públicos versionados |
| Aegis (privado) | Gate independente de risco/permissão |
| Aletheia | claimed-vs-verified (episódios que alimentam o Themis) |
| Moiras | observação shadow |
| Themis (privado) | reputação/scoring |
| Argos (privado) | browser QA observacional |
| athena.dev (privado) | produto/site |

## Módulos internos (não são repositórios)

Zeus (elegibilidade) · Nike (resolução de runtime/provider) · Chronos (ciclo governado) · Evidence Gate (EG-1 motor + EG-3A sink opt-in) · Clio (logging 4 níveis) · Harmonia (paralelismo/write-sets) · Capsule (ambiente mínimo com seal) · Iris (preflight) · Olimpo (adaptador local read-only) · Flow/Tasks (durabilidade) · Lease · config loader · bridge · SSH transport (dormente, D-SSH).

## Testes (comando exato e contagem fresh)

```bash
.venv/bin/python harness/p0_gate.py           # lint/boundaries/p0 — PASS
.venv/bin/python -m pytest tests -m "not regression" -q --ignore=tests/test_api_mode.py
# 715 passed, 3 deselected — runtime baseline 5319763;
# documentation-only commits do not change this test evidence.
```

`tests/test_api_mode.py` e `athena/api_mode.py` são arquivos protegidos do usuário (hash-verificados; não executados nesta suíte).

# Athena-MCP

Servidor MCP (stdio, JSON-RPC) para **execução governada de CLIs de agentes de IA** na sua máquina: combos ordenados com fallback controlado, lease de workspace, deadlines com cancelamento confiável e verificação determinística + advisory do resultado.

> **Status:** beta modular para uso local e single-user. POSIX (macOS/Linux). Windows não é suportado nesta versão.

## O que faz

1. **Roteamento com failover governado** — `run_combo` executa tentativas ordenadas; o fallback só ocorre após término confirmado da tentativa anterior e aprovação da política de risco.
2. **Verificação de alegações** — `verification` opcional re-executa comandos permitidos e confere arquivos citados (determinístico), com camada advisory não bloqueante.
3. **Ciclo de vida observável** — toda execução tem `execution_id`, estados explícitos (`completed`, `failed`, `cancelled`, `timed_out`…), consulta (`get_execution`, `list_executions`) e cancelamento idempotente (`cancel_execution`).
4. **Falha sanitizada** — timeouts e falhas voltam como resultado da tool com `isError: true` e payload sanitizado (estado, exit code, stdout/stderr parciais, duração, deadline expirado) — nunca como erro de protocolo opaco.

## Tools MCP (5)

| Tool | Descrição |
|---|---|
| `run_combo` | Executa sequência de tentativas com fallback governado |
| `ask_provider` | Executa uma solicitação preparada para um provider/CLI |
| `get_execution` | Consulta uma execução sanitizada por `execution_id` ou `request_id` |
| `list_executions` | Lista execuções recentes sanitizadas |
| `cancel_execution` | Solicita cancelamento idempotente |

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
- **Sem dashboard**: a interface é exclusivamente as tools MCP acima.
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

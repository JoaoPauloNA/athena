# Athena-MCP

[Português](README.md) · [English](README.en.md)

Servidor MCP (stdio, JSON-RPC) para **execução governada de CLIs de agentes de IA** na sua máquina: combos ordenados com fallback controlado, lease de workspace, deadlines com cancelamento confiável e verificação determinística + advisory do resultado.

> **Status:** Athena v1 tecnicamente fechado e verificado para uso local e
> single-user. POSIX (macOS/Linux). Windows não é suportado nesta versão.

## Estado da distribuição v1

- O runtime público expõe sete tools MCP e preserva as autoridades separadas
  de Zeus, Nike, Aegis, Chronos e Evidence Gate.
- O checkout `Athena-beta` já contém o runtime funcional do v1. Em 2026-08-31,
  a diferença restante para o checkout principal era somente documentação e
  testes adicionais de instalação/CAS, sem mudança no código de runtime.
- A validação corrente protegida registra `716 passed, 3 deselected`; o
  baseline histórico de evidência do runtime permanece `5319763`, com
  `715 passed, 3 deselected`.

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
# 1. Checkouts irmãos
git clone git@github.com:JoaoPauloNA/aegis.git ../aegis
git clone git@github.com:JoaoPauloNA/athena.git
cd athena

# 2. Um único ambiente virtual para Athena + Aegis
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ../aegis
python -m pip install -e ".[dev]"

# 3. Verificação protegida
python harness/p0_gate.py
python -m pytest tests -m "not regression" -q --ignore=tests/test_api_mode.py
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
- **Sem dashboard de produção**: as tools MCP são a interface operacional. **Olimpo OLIMPO-0** é uma biblioteca HTTP local opt-in em loopback para observação e publicação de configuração validada por preview + CAS; não executa, cancela ou autoriza tarefas e não é o produto Olimpo completo.
- **Sem sandbox forte**: as CLIs executadas rodam com as permissões do seu usuário no workspace informado — use diretórios de trabalho dedicados.
- **Fallback automático**: perfis `authenticated_external` e `unknown` nunca fazem fallback automático (política fail-closed do Aegis).
- Aprovação humana inline (`REQUIRES_HUMAN_APPROVAL`) está reservada no contrato do Aegis e ainda não é emitida.

## Publicação curada e segurança

O Athena adota um **monorepo público modular**. Zeus, Nike, Chronos, Evidence
Gate, Clio, Harmonia, Capsule e Iris possuem fronteiras próprias, mas permanecem
no núcleo enquanto não houver necessidade comprovada de ciclo de release ou
consumidor externo independente.

O material público pode conter código, contratos, schemas, testes, documentação
e templates sanitizados. Prompts proprietários completos pertencem a um
repositório privado separado; prompts preenchidos com dados reais, conversas,
respostas privadas, caminhos pessoais, tokens, chaves, cookies e sessões OAuth
não pertencem a nenhum repositório Git, mesmo privado.

Português é a documentação canônica e inglês é a primeira tradução mantida.
Outros idiomas entram somente quando houver demanda e revisão editorial capaz
de impedir divergência entre versões.

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

Zeus (elegibilidade) · Nike (resolução de runtime/provider) · Chronos (ciclo governado) · Evidence Gate (EG-1 motor + EG-3A sink opt-in) · Clio (logging 4 níveis) · Harmonia (paralelismo/write-sets) · Capsule (ambiente mínimo com seal) · Iris (preflight) · Olimpo (biblioteca HTTP loopback opt-in; observação + configuração CAS) · Flow/Tasks (durabilidade) · Lease · config loader · bridge · SSH transport (dormente, D-SSH).

## Testes (comando exato e contagem fresh)

```bash
.venv/bin/python harness/p0_gate.py           # lint/boundaries/p0 — PASS
.venv/bin/python -m pytest tests -m "not regression" -q --ignore=tests/test_api_mode.py
# 716 passed, 3 deselected — current protected-aware suite after install-smoke hardening;
# runtime evidence baseline remains 5319763 (715 passed, 3 deselected).
```

`tests/test_api_mode.py` e `athena/api_mode.py` são arquivos protegidos do usuário (hash-verificados; não executados nesta suíte).


## Classificações terminais v1 (ADR-0001)

| Item | Classificação |
|---|---|
| Núcleo 7-tool + módulos internos | IMPLEMENTED_AND_VERIFIED (runtime baseline `5319763`) |
| IAProxy zchat/kimi | OPTIONAL_NOT_CONFIGURED (login manual do usuário; nunca automatizado) |
| Content Gate | OPTIONAL_FUTURE (corpus humano CG-0 inexistente; nunca fabricado) |
| Aceite externo | EXTERNAL_ACCEPTANCE_PENDING |
| Olimpo O-2..O-5 | OPTIONAL_FUTURE |
| Metis | DEFERRED_BY_ADR (ADR-0001 §7) |
| SSH | INTENTIONALLY_CLOSED (D-SSH) |

Nenhum item opcional bloqueia o fechamento técnico do v1. Detalhes: `docs/adr/ADR-0001-v1-scope-and-deferrals.md`.

### Olimpo OLIMPO-0

OLIMPO-0 é uma **biblioteca**, não um daemon instalado nem parte do startup MCP. A composição autorizada instancia `athena.olimpo.OlimpoHttpServer` com dependências explícitas e chama `start()`; o servidor recusa bind fora de `127.0.0.1`, gera/exige CSRF e deve ser encerrado com `shutdown()`. O smoke HTTP real e o CAS de configuração são cobertos por `tests/test_olimpo_e2e.py`.

# Athena-MCP 🔱

[![CI](https://github.com/JoaoPauloNA/athena/actions/workflows/ci.yml/badge.svg)](https://github.com/JoaoPauloNA/athena/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Status: alpha](https://img.shields.io/badge/status-alpha%20%2F%20pre--release-orange.svg)

**Um único servidor MCP para detectar, rotear, verificar e economizar entre as CLIs de IA instaladas na sua máquina.**

> 💡 O roteamento do Athena é inspirado no conceito do OmniRoute — mesma ideia, implementação independente.

> ⚠️ **Status: alpha / pre-release.** O Athena-MCP é construído para compartilhamento local controlado — rodando na sua própria máquina, ou nas mãos de pessoas e máquinas em quem você já confia. Não passou por hardening para exposição pública ou não confiável. Leia [Segurança e validação](#segurança-e-validação) e [Limitações](#limitações) antes de confiar nele para além do uso local e cooperativo.

🇺🇸 [Read in English](README.md) · 🇨🇳 [阅读中文](README.zh-CN.md)

---

## O que é o Athena-MCP?

O Athena-MCP transforma as CLIs de IA instaladas na sua máquina (Codex, Claude Code, Cursor Agent, Antigravity, OpenCode, Ollama e mais) em um único conselho orquestrado, exposto como **servidor MCP** (para qualquer chat de IA delegar tarefas) e como **dashboard web** de gerenciamento.

Em vez de apostar em um único agente, o Athena:

- **Detecta** CLIs de IA do sistema (macOS, Windows, Linux)
- **Roteia** cada tarefa para um provider/modelo usando uma tabela de notas local cruzada com o que está instalado
- **Verifica** relatórios de execução contra evidências reais do projeto, para as alegações que dá para checar de fato — nunca uma prova geral de correção
- **Economiza** afastando tarefas simples de modelos pesados e caros
- **Faz failover** entre providers com combos configuráveis, condicionado à terminação confirmada da tentativa anterior

## Funcionalidades

| Recurso | Descrição |
|---|---|
| 🔍 **Scanner de CLIs** | Encontra CLIs de IA no PATH + `~/.local/bin`, Homebrew, npm global, Scoop, cargo, go, flatpak, snap… em macOS, Linux e Windows; 22 CLIs conhecidas listadas mesmo quando não instaladas |
| 📊 **Dashboard** | Providers, modelos, combos, uso e a tabela "Melhores por Função" em `localhost:7100` — sem autenticação, só para uso local |
| 🏆 **Tabela de notas de modelos** | Notas 0–10 por função (frontend, backend, raciocínio, rapidez), cacheadas localmente com TTL de 7 dias e fallback de seed; atualizar a partir de leaderboards ao vivo é um job externo e opt-in, não algo que o Athena roda sozinho |
| 🧪 **Verificador de relatórios** | Camada determinística re-executa comandos de teste/lint de uma whitelist citados no relatório e checa se os arquivos alegados existem (sem modelo nenhum); camada advisory (modelo barato/grátis) faz triagem do que a determinística não consegue decidir. Nenhuma das duas prova correção geral — ver [Verificação](docs/pt-BR/verificacao.md) |
| 💡 **Tool `recommend`** | Descreva a tarefa e receba o melhor provider+modelo instalado, com justificativa |
| 💰 **Roteamento econômico** | Estimativa de complexidade; modelos pesados são excluídos das sugestões para tarefas simples |
| 🔄 **Combos com failover** | Cadeias de providers com retries, modelo por etapa e políticas de timeout — o failover só prossegue quando o service_profile permite e a terminação da tentativa anterior está confirmada |
| 📜 **Contrato de relatório de 10 tópicos** | Executores devolvem relatórios enxutos e estruturados — o contexto do orquestrador fica limpo |
| 🧭 **Ciclo de vida e controle de execução** | Toda chamada longa recebe um `execution_id` com máquina de estados explícita, timeouts de inatividade/absoluto e cancelamento idempotente via `cancel_execution` |
| 🕯️ **Observador sombra [Moiras](https://github.com/JoaoPauloNA/moiras) opcional** | Um adaptador desativado por padrão traduz atualizações allowlisted em classificações temporais inertes; o Athena nunca as usa para controlar timeout, cancelamento, fallback, lease ou autorização |

## Início rápido

```bash
git clone https://github.com/JoaoPauloNA/athena.git
cd athena
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Subir o dashboard:

```bash
python -c "from athena.dashboard.app import run_dashboard; run_dashboard()"   # API na :20129
cd frontend && npx vite --port 7100                                            # UI na :7100
```

Rodar como servidor MCP (stdio), ex. na config do seu cliente MCP:

```json
{
  "mcpServers": {
    "athena": {
      "command": "/caminho/para/.venv/bin/python",
      "args": ["-m", "athena.mcp_server"],
      "cwd": "/caminho/para/athena"
    }
  }
}
```

## Ferramentas MCP

O Athena-MCP expõe 12 ferramentas via stdio. Parâmetros completos em [Referência das ferramentas MCP](docs/pt-BR/ferramentas-mcp.md).

| Tool | Função |
|---|---|
| `list_providers` | CLIs instaladas/disponíveis, modelos, papéis, notas |
| `list_combos` | Lista os combos configurados e suas cadeias de failover |
| `run_combo` | Executa um prompt pela cadeia de failover de um combo (`verify=true` checa o relatório de cada provider) |
| `ask_provider` | Envia tarefa a um provider (`verify=true` liga o verificador de relatórios) |
| `deliberate` | Consulta vários agentes em paralelo (sem etapa de verificação) |
| `recommend` | "Quem devo chamar para esta tarefa?" — notas × modelos instalados × complexidade |
| `refresh_models` | Re-escaneia os catálogos de modelos das CLIs |
| `list_usage` | Contadores de chamadas e tokens estimados por provider |
| `list_reliability` | Ranking local de claimed vs verified por CLI |
| `get_execution` | Consulta uma execução registrada por `execution_id` ou `request_id` |
| `list_executions` | Lista execuções registradas recentemente (somente leitura, sanitizado) |
| `cancel_execution` | Solicita cancelamento idempotente de uma execução em andamento |

## Como funciona a verificação

```
Orquestrador → CLI Executora → relatório de 10 tópicos
                     │
                     ▼
        Checagem determinística (re-executa comandos
        de teste/lint da whitelist, checa arquivos citados)
                     │
        conclusiva ──┴────── inconclusiva
         │                       │
       veredito           Checagem advisory (modelo mais
                           barato disponível, nunca o
                           provider do executor)
                                 │
                  VERDADEIRO ────┴──────── FALSO
                     │                       │
                  aceito             volta ao executor (1x)
                                          │
                                FALSO de novo → ESCALA para o orquestrador
                                (veredito + evidências + histórico)
```

As duas camadas só julgam o que o próprio relatório torna checável — comandos e arquivos citados. Nenhuma delas é prova geral de correção, e o `deliberate` não roda verificação nenhuma. Detalhes e roadmap atual: [docs/pt-BR/verificacao.md](docs/pt-BR/verificacao.md).

## Segurança e validação

- `skip_permissions=true` passa a flag de sem-confirmação de cada CLI (ex.: `--dangerously-skip-permissions`) — use só em projetos/workspaces em que você confia.
- O dashboard **não tem autenticação**; mantenha as portas `7100`/`20129` fora de redes que você não controla.
- Execuções via SSH (`ssh_host`) nunca confirmam que o processo remoto de fato terminou — timeouts e cancelamentos por SSH ficam indeterminados e bloqueiam o failover automático até verificação manual.
- Detalhes completos: [SECURITY.md](SECURITY.md).
- Sequência de validação local e reproduzível (lint + suíte de testes completa + gate sintético offline, sem precisar de CLI real): [docs/pt-BR/compartilhamento-local.md](docs/pt-BR/compartilhamento-local.md).

## Limitações

- **Status alpha, compartilhamento local** — sem hardening para exposição pública ou multi-tenant.
- **A limpeza do grupo de processos é coberta em POSIX (macOS/Linux)** para descendentes que permanecem no grupo controlado pelo Athena. Um descendente que escape deliberadamente com `setsid()`/`setpgid()` permanece indeterminado. No Windows, o Athena controla apenas o processo direto; a árvore mais ampla é classificada `NOT_GUARANTEED` em timeout/cancelamento.
- **O lease de workspace é intraprocesso** — serializa tentativas concorrentes dentro de um único processo do Athena e não dá nenhuma proteção entre múltiplos workers/processos ou hosts compartilhando o mesmo filesystem.
- **Fallback/retry é condicional, não automático** — depende da política do service_profile para o tipo de falha (erro vs. timeout) e exige confirmação positiva da terminação da tentativa anterior. Os perfis `authenticated_external` e `unknown` nunca fazem fallback automático.
- **As notas de modelos são um cache local, não um feed ao vivo** — o Athena não busca nem atualiza dados de leaderboard sozinho; atualizar é um passo externo e opt-in.
- **Moiras é opcional e somente observacional** — o servidor MCP não a ativa automaticamente. O adaptador exige o pacote separado e expõe apenas advisory intraprocesso; não integra conselho/modelo e não altera o fluxo de controle do Athena.

## Documentação

- 📗 [Arquitetura](docs/pt-BR/arquitetura.md) · [Referência das ferramentas MCP](docs/pt-BR/ferramentas-mcp.md) · [Verificação](docs/pt-BR/verificacao.md) · [Compartilhamento local](docs/pt-BR/compartilhamento-local.md)
- 📘 [Architecture](docs/en/architecture.md) · [MCP tools reference](docs/en/mcp-tools.md) · [Verification](docs/en/verification.md) · [Local sharing & validation](docs/en/local-sharing.md)
- 📙 [架构](docs/zh-CN/architecture.md) · [MCP 工具参考](docs/zh-CN/mcp-tools.md) *(tradução da comunidade, não sincronizada com os documentos EN/PT-BR acima — não define nenhuma garantia)*

## Requisitos

- Python ≥ 3.10
- Pelo menos uma CLI de IA instalada (Codex, Claude Code, Cursor Agent, Antigravity, OpenCode, Ollama…)
- Node.js (apenas para o servidor de dev do dashboard)

## Licença

MIT © 2026 João Paulo

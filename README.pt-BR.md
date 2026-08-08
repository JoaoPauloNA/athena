# Athena-MCP 🔱

[![CI](https://github.com/JoaoPauloNA/athena/actions/workflows/ci.yml/badge.svg)](https://github.com/JoaoPauloNA/athena/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**Um único servidor MCP para detectar, rotear, verificar e economizar entre todas as CLIs de IA da sua máquina.**

> 💡 O roteamento do Athena é inspirado no conceito do OmniRoute — mesma ideia, implementação independente.

🇺🇸 [Read in English](README.md) · 🇨🇳 [阅读中文](README.zh-CN.md)

---

## O que é o Athena-MCP?

O Athena-MCP transforma as CLIs de IA instaladas na sua máquina (Codex, Claude Code, Cursor Agent, Antigravity, OpenCode, Ollama e mais) em um único conselho orquestrado, exposto como **servidor MCP** (para qualquer chat de IA delegar tarefas) e como **dashboard web** de gerenciamento.

Em vez de apostar em um único agente, o Athena:

- **Detecta** todas as CLIs de IA do sistema automaticamente (macOS, Windows, Linux)
- **Roteia** cada tarefa para o melhor provider/modelo usando uma tabela de notas auto-atualizada
- **Verifica** relatórios de execução contra evidências reais do projeto — um "detector de mentiras" que pega agentes alegando trabalho que não fizeram
- **Economiza** afastando tarefas simples de modelos pesados e caros
- **Faz failover** entre providers com combos configuráveis

## Funcionalidades

| Recurso | Descrição |
|---|---|
| 🔍 **Scanner cross-platform de CLIs** | Encontra CLIs de IA no PATH + `~/.local/bin`, Homebrew, npm global, Scoop, cargo, go, flatpak, snap… 23 CLIs conhecidas listadas mesmo quando não instaladas |
| 📊 **Dashboard** | Providers, modelos, combos, uso e a tabela "Melhores por Função" em `localhost:7100` |
| 🏆 **Tabela de notas de modelos** | Notas 0–10 por função (frontend, backend, raciocínio, rapidez), atualizada **semanalmente a partir de leaderboards** (SWE-bench, GPQA, Design Arena) |
| 🕵️ **Detector de mentiras (verificador)** | Um modelo barato/grátis (OpenCode free primeiro) confere cada relatório com evidências do git; relatório falso volta para correção 1x e depois escala para o orquestrador |
| 💡 **Tool `recommend`** | Descreva a tarefa e receba o melhor provider+modelo instalado, com justificativa |
| 💰 **Roteamento econômico** | Estimativa de complexidade; modelos pesados são excluídos das sugestões para tarefas simples |
| 🔄 **Combos com failover** | Cadeias de providers com retries, modelo por etapa e políticas de timeout |
| 📜 **Contrato de relatório de 10 tópicos** | Executores devolvem relatórios enxutos e estruturados — o contexto do orquestrador fica limpo |

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

| Tool | Função |
|---|---|
| `list_providers` | CLIs instaladas/disponíveis, modelos, papéis, notas |
| `ask_provider` | Envia tarefa a um provider (`verify=true` liga o detector de mentiras) |
| `run_combo` | Executa um prompt por uma cadeia com failover |
| `deliberate` | Consulta vários agentes em paralelo |
| `recommend` | "Quem devo chamar para esta tarefa?" — notas × modelos instalados × complexidade |
| `refresh_models` | Re-escaneia os catálogos de modelos das CLIs |
| `list_usage` | Contadores de chamadas e tokens estimados por provider |

## Como funciona a verificação

```
Orquestrador → CLI Executora → relatório de 10 tópicos
                     │
                     ▼
        Verificador (modelo mais barato disponível,
        nunca o mesmo provider do executor)
        confere git status/diff + arquivos citados
                     │
        VERDADEIRO ──┴──────── FALSO
         │                       │
      aceito              volta ao executor (1x)
                               │
                  VERDADEIRO ──┴──── FALSO de novo
                      │                 │
                   aceito      ESCALA para o orquestrador
                               (veredito + evidências + histórico)
```

## Documentação

- 📗 [Arquitetura](docs/pt-BR/arquitetura.md) · [Referência das ferramentas MCP](docs/pt-BR/ferramentas-mcp.md)
- 📘 [Architecture](docs/en/architecture.md) · [MCP tools reference](docs/en/mcp-tools.md)
- 📙 [架构](docs/zh-CN/architecture.md) · [MCP 工具参考](docs/zh-CN/mcp-tools.md)

## Requisitos

- Python ≥ 3.10
- Pelo menos uma CLI de IA instalada (Codex, Claude Code, Cursor Agent, Antigravity, OpenCode, Ollama…)
- Node.js (apenas para o servidor de dev do dashboard)

## Licença

MIT © 2026 João Paulo

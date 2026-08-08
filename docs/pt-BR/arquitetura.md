# Arquitetura

🇺🇸 [EN](../en/architecture.md) · 🇨🇳 [中文](../zh-CN/architecture.md)

## Visão geral

```
┌─────────────────────────────────────────────────────────┐
│ Orquestrador (qualquer chat de IA com MCP)              │
└──────────────┬──────────────────────────┬───────────────┘
               │ stdio (JSON-RPC)         │ HTTP
┌──────────────▼──────────────┐  ┌────────▼───────────────┐
│ athena/mcp_server.py        │  │ athena/dashboard/app.py│
│ 7 ferramentas MCP           │  │ FastAPI :20129         │
└──────────────┬──────────────┘  └────────┬───────────────┘
               │                          │
┌──────────────▼──────────────────────────▼───────────────┐
│ athena/providers.py                                     │
│ ask_provider · ask_provider_verified · combos           │
└──┬─────────┬──────────┬──────────┬─────────┬────────────┘
   │         │          │          │         │
┌──▼───┐ ┌───▼────┐ ┌───▼─────┐ ┌──▼──────┐ ┌▼───────────┐
│bridge│ │contract│ │verifier │ │recommend│ │models/     │
│.py   │ │.py     │ │.py      │ │.py      │ │ratings.py  │
│execu-│ │relató- │ │detector │ │tarefa→  │ │catálogos + │
│ção   │ │rio 10t │ │mentiras │ │melhor   │ │notas       │
└──┬───┘ └────────┘ └────┬────┘ └─────────┘ └────────────┘
   │                     │
┌──▼─────────────────────▼────────────────────────────────┐
│ CLIs de IA: codex · claude · agent · agy · openclaude · │
│ opencode · ollama (+ auto-descobertas e 16 conhecidas)  │
└─────────────────────────────────────────────────────────┘
```

## Módulos

| Módulo | Responsabilidade |
|---|---|
| `bridge.py` | Execução de subprocessos, PTY, limpeza de saída, **PATH enriquecido** (`which` acha CLIs em `~/.local/bin`, Homebrew, Scoop, npm global etc. mesmo quando o host GUI tem PATH mínimo) |
| `providers.py` | Registro de CLIs (catálogo fixo de 23 + auto-descoberta), construção de comandos por CLI, `ask_provider`, `ask_provider_verified` |
| `contract.py` | Contrato de relatório de 10 tópicos injetado em todo prompt de executor + checagem barata de formato via regex |
| `verifier.py` | Detector de mentiras: evidências objetivas (`git status`, `git diff --stat`, existência de arquivos citados) + veredito de modelo barato; anti-conluio (verificador ≠ provider executor); loop de correção e escalada na 2ª falha |
| `recommend.py` | Classificação da tarefa (função × complexidade), teto econômico (simples→só light), recomendação de provider+modelo entre os instalados |
| `ratings.py` | Notas 0–10 por função, cache JSON `~/.athena/model_ratings.json` com TTL de 7 dias, atualizado semanalmente por job automatizado de pesquisa web |
| `models.py` | Catálogo vivo de modelos por CLI (`--list-models`/`models`), catálogo de fallback, classificação de peso (light/medium/heavy) |
| `router.py` + `combos.py` | Cadeias de failover: providers ordenados, retries, modelo/timeout por etapa |
| `agents.py` | Papéis nomeados (arquiteto, revisor, contraponto…) injetados nos prompts |
| `usage.py` | Contadores locais por provider (`~/.athena/usage.json`) |
| `dashboard/app.py` | Backend FastAPI + templates HTMX/Jinja + API JSON |

## Detecção de CLIs (cross-platform)

1. **PATH enriquecido** — PATH do processo + locais conhecidos por SO:
   - **Windows:** `~/.local/bin`, `%LOCALAPPDATA%\Programs`, npm global, WinGet Links, Scoop shims, Chocolatey, Scripts do pip `--user`, cargo, go
   - **macOS:** `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, npm global, cargo, go, bun
   - **Linux:** `~/.local/bin`, `/usr/local/bin`, snap, exports do flatpak, npm global, cargo, go, bun
2. **Heurística de nome** — match de palavra inteira por palavras-chave (removendo extensões do Windows: `claude.cmd` → `claude`)
3. **Probe** — saída de `--help`/`--version` pontuada por palavras-chave de IA (shims `.cmd`/`.bat` rodam via `cmd /c`)
4. **Catálogo fixo** — 23 CLIs conhecidas sempre listadas; as não instaladas aparecem como *Offline* no dashboard

## Arquivos de dados (`~/.athena/`)

| Arquivo | Conteúdo |
|---|---|
| `models_catalog.json` | Listas vivas de modelos por CLI (TTL de 5 dias) |
| `model_ratings.json` | Notas por função, atualizadas semanalmente de leaderboards públicos |
| `combos.json` | Cadeias de failover |
| `usage.json` | Contadores de chamadas, durações, tokens estimados |
| `custom_providers.json` | Providers definidos pelo usuário (sobrescrevem a auto-descoberta) |

Tudo configurável via `ATHENA_DATA_DIR`, `ATHENA_MODELS_FILE` etc. (ver `athena/config.py`).

## Princípios de design

1. **O contexto do orquestrador é sagrado** — executores devolvem relatórios enxutos de 10 tópicos, nunca dumps de código.
2. **Confie, mas verifique** — relatórios são conferidos contra evidências objetivas do projeto, não aceitos de graça.
3. **Barato antes de caro** — modelos grátis/locais para verificação; pesados só quando a complexidade justifica (aviso, nunca bloqueio).
4. **Funciona com o que você tem** — toda decisão de roteamento é computada a partir das CLIs realmente instaladas na máquina.

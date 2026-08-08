# Athena-MCP Dashboard — Design Spec

**Data:** 2026-08-07  
**Versão:** 1.0  
**Escopo:** Dashboard de gerenciamento local para o Athena-MCP (OmniRouter com CLIs locais)

---

## 1. Visão

O Athena-MCP Dashboard é uma interface web local que permite ao usuário:

1. **Visualizar** quais CLIs de agentes de IA estão instaladas no sistema
2. **Explorar** os modelos disponíveis em cada CLI
3. **Criar e gerenciar "combos"** — sequências ordenadas de providers com failover automático
4. **Monitorar** uso, logs e estatísticas de requisições

O dashboard roda em `http://localhost:20128` e serve como camada de gerenciamento do Athena-MCP, que por sua vez pode ser exposto como servidor MCP (stdio ou SSE) para clientes como Claude Desktop, Cursor, ou Kimi Work.

---

## 2. Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENTE MCP                               │
│              (Claude Desktop / Cursor / Kimi)                    │
└──────────────────────┬──────────────────────────────────────────┘
                       │ stdio / SSE
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ATHENA-MCP SERVER                            │
│                                                                  │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐  │
│  │ MCP Router   │   │ Dashboard    │   │ Combo Engine         │  │
│  │ (stdio/SSE)  │◄──│ (FastAPI)    │◄──│ (Failover Logic)     │  │
│  └──────┬───────┘   └──────┬───────┘   └──────────┬───────────┘  │
│         │                  │                      │              │
│         ▼                  ▼                      ▼              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Provider Manager (reutilizado do Conselho)   │   │
│  │  • Detecção de CLIs (claude, agent, agy, codex...)       │   │
│  │  • Discovery de modelos via CLI (--list-models, etc.)    │   │
│  │  • Cache em ~/.athena/models_catalog.json (TTL 5 dias)   │   │
│  └──────────────────────────────────────────────────────────┘   │
│         │                  │                      │              │
│         ▼                  ▼                      ▼              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              CLIs Locais (executam via subprocess)        │   │
│  │  Claude Code  │  Cursor Agent  │  agy (Gemini)  │  Codex │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Persistência: ~/.athena/                                        │
│  • combos.json        — definições de combos                     │
│  • models_catalog.json — cache de modelos (TTL 5 dias)           │
│  • usage.json         — métricas de uso                          │
│  • logs/              — logs de requisições (rolling, 30 dias)   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1. Principais Componentes

| Componente | Responsabilidade |
|---|---|
| `athena/providers.py` | Detecção de CLIs, discovery de modelos, execução de prompts (adaptado do Conselho) |
| `athena/combos.py` | CRUD de combos, serialização, validação |
| `athena/router.py` | Lógica de failover: tenta provider N, se falhar vai para N+1 |
| `athena/usage.py` | Registro e agregação de métricas |
| `athena/dashboard/` | FastAPI app + templates Jinja2 + HTMX |
| `athena/mcp_server.py` | Interface MCP (stdio/SSE) que usa o router |

---

## 3. Estrutura de Diretórios

```
Athena-MCP/
├── athena/
│   ├── __init__.py
│   ├── __main__.py              # Entry point: python -m athena
│   ├── providers.py             # Adaptado do Conselho (detecção + execução)
│   ├── models.py                # Adaptado do Conselho (catálogo de modelos)
│   ├── combos.py                # Nova: gerenciamento de combos
│   ├── router.py                # Nova: lógica de failover entre providers
│   ├── usage.py                 # Nova: métricas de uso
│   ├── config.py                # Nova: paths, defaults, env vars
│   ├── mcp_server.py            # Interface MCP (stdio ou SSE)
│   ├── dashboard/
│   │   ├── __init__.py
│   │   ├── app.py               # FastAPI application
│   │   ├── static/
│   │   │   ├── css/
│   │   │   │   └── style.css    # Estilos custom (Pico CSS base)
│   │   │   └── js/
│   │   │       └── htmx.min.js  # HTMX via CDN (fallback local)
│   │   └── templates/
│   │       ├── base.html        # Layout base com navegação
│   │       ├── index.html       # Dashboard principal (4 seções)
│   │       ├── providers.html   # Lista de CLIs (HTMX partial)
│   │       ├── models.html      # Modelos por CLI (HTMX partial)
│   │       ├── combos.html      # Lista de combos (HTMX partial)
│   │       ├── combo_form.html  # Form de criação/edição de combo
│   │       ├── logs.html        # Logs de requisições (HTMX partial)
│   │       └── toast.html       # Notificações inline
│   └── cli/                     # (futuro) CLI standalone do athena
│       └── ...
├── tests/
│   └── ...
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-08-07-athena-mcp-dashboard-design.md
├── pyproject.toml
└── README.md
```

---

## 4. API do Dashboard (FastAPI Endpoints)

### 4.1. Páginas (HTML completas)

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Dashboard principal (renderiza `index.html`) |
| `GET` | `/providers` | Página de CLIs detectadas |
| `GET` | `/models` | Página de modelos disponíveis |
| `GET` | `/combos` | Página de combos |
| `GET` | `/combos/new` | Form de criação de combo |
| `GET` | `/combos/{id}/edit` | Form de edição de combo |
| `GET` | `/logs` | Página de logs |

### 4.2. HTMX Partials (fragmentos HTML)

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/hx/providers` | Lista de CLIs (partial, atualizável) |
| `POST` | `/hx/providers/refresh` | Re-detecta CLIs e retorna lista atualizada |
| `GET` | `/hx/models/{provider_id}` | Modelos de um provider específico |
| `GET` | `/hx/combos` | Lista de combos (partial) |
| `POST` | `/hx/combos` | Cria novo combo (retorna lista atualizada) |
| `PUT` | `/hx/combos/{id}` | Atualiza combo |
| `DELETE` | `/hx/combos/{id}` | Remove combo |
| `POST` | `/hx/combos/{id}/test` | Testa combo (pinga cada provider na ordem) |
| `GET` | `/hx/logs` | Logs recentes (partial, paginado) |
| `POST` | `/hx/logs/clear` | Limpa logs |

### 4.3. API JSON (para MCP e integrações)

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/v1/providers` | JSON com CLIs detectadas |
| `GET` | `/api/v1/models` | JSON com todos os modelos |
| `GET` | `/api/v1/combos` | JSON com todos os combos |
| `POST` | `/api/v1/combos/{id}/run` | Executa prompt via combo (usado pelo MCP) |
| `GET` | `/api/v1/usage` | JSON com métricas de uso |

---

## 5. Persistência

### 5.1. `~/.athena/combos.json`

```json
{
  "version": "1.0",
  "combos": [
    {
      "id": "default",
      "name": "Padrão",
      "description": "Combo padrão com failover entre todos os providers disponíveis",
      "enabled": true,
      "created_at": "2026-08-07T16:00:00Z",
      "updated_at": "2026-08-07T16:00:00Z",
      "chain": [
        {
          "provider_id": "claude",
          "model": "sonnet",
          "role": "arquiteto",
          "timeout": 300,
          "skip_permissions": false
        },
        {
          "provider_id": "agent",
          "model": "auto",
          "role": "revisor",
          "timeout": 300,
          "skip_permissions": false
        },
        {
          "provider_id": "agy",
          "model": "gemini-3.6-flash-medium",
          "role": "contraponto",
          "timeout": 120,
          "skip_permissions": false
        }
      ],
      "failover_policy": {
        "on_timeout": true,
        "on_error": true,
        "on_quota_exceeded": true,
        "max_retries_per_provider": 1,
        "retry_delay_seconds": 2
      }
    }
  ]
}
```

### 5.2. `~/.athena/usage.json`

```json
{
  "total_requests": 47,
  "total_failovers": 2,
  "by_combo": {
    "default": { "requests": 47, "failovers": 2 }
  },
  "by_provider": {
    "claude": { "requests": 35, "success": 34, "failures": 1 },
    "agent": { "requests": 12, "success": 11, "failures": 1 },
    "agy": { "requests": 2, "success": 2, "failures": 0 }
  },
  "last_updated": "2026-08-07T16:30:00Z"
}
```

### 5.3. `~/.athena/logs/YYYY-MM-DD.jsonl`

Formato JSON Lines, um log por linha:

```json
{"timestamp":"2026-08-07T16:25:12Z","combo_id":"default","attempt":1,"provider_id":"claude","model":"sonnet","status":"success","duration_ms":4500,"prompt_chars":1200,"output_chars":3400}
{"timestamp":"2026-08-07T16:28:33Z","combo_id":"default","attempt":1,"provider_id":"claude","model":"sonnet","status":"timeout","error":"Timeout after 300s","duration_ms":300000,"prompt_chars":5000,"output_chars":0}
{"timestamp":"2026-08-07T16:28:35Z","combo_id":"default","attempt":2,"provider_id":"agent","model":"auto","status":"success","duration_ms":3200,"prompt_chars":5000,"output_chars":2800}
```

---

## 6. Lógica de Failover (Router)

```python
async def run_combo(combo_id: str, prompt: str, **kwargs) -> RunResult:
    combo = load_combo(combo_id)
    if not combo or not combo.enabled:
        raise ValueError(f"Combo '{combo_id}' não encontrado ou desabilitado")

    for attempt, step in enumerate(combo.chain, start=1):
        provider_id = step.provider_id
        try:
            result = await ask_provider(
                provider_id,
                prompt,
                model=step.model,
                role=step.role,
                timeout=step.timeout,
                skip_permissions=step.skip_permissions,
                **kwargs
            )
            
            log_request(combo_id, attempt, provider_id, result)
            
            if result.success:
                record_usage(provider_id, success=True)
                return result
            
            # Falha "suave" (retornou mas com erro) — tenta próximo?
            if combo.failover_policy.on_error:
                continue
            else:
                return result
                
        except TimeoutError:
            log_request(combo_id, attempt, provider_id, status="timeout")
            if combo.failover_policy.on_timeout:
                continue
            raise
            
        except Exception as e:
            log_request(combo_id, attempt, provider_id, status="error", error=str(e))
            if combo.failover_policy.on_error:
                continue
            raise
    
    # Todos os providers do combo falharam
    raise AllProvidersFailed(f"Combo '{combo_id}' esgotou todos os {len(combo.chain)} providers")
```

---

## 7. Interface MCP

O Athena-MCP expõe as mesmas ferramentas do Conselho, mas com adição do conceito de **combo**:

### Novas Tools

| Tool | Descrição |
|---|---|
| `list_combos` | Lista combos disponíveis com status |
| `run_combo` | Executa prompt via combo (com failover automático) |
| `get_combo_status` | Status de um combo específico |

### Tool `run_combo` (exemplo de schema)

```json
{
  "name": "run_combo",
  "description": "Executa um prompt através de um combo Athena-MCP com failover automático entre providers.",
  "inputSchema": {
    "type": "object",
    "required": ["combo_id", "prompt"],
    "properties": {
      "combo_id": {
        "type": "string",
        "description": "ID do combo a usar (ex: 'default'). Use list_combos para ver disponíveis."
      },
      "prompt": {
        "type": "string",
        "description": "Prompt a enviar ao agente."
      },
      "working_directory": {
        "type": "string",
        "description": "Diretório de trabalho."
      },
      "timeout": {
        "type": "integer",
        "description": "Timeout por provider em segundos (override do combo)."
      }
    }
  }
}
```

---

## 8. Design da Interface Web

### 8.1. Layout Base (`base.html`)

```html
<!DOCTYPE html>
<html data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>🔱 Athena-MCP</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
  <script src="https://unpkg.com/htmx.org@2.0.0/dist/htmx.min.js"></script>
  <link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
  <nav class="container-fluid">
    <ul><li><strong>🔱 Athena-MCP</strong></li></ul>
    <ul>
      <li><a href="/">Dashboard</a></li>
      <li><a href="/providers">CLIs</a></li>
      <li><a href="/combos">Combos</a></li>
      <li><a href="/logs">Logs</a></li>
      <li><span hx-get="/hx/status" hx-trigger="every 30s" class="status-badge">🟢 Online</span></li>
    </ul>
  </nav>
  <main class="container">
    {% block content %}{% endblock %}
  </main>
  <footer class="container">
    <small>Athena-MCP v{{ version }} | <a href="https://github.com/...">GitHub</a></small>
  </footer>
</body>
</html>
```

### 8.2. Dashboard Principal (`index.html`)

4 cards principais em grid (responsive):

```html
<div class="grid">
  <!-- Card 1: CLIs -->
  <article>
    <header><h3>🖥️ CLIs Detectadas</h3></header>
    <div hx-get="/hx/providers" hx-trigger="load">
      <p aria-busy="true">Detectando...</p>
    </div>
    <footer><button hx-post="/hx/providers/refresh">🔄 Re-detectar</button></footer>
  </article>

  <!-- Card 2: Combos -->
  <article>
    <header><h3>🔀 Combos Ativos</h3></header>
    <div hx-get="/hx/combos" hx-trigger="load"></div>
    <footer><a href="/combos/new" role="button">+ Novo Combo</a></footer>
  </article>

  <!-- Card 3: Uso -->
  <article>
    <header><h3>📊 Uso Hoje</h3></header>
    <div hx-get="/hx/usage" hx-trigger="load,every 60s">
      <p aria-busy="true">Carregando...</p>
    </div>
  </article>

  <!-- Card 4: Últimas Requisições -->
  <article>
    <header><h3>📝 Logs Recentes</h3></header>
    <div hx-get="/hx/logs?limit=5" hx-trigger="load,every 10s"></div>
    <footer><a href="/logs">Ver todos →</a></footer>
  </article>
</div>
```

### 8.3. HTMX Patterns

- **Polling automático:** `hx-trigger="every Ns"` para status e logs
- **Swap inline:** `hx-swap="outerHTML"` para atualizar cards sem recarregar página
- **Confirm actions:** `hx-confirm="Excluir combo 'X'?"` para deleções
- **Toast notifications:** respostas com `HX-Trigger: {"showToast": "Combo criado!"}`

---

## 9. Execução

### 9.1. Como iniciar o dashboard

```bash
# Instalação
pip install -e .

# Iniciar dashboard
athena dashboard
# ou
python -m athena dashboard

# Iniciar servidor MCP (stdio)
athena mcp

# Iniciar ambos
athena serve  # Dashboard + MCP SSE
```

### 9.2. Variáveis de ambiente

| Variável | Default | Descrição |
|---|---|---|
| `ATHENA_PORT` | `20128` | Porta do dashboard |
| `ATHENA_HOST` | `127.0.0.1` | Host do dashboard |
| `ATHENA_DATA_DIR` | `~/.athena` | Diretório de persistência |
| `ATHENA_MODELS_TTL_DAYS` | `5` | TTL do cache de modelos |
| `ATHENA_LOG_RETENTION_DAYS` | `30` | Retenção de logs |
| `ATHENA_MCP_MODE` | `stdio` | Modo MCP: `stdio` ou `sse` |

---

## 10. Dependências

```toml
[project]
name = "athena-mcp"
version = "0.1.0"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "jinja2>=3.1.0",
    "pydantic>=2.0",
    "python-multipart>=0.0.9",  # para FastAPI forms
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx>=0.27.0", "ruff>=0.5.0"]
```

---

## 11. Roadmap / Futuro

| Versão | Feature |
|---|---|
| v0.1 | Dashboard básico + combos + failover |
| v0.2 | Limites de uso por combo (tokens/requisições) |
| v0.3 | Notificações desktop/web quando combo esgota |
| v0.4 | Integração com billing real das CLIs (se disponível) |
| v0.5 | Combos condicionais (ex: "se frontend → agy primeiro") |
| v0.6 | API REST pública para integração com outros tools |

---

## 12. Decisões de Design

1. **FastAPI + HTMX vs React/Streamlit:** FastAPI + HTMX escolhido por simplicidade, zero build step, e facilidade de manutenção.
2. **JSON files vs SQLite:** JSON para config (legível, editável manualmente) e JSON Lines para logs (append-only, fácil de parsear).
3. **Porta 20128:** Segue a convenção do OmniRoute original.
4. **Reutilização do Conselho:** Os módulos `providers.py` e `models.py` são adaptados do ConselhoMCP, renomeando `conselho/` para `athena/` e ajustando paths.
5. **Dois modos de execução:** Dashboard pode rodar standalone ou integrado ao servidor MCP.

---

*Spec pronto para revisão. Próximo passo: implementação via `writing-plans`.*

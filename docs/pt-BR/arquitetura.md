# Arquitetura

🇺🇸 [EN](../en/architecture.md) · 🇨🇳 [中文](../zh-CN/architecture.md) *(tradução da comunidade, não sincronizada — apenas informativa)*

## Visão geral

```
┌─────────────────────────────────────────────────────────┐
│ Orquestrador (qualquer chat de IA com MCP)              │
└──────────────┬──────────────────────────┬───────────────┘
               │ stdio (JSON-RPC)         │ HTTP
┌──────────────▼──────────────┐  ┌────────▼───────────────┐
│ athena/mcp_server.py        │  │ athena/dashboard/app.py│
│ 12 ferramentas MCP          │  │ FastAPI :20129         │
└──────────────┬──────────────┘  └────────┬───────────────┘
               │                          │
┌──────────────▼──────────────────────────▼───────────────┐
│ athena/providers.py                                     │
│ ask_provider · ask_provider_verified · combos           │
└──┬─────────┬──────────┬──────────┬─────────┬────────────┘
   │         │          │          │         │
┌──▼───┐ ┌───▼────┐ ┌───▼─────┐ ┌──▼──────┐ ┌▼───────────┐
│bridge│ │contract│ │verifier │ │recommend│ │models/     │
│.py   │ │.py     │ │+dverify │ │.py      │ │ratings.py  │
│execu-│ │relató- │ │.py      │ │tarefa→  │ │catálogos + │
│ção   │ │rio 10t │ │checagem │ │melhor   │ │notas       │
│      │ │        │ │relatório│ │         │ │            │
└──┬───┘ └────────┘ └────┬────┘ └─────────┘ └────────────┘
   │                     │
┌──▼─────────────────────▼────────────────────────────────┐
│ CLIs de IA: codex · claude · agent · agy · openclaude · │
│ opencode · ollama (+ auto-descobertas e outras 15       │
│ conhecidas), local ou via SSH                            │
└─────────────────────────────────────────────────────────┘
```

Abaixo do `bridge.py`, três módulos governam *o quão seguro* é repetir uma execução ou confiar num relatório sobre ela: `execution.py` (máquina de estados do ciclo de vida por tentativa), `execution_registry.py` (registro no servidor que alimenta as ferramentas `*_execution`) e `workspace_lease.py` (serialização intraprocesso contra um diretório de trabalho). `service_profiles.py` decide a política de timeout/fallback de uma chamada antes dela começar, e `ssh.py` monta o comando para execução remota.

## Módulos

| Módulo | Responsabilidade |
|---|---|
| `bridge.py` | Execução de subprocesso/PTY, limpeza de saída, **PATH enriquecido** (`which` acha CLIs em `~/.local/bin`, Homebrew, Scoop, npm global etc. mesmo quando o host GUI tem PATH mínimo). Aplica os deadlines absoluto + de inatividade e conduz as transições do `execution.py` em cada tentativa. |
| `execution.py` | Contrato de execução: máquina de estados `ExecutionState` (`QUEUED → STARTING → RUNNING → ... → COMPLETED/FAILED/CANCELLED/TIMED_OUT/TERMINATION_UNCONFIRMED`), `ExecutionRecord` (identidade, timing, flags de confirmação de terminação, `to_dict()` sanitizado), `DeadlineBudget` para timeouts multi-estágio e `ExecutionControl` para cancelamento thread-safe. |
| `execution_registry.py` | Registro em memória, sanitizado e limitado (padrão 256 registros / 64 tentativas cada) que o `mcp_server.py` atualiza conforme as ferramentas de execução longa (`run_combo`, `ask_provider`) progridem. Alimenta `get_execution`, `list_executions`, `cancel_execution`. Redige identificadores, categoriza motivos em texto livre numa lista fixa de códigos, e evicta primeiro os registros já *finalizados* mais antigos — nunca um ativo. |
| `workspace_lease.py` | Lease intraprocesso indexada pelo diretório de trabalho canônico (resolvido via realpath): `workspace canônico → execution_id titular → attempt_id ativo`. Serializa chamadas de `router.run_combo()` e tentativas de fallback/retry que compartilham diretório de trabalho. **Apenas intraprocesso** — `threading.Lock` + dict simples não dão nenhuma proteção entre processos do SO separados, workers ou hosts compartilhando o mesmo filesystem. |
| `moiras_adapter.py` | Observador opcional e desativado por padrão para embeddings que passam explicitamente `MoirasShadowObserver.observe` pelo callback de lifecycle existente. Importa o pacote Moiras separado sob demanda, remove metadados de provider/processo e mantém somente advisory temporal inerte. O Athena nunca o consome para controlar execução. |
| `service_profiles.py` | Políticas nomeadas (`text_generation`, `code_agent`, `build_test`, `research`, `local_model`, `verification`, `workspace_mutation`, `authenticated_external`, `unknown`) que definem o timeout absoluto máximo, se exigem diretório de trabalho, e se permitem fallback em erro/timeout. `authenticated_external` e `unknown` nunca permitem fallback automático. |
| `ssh.py` | Monta o argv do `ssh` para `ask_provider(..., ssh_host=...)`: valida a string do host, escapa o comando remoto como um único token para evitar injeção via conteúdo do prompt, e nunca aceita ou armazena senha/credencial. |
| `providers.py` | Registro de CLIs (catálogo fixo de 22 + auto-descoberta), construção de comandos por CLI, `ask_provider`, `ask_provider_verified`. Adquire/libera a lease de workspace em torno de cada tentativa. |
| `contract.py` | Contrato de relatório de 10 tópicos injetado em todo prompt de executor + checagem barata de formato via regex |
| `verifier.py` | Verificador advisory: triagem por modelo barato sobre evidências objetivas (`git status`, `git diff --stat`, existência de arquivos citados); anti-conluio (verificador ≠ provider executor); loop de correção e escalada na 2ª falha. Delega primeiro ao `dverify.py` e só cai no modelo quando nada é conclusivo. |
| `dverify.py` | Verificador determinístico: sem modelo. Re-executa uma whitelist de comandos de teste/lint que o relatório alega terem passado (tokenizado via `shlex`, nunca shell, timeout por comando, no máximo 3 comandos) e compara exit codes reais; checa se arquivos alegados como criados/editados realmente existem. Não re-roda um comando se o relatório já admite a falha perto dele. |
| `reliability.py` | Persiste cada episódio de verificação, redigido, em `~/.athena/verdicts.json` (últimos 500); agrega uma taxa local de claimed vs verified por CLI para `list_reliability` e `recommend`. |
| `recommend.py` | Classificação da tarefa (função × complexidade), teto econômico (simples→só light), recomendação de provider+modelo entre os instalados |
| `ratings.py` | Notas 0–10 por função, cache JSON local `~/.athena/model_ratings.json` com TTL de 7 dias e fallback de seed. O próprio Athena nunca busca dados de leaderboard — atualizar o cache a partir de fontes ao vivo é um job externo e opt-in, fora deste repositório. |
| `models.py` | Catálogo vivo de modelos por CLI (`--list-models`/`models`), catálogo de fallback, classificação de peso (light/medium/heavy) |
| `router.py` + `combos.py` | Cadeias de failover: providers ordenados, retries, modelo/timeout por etapa. Antes de iniciar uma nova tentativa, `router._fallback_block_reason` exige que os metadados de execução da tentativa anterior confirmem positivamente a terminação (processo direto e, quando havia `pgid`, a árvore de processos) — caso contrário levanta `FallbackBlocked` em vez de arriscar dois processos concorrentes. |
| `agents.py` | Papéis nomeados (arquiteto, revisor, contraponto…) injetados nos prompts |
| `usage.py` | Contadores locais por provider (`~/.athena/usage.json`) |
| `dashboard/app.py` | Backend FastAPI + templates HTMX/Jinja + API JSON (sem autenticação — só para uso local, ver [SECURITY.md](../../SECURITY.md)) |

## Ciclo de vida da execução

Toda tentativa de subprocesso de provider é rastreada por um `ExecutionRecord` (`athena/execution.py`) que percorre uma máquina de estados explícita e unidirecional — estados terminais (`COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`, `TERMINATION_UNCONFIRMED`) nunca fazem nova transição. Dois deadlines independentes se aplicam: um teto **absoluto** sobre o tempo total, e um teto **de inatividade** opcional que reseta a cada chunk de stdout/stderr/PTY observado — o teto absoluto sempre vence quando os dois expiram ao mesmo tempo.

Em timeout ou cancelamento, o `bridge.py` envia `SIGTERM` ao grupo de processos, espera uma janela de graça (padrão 3s) e escala para `SIGKILL`, só reportando a tentativa como `CANCELLED`/`TIMED_OUT` depois que o processo (e, quando havia `pgid`, o grupo inteiro) é confirmado positivamente como vazio. Se essa confirmação não puder ser obtida, o estado é `TERMINATION_UNCONFIRMED` — é isso que bloqueia o fallback automático e a liberação do lease de workspace (ver abaixo). Em POSIX, `start_new_session=True` faz o processo lançado virar líder do próprio grupo, então `killpg` alcança os descendentes a menos que algum escape via seu próprio `setsid()`/`setpgid()`. No Windows não há sinalização de grupo de processos: só o filho direto é controlado, e confirmação em nível de árvore nunca é alegada lá — ver [Suporte por plataforma](#suporte-por-plataforma).

O `mcp_server.py` registra um `execution_id` por chamada de `run_combo`/`ask_provider` (as duas ferramentas de execução longa) e transmite atualizações de tentativa para o `execution_registry.py`, de onde `get_execution`, `list_executions` e `cancel_execution` leem. `cancel_execution` é idempotente e funciona por `execution_id` ou pelo `request_id` original do JSON-RPC.

## Lease de workspace

`workspace_lease.py` impede que duas tentativas contra o *mesmo diretório de trabalho canônico* rodem concorrentemente — sejam elas de duas chamadas `run_combo()` diferentes ou de um fallback/retry dentro de uma única chamada. Um lease só é transferido para uma nova tentativa depois que os metadados de execução da tentativa anterior confirmam que ela terminou com segurança; caso contrário a transferência levanta `WorkspaceLeaseError` e o chamador fica fail-closed (lease retida, nenhuma nova tentativa iniciada).

Isso é **apenas intraprocesso**: o lease vive na memória de um único processo Python (`threading.Lock` + dict). Não coordena entre múltiplos processos do Athena, pools de workers ou máquinas compartilhando o mesmo filesystem — isso está fora do escopo da implementação atual.

## Fronteira opcional com Moiras

`athena/moiras_adapter.py` é a única fronteira implementada com o projeto independente [Moiras](https://github.com/JoaoPauloNA/moiras). Ela não está ligada ao servidor MCP e permanece desativada até um embedding construir `MoirasShadowObserver(enabled=True)`. O observador pode ocupar o callback `on_execution_update` existente quando esse callback estiver livre; o último resultado fica apenas em memória.

Só cruzam a fronteira IDs de execução/tentativa, estado Athena mapeado para enum Moiras, contadores sintéticos, perfil fixo `athena-shadow`, timestamps e booleanos explícitos de espera/bloqueio. Provider, prompt, output, comando, path, host, usuário, PID e PGID não cruzam. O adaptador não recebe ação, conselho ou modelo, e fixa `affects_control_flow=false`, `executed=false`, `mode=shadow`. Ausência do pacote, incompatibilidade de schema ou comparação rejeitada não relaxa lifecycle, fallback ou lease determinísticos do Athena.

## Segurança de fallback / retry

`router.run_combo()` não faz fallback para o próximo provider (nem retry) a menos que ambos sejam verdadeiros: (1) o `service_profile` ativo permite fallback para aquele tipo de falha (`allow_fallback_on_error` / `allow_fallback_on_timeout`), e (2) os metadados de execução da tentativa anterior confirmam positivamente a terminação. Uma tentativa `client_abandoned`, uma sessão remota (SSH) sem confirmação de terminação remota, ou qualquer estado não-terminal bloqueia o fallback e levanta `FallbackBlocked`. Os perfis `authenticated_external` e `unknown` definem as duas flags de fallback como `false` incondicionalmente, então ações autenticadas ou não classificadas nunca são repetidas automaticamente.

## Execução remota via SSH

`ask_provider(..., ssh_host=...)` roda a CLI numa máquina remota via o construtor de comando do `ssh.py` (validação de host, sem interpolação de shell do conteúdo do prompt, autenticação só por chave — o Athena nunca aceita ou armazena senha). Como o processo roda em outra máquina, **o Athena não consegue confirmar diretamente que o processo remoto de fato terminou**: `ExecutionRecord.remote_termination_confirmed` fica `False`/não definido a menos que um caminho explícito de confirmação o defina, então um timeout ou cancelamento via SSH resolve para `TERMINATION_UNCONFIRMED` em vez de `CANCELLED`/`TIMED_OUT` — o que bloqueia fallback e liberação de lease do mesmo jeito que um processo local travado bloquearia.

## Suporte por plataforma

A *detecção* de CLIs roda em macOS, Windows e Linux (ver abaixo). A limpeza do ciclo de vida do grupo de processos é exercitada pela suíte atual **só em POSIX (macOS/Linux)**, para descendentes que permanecem no grupo controlado; um descendente que escape com `setsid()`/`setpgid()` não pode ser contabilizado positivamente. O `harness/p0_gate.py` registra o escopo local: `posix` é classificado `LOCAL_CONTROLLED_ONLY`, enquanto `windows` é `NOT_GUARANTEED` (o Athena controla o filho direto no Windows via `CREATE_NEW_PROCESS_GROUP`, mas não alega limpar a árvore mais ampla).

## Detecção de CLIs (cross-platform)

1. **PATH enriquecido** — PATH do processo + locais conhecidos por SO:
   - **Windows:** `~/.local/bin`, `%LOCALAPPDATA%\Programs`, npm global, WinGet Links, Scoop shims, Chocolatey, Scripts do pip `--user`, cargo, go
   - **macOS:** `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, npm global, cargo, go, bun
   - **Linux:** `~/.local/bin`, `/usr/local/bin`, snap, exports do flatpak, npm global, cargo, go, bun
2. **Heurística de nome** — match de palavra inteira por palavras-chave (removendo extensões do Windows: `claude.cmd` → `claude`)
3. **Probe** — saída de `--help`/`--version` pontuada por palavras-chave de IA (shims `.cmd`/`.bat` rodam via `cmd /c`)
4. **Catálogo fixo** — 22 CLIs conhecidas sempre listadas; as não instaladas aparecem como *Offline* no dashboard

## Arquivos de dados (`~/.athena/`)

| Arquivo | Conteúdo |
|---|---|
| `models_catalog.json` | Listas vivas de modelos por CLI (TTL de 5 dias) |
| `model_ratings.json` | Notas por função, cache local com TTL de 7 dias e fallback de seed (ver [Notas](#módulos)) |
| `combos.json` | Cadeias de failover |
| `usage.json` | Contadores de chamadas, durações, tokens estimados |
| `custom_providers.json` | Providers definidos pelo usuário (sobrescrevem a auto-descoberta) |
| `verdicts.json` | Últimos 500 episódios de verificação, redigidos (sem prompts/relatórios completos), alimentando o `list_reliability` |

Tudo configurável via `ATHENA_DATA_DIR`, `ATHENA_MODELS_FILE` etc. (ver `athena/config.py`).

## Princípios de design

1. **O contexto do orquestrador é sagrado** — executores devolvem relatórios enxutos de 10 tópicos, nunca dumps de código.
2. **Confie, mas verifique o que é checável** — relatórios são conferidos contra evidências objetivas do projeto para as alegações específicas que admitem checagem; isso não é prova geral de correção.
3. **Barato antes de caro** — modelos grátis/locais para verificação; pesados só quando a complexidade justifica (aviso, nunca bloqueio).
4. **Fail closed na incerteza de ciclo de vida** — fallback, transferência/liberação de lease e verificação todos se recusam a prosseguir quando a terminação de uma tentativa anterior não está positivamente confirmada.
5. **Funciona com o que você tem** — toda decisão de roteamento é computada a partir das CLIs realmente instaladas na máquina.

# Referência das Ferramentas MCP

🇺🇸 [EN](../en/mcp-tools.md) · 🇨🇳 [中文](../zh-CN/mcp-tools.md)

O servidor fala MCP via stdio (`python -m athena.mcp_server`). Todas as respostas são JSON dentro de um bloco de conteúdo de texto.

## `list_providers`

Lista todas as CLIs registradas: disponibilidade, caminho do binário resolvido, papel padrão, catálogo vivo de modelos, modelo padrão recomendado, notas de rating.

**Entrada:** `{}`

## `ask_provider`

Envia uma tarefa a um provider específico. O prompt é envolvido no contrato de relatório de 10 tópicos.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `provider` | string | ✅ | `codex`, `agent`, `claude`, `agy`, `openclaude`, `opencode`, `ollama` |
| `prompt` | string | ✅ | A tarefa |
| `model` | string | | Id do modelo (cai no default recomendado do provider) |
| `working_directory` | string | | Diretório do projeto para o executor |
| `timeout` | integer | | Segundos (default do provider se omitido) |
| `skip_permissions` | boolean | | Passa a flag de sem-confirmação da CLI |
| `verify` | boolean | | Liga o **detector de mentiras** (ver abaixo) |

**Com `verify=true`:** após a execução, o verificador mais barato disponível (modelos free do OpenCode primeiro, nunca o provider do executor) confere o relatório com evidências do git e arquivos citados. Relatório FALSO volta ao executor uma vez com os motivos; um segundo FALSO retorna `verdict.escalado=true` para o orquestrador decidir (trocar de CLI, dividir a tarefa, abortar).

**Extras na resposta:** `report_format_ok`, `warnings[]` (incluindo o aviso econômico de modelo pesado para tarefa simples), `verdict` (quando verificado).

## `run_combo`

Executa um prompt através da cadeia de failover de um combo.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `prompt` | string | ✅ | A tarefa |
| `combo_id` | string | | Padrão: `default` |
| `working_directory` | string | | |
| `timeout` | integer | | Override por etapa |

Em timeout ou erro, a próxima etapa da cadeia é tentada, conforme a política de failover do combo.

## `deliberate`

Consulta vários agentes em paralelo e devolve todas as respostas.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `prompt` | string | ✅ | |
| `providers` | string[] | | Padrão: `["agent", "agy", "claude"]` |

## `recommend`

"Quem devo chamar?" Combina a tabela de notas (atualizada semanalmente) com o que está realmente instalado.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `task` | string | ✅ | Descrição da tarefa em linguagem natural |
| `task_type` | string | | Força uma função: `frontend`, `backend`, `raciocinio`, `rapidez` |
| `top_n` | integer | | Número de recomendações (padrão 3) |
| `only_installed` | boolean | | Só sugerir modelos instalados (padrão true) |

**Resposta:** função(ões) detectada(s), complexidade estimada (`simple|medium|complex`), recomendações rankeadas (`provider` + `model_id` prontos para o `ask_provider`), nota de economia listando os modelos pesados excluídos e uma `dica` pronta para uso.

## `refresh_models`

Re-escaneia os catálogos de modelos das CLIs (`--list-models`, `opencode models`, …) e regrava o cache.

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `force` | boolean | Ignora o TTL (padrão true) |

## `list_usage`

Contadores por provider: chamadas, duração total, tokens estimados, último uso.

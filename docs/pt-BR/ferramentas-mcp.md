# Referência das Ferramentas MCP

🇺🇸 [EN](../en/mcp-tools.md) · 🇨🇳 [中文](../zh-CN/mcp-tools.md) *(tradução da comunidade, não sincronizada — apenas informativa)*

O servidor fala MCP via stdio (`python -m athena.mcp_server`). Todas as respostas são JSON dentro de um bloco de conteúdo de texto. São **12 ferramentas**.

## Ferramentas de execução longa e rastreamento

`run_combo` e `ask_provider` são as duas ferramentas de execução longa. Em cada chamada o servidor gera automaticamente um `execution_id` (ou aceita um que você passe em `arguments.execution_id`, como string não vazia) e transmite atualizações de ciclo de vida para um registro em memória conforme a chamada progride. Esse `execution_id` volta no nível superior da resposta e pode ser usado com `get_execution`, `list_executions` e `cancel_execution`. O registro é limitado (256 execuções / 64 tentativas cada por padrão) e sanitizado — sem prompts, relatórios ou credenciais, só metadados de identidade/timing/estado (ver [Arquitetura — Ciclo de vida da execução](arquitetura.md#ciclo-de-vida-da-execução)).

Se o pacote Moiras compatível separado estiver instalado e o servidor iniciar com `ATHENA_MOIRAS_SHADOW=1` (também aceita `true`, `yes`, `on`), as atualizações de lifecycle também são enviadas a um sampler sombra limitado e coalescido. Esse opt-in é apenas observacional e não afeta timeout, cancelamento, fallback, lease ou autorização de nenhuma ferramenta.

Parâmetros numéricos de timeout (`timeout`, `overall_timeout`, `verification_timeout`, `idle_timeout`) devem ser números reais maiores que 0 (booleanos são rejeitados); `idle_timeout` não pode exceder o timeout absoluto efetivo em vigor para a chamada.

## `list_providers`

Lista todas as CLIs registradas: disponibilidade, caminho do binário resolvido, papel padrão, catálogo vivo de modelos, modelo padrão recomendado, notas de rating.

**Entrada:** `{}`

## `list_combos`

Lista os combos configurados com suas cadeias de failover.

**Entrada:** `{}`

## `run_combo`

Executa um prompt através da cadeia condicional de failover de um combo.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `prompt` | string | ✅ | A tarefa |
| `combo_id` | string | | Padrão: `default` |
| `working_directory` | string | | Obrigatório se o `service_profile` resolvido exigir workspace |
| `timeout` | integer | | Override por etapa (segundos) |
| `verification_timeout` | number | | Teto para a fase de verificação, se `verify=true` |
| `overall_timeout` | number | | Deadline para a execução inteira do combo em todas as etapas; se esgotado antes de um próximo estágio seguro, levanta `ComboDeadlineExceeded` |
| `verify` | boolean | | Checa o relatório de cada provider (ver abaixo); relatório marcado FALSO conta como falha e aciona failover para o próximo provider |
| `task_type` | string | | `frontend`, `backend`, `raciocinio`, `rapidez` — função explícita (detectada do prompt se omitida) |
| `service_profile` | string | | Um dos ids de perfil em [Arquitetura — Módulos](arquitetura.md#módulos) (`text_generation`, `code_agent`, `build_test`, `research`, `local_model`, `verification`, `workspace_mutation`, `authenticated_external`, `unknown`) |
| `idle_timeout` | number | | Segundos sem saída observável antes da etapa ser tratada como travada |
| `execution_id` | string | | Reusa um execution_id específico em vez do gerado automaticamente |

Em timeout ou erro a próxima etapa da cadeia é tentada, mas só quando a política de failover do combo e o service_profile resolvido permitem para aquele tipo de falha, **e** a terminação da tentativa anterior está confirmada positivamente — caso contrário a chamada levanta `FallbackBlocked` em vez de continuar silenciosamente (ver [Arquitetura — Segurança de fallback / retry](arquitetura.md#segurança-de-fallback--retry)). Com `verify=true`, um veredito indeterminado é aceito com aviso (não conta como falha); uma fase de verificação que não consegue confirmar a própria terminação também bloqueia a execução em vez de arriscar um palpite.

## `ask_provider`

Envia uma tarefa a um provider específico. O prompt é envolvido no contrato de relatório de 10 tópicos.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `provider` | string | ✅ | `codex`, `agent`, `claude`, `agy`, `openclaude`, `opencode`, `ollama` |
| `prompt` | string | ✅ | A tarefa |
| `model` | string | | Id do modelo (cai no default recomendado do provider) |
| `working_directory` | string | | Obrigatório se o `service_profile` resolvido exigir workspace |
| `timeout` | integer | | Segundos (default do provider/perfil se omitido) |
| `skip_permissions` | boolean | | Passa a flag de sem-confirmação da CLI — ver [Segurança e validação](../../README.pt-BR.md#segurança-e-validação) |
| `verify` | boolean | | Liga o verificador de relatórios (ver abaixo) |
| `task_type` | string | | `frontend`, `backend`, `raciocinio`, `rapidez` — função explícita (detectada se omitida) |
| `service_profile` | string | | Mesmos ids de perfil do `run_combo` |
| `idle_timeout` | number | | Segundos sem saída observável antes da chamada ser tratada como travada |
| `execution_id` | string | | Reusa um execution_id específico em vez do gerado automaticamente |

**Com `verify=true`:** após a execução, o relatório é checado primeiro pela camada determinística (re-executa comandos de teste/lint de uma whitelist que o relatório alega terem passado, checa arquivos citados) e, só se isso for inconclusivo, por um modelo barato advisory (tier grátis primeiro, nunca o próprio provider do executor). Um veredito FALSO volta ao executor uma vez com os motivos; se a nova tentativa também for marcada FALSA, a resposta traz `verdict.escalado=true` para o orquestrador decidir (trocar de CLI, dividir a tarefa, abortar). Se o service_profile não permitir retry corretivo automático (ex.: `authenticated_external`), o primeiro FALSO já escala direto. Nenhuma das camadas de verificação prova correção geral — ver [Verificação](verificacao.md).

**Extras na resposta:** `report_format_ok`, `warnings[]` (incluindo o aviso econômico de modelo pesado para tarefa simples), `verdict` (quando verificado), `execution` (metadados de ciclo de vida da tentativa).

## `deliberate`

Consulta vários agentes em paralelo e devolve todas as respostas. **Não** roda nenhuma etapa de verificação.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `prompt` | string | ✅ | |
| `providers` | string[] | | Padrão: `["agent", "agy", "claude"]` |

## `recommend`

"Quem devo chamar?" Combina o cache local de notas com o que está realmente instalado.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `task` | string | ✅ | Descrição da tarefa em linguagem natural |
| `task_type` | string | | Força uma função: `frontend`, `backend`, `raciocinio`, `rapidez` |
| `top_n` | integer | | Número de recomendações (padrão 3) |
| `only_installed` | boolean | | Só sugerir modelos instalados (padrão true) |

**Resposta:** função(ões) detectada(s), complexidade estimada (`simple|medium|complex`), recomendações rankeadas (`provider` + `model_id` prontos para o `ask_provider`), nota de economia listando os modelos pesados excluídos e uma `dica` pronta para uso.

## `refresh_models`

Re-escaneia os catálogos de modelos das CLIs (`--list-models`, `opencode models`, …) e regrava o cache local.

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `force` | boolean | Ignora o TTL (padrão true) |

## `list_usage`

Contadores por provider: chamadas, duração total, tokens estimados, último uso.

**Entrada:** `{}`

## `list_reliability`

Ranking local de claimed vs verified por CLI, a partir dos vereditos persistidos (redigidos) em `~/.athena/verdicts.json`: quantas vezes cada CLI declarou "pronto" e isso se confirmou, taxa de relatórios falsos e escaladas.

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `limit` | integer | Episódios recentes a incluir em `ultimos_episodios` (padrão 20) |

## `get_execution`

Consulta uma execução registrada por `execution_id` ou `request_id` (o id JSON-RPC original do `tools/call` que a iniciou).

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `execution_id` | string | |
| `request_id` | string \| number | |

**Resposta:** `{"execution": null}` se não encontrada, senão o registro de execução sanitizado (estado, tentativas, timestamps — sem prompts/relatórios). Com `ATHENA_MOIRAS_SHADOW` ativo, o registro também contém `execution.moiras_shadow` para a tentativa atual: `null` enquanto não houver amostra pronta ou um advisory com `status`, `reason`, `classification`, `evidence_codes`, `affects_control_flow=false`, `executed=false` e `mode="shadow"`. O valor é assíncrono, intraprocesso e não persistido; `list_executions` não recebe esse enriquecimento.

O adaptador aceita Moiras `0.1.x` / schema `1.0`. Dados normais de lifecycle do Athena podem produzir hoje `REAL_PROGRESS`, `ACTIVITY_WITHOUT_PROGRESS`, `PROBABLE_INACTIVITY` e `INDETERMINATE`. `LEGITIMATE_WAIT` e `EXTERNAL_BLOCK` exigem flags explícitas de espera/bloqueio que o lifecycle padrão do Athena ainda não produz.

## `list_executions`

Lista execuções registradas recentemente, somente leitura e sanitizado, mais recentemente atualizadas primeiro.

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `limit` | integer | Limitado a `1..100` (padrão 20) |

## `cancel_execution`

Solicita cancelamento idempotente de uma execução por `execution_id` ou `request_id`. Chamar de novo numa execução já finalizada ou já em cancelamento é um no-op seguro.

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `execution_id` | string | |
| `request_id` | string \| number | |
| `reason` | string | Normalizado para um pequeno conjunto de motivos seguros; qualquer outro valor é registrado como `user_requested` |

**Resposta:** `{"found": bool, "requested": bool, "execution_id": ...}` — `requested=false` com `found=true` significa que a execução já estava finalizada ou um cancelamento já estava em andamento.

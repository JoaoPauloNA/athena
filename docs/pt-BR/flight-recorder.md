# Athena Flight Recorder (Fase 1)

Log forense local, append-only, para cada tentativa de execução de provider.

## Localização

Por padrão os dados ficam em `~/.athena/logs/`. Use `ATHENA_LOGS_DIR` para outro diretório.

## Estrutura

```
logs/
  YYYY-MM-DD/
    events.jsonl          # eventos JSONL append-only do dia
  artifacts/
  <execution_id>/
    <attempt_id>/
      stdout.txt          # stdout completo da tentativa
      stderr.txt          # stderr completo da tentativa
```

## Eventos

Cada linha em `events.jsonl` é um objeto JSON com, no mínimo:

- `timestamp_utc`, `event_type`, `execution_id`, `attempt_id`
- `provider`, `profile`, `transport`
- Para `attempt_started`: `command` (sanitizado), `cwd`, `env_var_names` (somente nomes)
- Para `state_transition`: `from_state`, `to_state`, `reason`, `pid`/`pgid` quando houver
- Para `attempt_terminal`: `state`, `exit_code`, `duration_s`, hashes e tamanhos de stdout/stderr, caminhos dos artefatos, `error`/`timed_out` quando aplicável

Tipos principais: `attempt_started`, `state_transition`, `attempt_terminal`.

## Privacidade

O Flight Recorder **nunca** grava valores de API keys, tokens, cookies, cabeçalhos `Authorization`/`Bearer` nem valores de variáveis de ambiente sensíveis. Comandos são sanitizados; o log registra apenas **nomes** de variáveis de ambiente.

Falhas ao escrever log são **não-fatais** para a execução; avisos podem aparecer em `warnings` do resultado.

## Correlação

- `execution_id`: uma solicitação lógica (pode ter várias tentativas).
- `attempt_id`: uma tentativa concreta (retry/fallback gera novo `attempt_id`).

Para reconstruir uma tentativa:

1. Filtrar `events.jsonl` por `execution_id` e `attempt_id`.
2. Ler `artifacts/<execution_id>/<attempt_id>/stdout.txt` e `stderr.txt`.

## Limitações (Fase 1)

- Sem índice global nem painel de consulta — virão em fases posteriores.
- Sem política de retenção ou limpeza automática nesta fase.
- Instrumentação focada no transporte do bridge (`run_subprocess` / `run_with_pty`).

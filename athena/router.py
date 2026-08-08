"""Router do Athena-MCP: lógica de failover entre providers via combos."""
from __future__ import annotations

import time

from athena.bridge import RunResult
from athena.combos import get_combo
from athena.providers import ask_provider


class AllProvidersFailed(Exception):
    """Todos os providers do combo falharam."""


def run_combo(
    combo_id: str,
    prompt: str,
    *,
    working_directory: str | None = None,
    timeout: int | None = None,
    heavy_model_authorized: bool = False,
    task_type: str | None = None,
) -> RunResult:
    """Executa um prompt através de um combo, com failover automático.

    Percorre a chain do combo em ordem. Se um provider falha (timeout, erro),
    tenta o próximo da lista conforme a política de failover.
    """
    combo = get_combo(combo_id)
    if combo is None:
        raise ValueError(f"Combo '{combo_id}' não encontrado")
    if not combo.enabled:
        raise ValueError(f"Combo '{combo_id}' está desabilitado")
    if not combo.chain:
        raise ValueError(f"Combo '{combo_id}' não tem providers configurados")

    start_time = time.monotonic()
    last_error: str | None = None
    attempted: list[str] = []

    for step in combo.chain:
        provider_id = step.provider_id
        attempted.append(provider_id)

        effective_timeout = timeout or step.timeout

        for attempt in range(combo.failover_policy.max_retries_per_provider):
            result = ask_provider(
                provider_id,
                prompt,
                role=step.role,
                use_default_role=False,
                model=step.model,
                working_directory=working_directory,
                timeout=effective_timeout,
                skip_permissions=step.skip_permissions,
                extra_args=step.extra_args,
                heavy_model_authorized=heavy_model_authorized,
                task_type=task_type,
            )

            # Sucesso
            if result.exit_code == 0 and not result.timed_out:
                result.duration_s = time.monotonic() - start_time
                return result

            # Timeout — verifica se deve fazer failover
            if result.timed_out:
                last_error = f"Timeout ({effective_timeout}s)"
                if not combo.failover_policy.on_timeout:
                    # Não faz failover em timeout — retorna o erro
                    result.duration_s = time.monotonic() - start_time
                    return result
                break  # Vai para próximo provider

            # Erro de execução (exit_code != 0)
            if result.exit_code != 0:
                last_error = result.error or f"Exit code {result.exit_code}"
                if not combo.failover_policy.on_error:
                    result.duration_s = time.monotonic() - start_time
                    return result
                break  # Vai para próximo provider

            # Retry delay (se não for última tentativa)
            if attempt < combo.failover_policy.max_retries_per_provider - 1:
                time.sleep(combo.failover_policy.retry_delay_seconds)

    # Todos os providers falharam
    error_msg = (
        f"Combo '{combo_id}' esgotou todos os {len(combo.chain)} providers. "
        f"Tentados: {', '.join(attempted)}. "
        f"Último erro: {last_error or 'desconhecido'} "
        f"(duração total: {time.monotonic() - start_time:.1f}s)."
    )
    raise AllProvidersFailed(error_msg)


def test_combo(combo_id: str) -> list[dict]:
    """Testa um combo pingando cada provider com um prompt mínimo.

    Retorna lista de resultados por provider (sem lançar exceção).
    """
    combo = get_combo(combo_id)
    if combo is None:
        return [{"provider_id": None, "status": "not_found", "error": f"Combo '{combo_id}' não encontrado"}]

    results = []
    test_prompt = "Responda apenas com a palavra 'OK'."

    for step in combo.chain:
        try:
            result = ask_provider(
                step.provider_id,
                test_prompt,
                role=None,
                use_default_role=False,
                model=step.model,
                timeout=min(step.timeout or 30, 30),
                skip_permissions=True,
            )
            results.append({
                "provider_id": step.provider_id,
                "status": "ok" if result.exit_code == 0 else "error",
                "exit_code": result.exit_code,
                "duration_s": round(result.duration_s, 2),
                "error": result.error,
            })
        except Exception as exc:
            results.append({
                "provider_id": step.provider_id,
                "status": "exception",
                "error": str(exc),
            })

    return results

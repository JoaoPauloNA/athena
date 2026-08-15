"""Servidor MCP do Athena-MCP."""
from __future__ import annotations

import json
import math
import os
import sys
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any

from athena import __version__
from athena.combos import ensure_default_combo, get_combo, list_combos
from athena.execution import ExecutionControl, normalize_cancel_reason
from athena.execution_registry import ExecutionRegistry
from athena.models import ensure_models_fresh, refresh_model_catalog
from athena.moiras_adapter import MoirasShadowObserver
from athena.providers import (
    PROVIDERS,
    ask_provider,
    ask_provider_verified,
    deliberate,
    list_providers,
)
from athena.router import run_combo
from athena.service_profiles import (
    SERVICE_PROFILE_IDS,
    resolve_service_profile,
    resolve_timeouts,
)
from athena.usage import get_usage

ToolHandler = Callable[[dict[str, Any]], Any]
STDOUT_LOCK = Lock()
EXECUTION_REGISTRY = ExecutionRegistry()
LONG_RUNNING_TOOLS = {"run_combo", "ask_provider"}


def _moiras_shadow_enabled_from_env() -> bool:
    return os.environ.get("ATHENA_MOIRAS_SHADOW", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


MOIRAS_SHADOW_OBSERVER = MoirasShadowObserver(
    enabled=_moiras_shadow_enabled_from_env(),
    background_sampling=True,
)


def _is_real_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_positive_timeout_arg(arguments: dict[str, Any], key: str) -> None:
    if key not in arguments or arguments.get(key) is None:
        return
    value = arguments.get(key)
    if (
        not _is_real_number(value)
        or float(value) <= 0
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{key} inválido: deve ser número > 0 (bool não permitido)")


def _validate_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> None:
    if tool_name == "run_combo":
        for key in ("timeout", "overall_timeout", "verification_timeout"):
            _validate_positive_timeout_arg(arguments, key)
    if tool_name == "ask_provider":
        _validate_positive_timeout_arg(arguments, "timeout")
    if "idle_timeout" in arguments and arguments.get("idle_timeout") is not None:
        _validate_positive_timeout_arg(arguments, "idle_timeout")
    if "service_profile" in arguments and arguments.get("service_profile") is not None:
        if arguments.get("service_profile") not in SERVICE_PROFILE_IDS:
            raise ValueError(
                f"service_profile inválido: {arguments.get('service_profile')}"
            )


def _text_content(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


def _handle_list_providers(_arguments: dict[str, Any]) -> dict:
    ensure_models_fresh()
    return _text_content({"providers": list_providers()})


def _handle_list_combos(_arguments: dict[str, Any]) -> dict:
    ensure_default_combo()
    return _text_content({"combos": [c.to_dict() for c in list_combos()]})


def _handle_run_combo(arguments: dict[str, Any]) -> dict:
    combo_id = arguments.get("combo_id", "default")
    prompt = arguments["prompt"]
    execution_id = arguments.get("execution_id")
    on_execution_update = arguments.get("on_execution_update")
    execution_control = arguments.get("execution_control")
    resolved_profile_id = arguments.get("service_profile")
    combo = get_combo(combo_id)
    if combo is not None and combo.chain:
        primary_provider_id = combo.chain[0].provider_id
        profile = resolve_service_profile(
            explicit_profile_id=arguments.get("service_profile"),
            provider_id=primary_provider_id,
            task_type=arguments.get("task_type"),
            working_directory=arguments.get("working_directory"),
        )
        if profile.requires_workspace and not arguments.get("working_directory"):
            raise ValueError(f"service_profile '{profile.id}' exige working_directory")
        provider_spec = PROVIDERS.get(primary_provider_id)
        provider_default_timeout = (
            provider_spec.default_timeout if provider_spec is not None else None
        )
        resolve_timeouts(
            profile=profile,
            provider_default_absolute_timeout_s=provider_default_timeout,
            explicit_absolute_timeout_s=arguments.get("timeout"),
            explicit_idle_timeout_s=arguments.get("idle_timeout"),
        )
        resolved_profile_id = profile.id
    result = run_combo(
        combo_id,
        prompt,
        working_directory=arguments.get("working_directory"),
        timeout=arguments.get("timeout"),
        verification_timeout=arguments.get("verification_timeout"),
        overall_timeout=arguments.get("overall_timeout"),
        verify=bool(arguments.get("verify", False)),
        task_type=arguments.get("task_type"),
        service_profile=resolved_profile_id,
        idle_timeout=arguments.get("idle_timeout"),
        execution_id=execution_id,
        on_execution_update=on_execution_update,
        execution_control=execution_control,
    )
    return _text_content({
        "combo_id": combo_id,
        "execution_id": execution_id,
        "result": result.to_dict(),
        "success": result.exit_code == 0,
    })


def _handle_ask_provider(arguments: dict[str, Any]) -> dict:
    fn = ask_provider_verified if arguments.get("verify") else ask_provider
    execution_id = arguments.get("execution_id")
    on_execution_update = arguments.get("on_execution_update")
    execution_control = arguments.get("execution_control")
    provider_id = arguments["provider"]
    profile = resolve_service_profile(
        explicit_profile_id=arguments.get("service_profile"),
        provider_id=provider_id,
        task_type=arguments.get("task_type"),
        working_directory=arguments.get("working_directory"),
    )
    if profile.requires_workspace and not arguments.get("working_directory"):
        raise ValueError(f"service_profile '{profile.id}' exige working_directory")
    provider_spec = PROVIDERS.get(provider_id)
    provider_default_timeout = (
        provider_spec.default_timeout if provider_spec is not None else 300
    )
    result_timeout, _ = resolve_timeouts(
        profile=profile,
        provider_default_absolute_timeout_s=provider_default_timeout,
        explicit_absolute_timeout_s=arguments.get("timeout"),
        explicit_idle_timeout_s=arguments.get("idle_timeout"),
    )
    if result_timeout is None:
        raise ValueError(f"service_profile '{profile.id}' sem timeout efetivo")
    result = fn(
        provider_id,
        arguments["prompt"],
        model=arguments.get("model"),
        working_directory=arguments.get("working_directory"),
        timeout=arguments.get("timeout"),
        skip_permissions=bool(arguments.get("skip_permissions", False)),
        task_type=arguments.get("task_type"),
        service_profile=arguments.get("service_profile"),
        idle_timeout=arguments.get("idle_timeout"),
        execution_id=execution_id,
        on_execution_update=on_execution_update,
        execution_control=execution_control,
    )
    return _text_content({"execution_id": execution_id, "result": result.to_dict()})


def _handle_deliberate(arguments: dict[str, Any]) -> dict:
    results = deliberate(
        arguments["prompt"],
        arguments.get("providers", ["agent", "agy", "claude"]),
    )
    return _text_content({
        "prompt": arguments["prompt"],
        "responses": [r.to_dict() for r in results],
    })


def _handle_list_usage(_arguments: dict[str, Any]) -> dict:
    return _text_content({"usage": get_usage()})


def _handle_refresh_models(arguments: dict[str, Any]) -> dict:
    force = bool(arguments.get("force", True))
    payload = refresh_model_catalog(force=force)
    return _text_content({
        "updated_at": payload.get("updated_at"),
        "message": "Catálogo de modelos atualizado.",
    })


def _handle_recommend(arguments: dict[str, Any]) -> dict:
    from athena.recommend import recommend_for_task
    payload = recommend_for_task(
        arguments["task"],
        task_type=arguments.get("task_type"),
        top_n=int(arguments.get("top_n", 3)),
        only_installed=bool(arguments.get("only_installed", True)),
    )
    return _text_content(payload)


def _handle_list_reliability(arguments: dict[str, Any]) -> dict:
    from athena.reliability import list_verdicts, reliability_report
    payload = {
        "ranking": reliability_report(),
        "ultimos_episodios": list_verdicts(limit=int(arguments.get("limit", 20))),
    }
    return _text_content(payload)


def _handle_get_execution(arguments: dict[str, Any]) -> dict:
    payload = EXECUTION_REGISTRY.get(
        execution_id=arguments.get("execution_id"),
        request_id=arguments.get("request_id"),
    )
    if payload is None:
        return _text_content({"execution": None})
    attempt_id = payload.get("current_attempt_id")
    if MOIRAS_SHADOW_OBSERVER.enabled and isinstance(attempt_id, str):
        advisory = MOIRAS_SHADOW_OBSERVER.advisory_for(
            str(payload.get("execution_id")),
            attempt_id,
        )
        payload["moiras_shadow"] = advisory.to_dict() if advisory is not None else None
    return _text_content({"execution": payload})


def _handle_list_executions(arguments: dict[str, Any]) -> dict:
    limit = int(arguments.get("limit", 20))
    limit = max(1, min(limit, 100))
    return _text_content({"executions": EXECUTION_REGISTRY.list(limit=limit)})


def _handle_cancel_execution(arguments: dict[str, Any]) -> dict:
    reason = normalize_cancel_reason(arguments.get("reason"))
    result = EXECUTION_REGISTRY.request_cancel(
        execution_id=arguments.get("execution_id"),
        request_id=arguments.get("request_id"),
        reason=reason,
    )
    return _text_content(
        {
            "found": bool(result["found"]),
            "requested": bool(result["requested"]),
            "execution_id": result["execution_id"],
        }
    )


TOOLS: list[dict] = [
    {
        "name": "list_providers",
        "description": "Lista CLIs registradas, disponibilidade no PATH e catálogo de modelos.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_combos",
        "description": "Lista combos disponíveis com suas chains de failover.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_combo",
        "description": "Executa um prompt através de um combo Athena-MCP com fallback condicional. Com verify=true, o relatório de cada provider é checado; relatório FALSO pode acionar fallback quando a política e o término seguro permitem.",
        "inputSchema": {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "combo_id": {"type": "string", "description": "ID do combo (padrão: 'default')"},
                "prompt": {"type": "string", "description": "Prompt a enviar"},
                "working_directory": {"type": "string"},
                "timeout": {"type": "integer"},
                "verification_timeout": {"type": "number"},
                "overall_timeout": {"type": "number"},
                "verify": {"type": "boolean", "description": "Checa o relatório de cada provider; FALSO aciona failover"},
                "task_type": {"type": "string", "enum": ["frontend", "backend", "raciocinio", "rapidez"], "description": "Função explícita (opcional; se omitida, detectada pela descrição)"},
                "service_profile": {"type": "string", "enum": list(SERVICE_PROFILE_IDS)},
                "idle_timeout": {"type": "number", "exclusiveMinimum": 0},
                "execution_id": {"type": "string"},
            },
        },
    },
    {
        "name": "ask_provider",
        "description": "Envia um prompt diretamente a um provider específico. Com verify=true, o pipeline determinístico/advisory checa alegações verificáveis; um resultado FALSO pode gerar uma correção condicionada pela política ou escalar.",
        "inputSchema": {
            "type": "object",
            "required": ["provider", "prompt"],
            "properties": {
                "provider": {"type": "string", "enum": ["codex", "agent", "claude", "agy", "openclaude", "opencode", "ollama"]},
                "prompt": {"type": "string"},
                "model": {"type": "string"},
                "working_directory": {"type": "string"},
                "timeout": {"type": "integer"},
                "skip_permissions": {"type": "boolean"},
                "verify": {"type": "boolean", "description": "Ativa a verificação determinística/advisory das alegações do relatório"},
                "task_type": {"type": "string", "enum": ["frontend", "backend", "raciocinio", "rapidez"], "description": "Função explícita (opcional; se omitida, detectada pela descrição)"},
                "service_profile": {"type": "string", "enum": list(SERVICE_PROFILE_IDS)},
                "idle_timeout": {"type": "number", "exclusiveMinimum": 0},
                "execution_id": {"type": "string"},
            },
        },
    },
    {
        "name": "deliberate",
        "description": "Consulta vários agentes em paralelo.",
        "inputSchema": {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"},
                "providers": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "list_usage",
        "description": "Contador local de uso por provider.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "refresh_models",
        "description": "Atualiza o catálogo de modelos consultando as CLIs locais.",
        "inputSchema": {
            "type": "object",
            "properties": {"force": {"type": "boolean"}},
        },
    },
    {
        "name": "recommend",
        "description": "Recomenda qual provider/modelo chamar para uma tarefa, com base na tabela de notas (frontend, backend, raciocínio, rapidez) cruzada com o que está instalado na máquina. Use ANTES de ask_provider quando não souber quem chamar.",
        "inputSchema": {
            "type": "object",
            "required": ["task"],
            "properties": {
                "task": {"type": "string", "description": "Descrição da tarefa em linguagem natural"},
                "task_type": {"type": "string", "enum": ["frontend", "backend", "raciocinio", "rapidez"], "description": "Função explícita (opcional; se omitida, detectada pela descrição)"},
                "top_n": {"type": "integer", "description": "Quantas recomendações (padrão 3)"},
                "only_installed": {"type": "boolean", "description": "Só sugerir modelos instalados (padrão true)"},
            },
        },
    },
    {
        "name": "list_reliability",
        "description": "Ranking local de confiabilidade por CLI (claimed vs verified): quantas vezes cada CLI declarou 'pronto' e era verdade, taxa de relatórios falsos e escaladas. Dados dos vereditos persistidos em ~/.athena/verdicts.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Quantos episódios recentes incluir (padrão 20)"},
            },
        },
    },
    {
        "name": "get_execution",
        "description": "Consulta execução registrada por execution_id ou request_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
                "request_id": {},
            },
        },
    },
    {
        "name": "list_executions",
        "description": "Lista execuções registradas recentemente (somente leitura e sanitizado).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "cancel_execution",
        "description": "Solicita cancelamento idempotente de uma execução por execution_id ou request_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
                "request_id": {},
                "reason": {"type": "string"},
            },
        },
    },
]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "list_providers": _handle_list_providers,
    "list_combos": _handle_list_combos,
    "run_combo": _handle_run_combo,
    "ask_provider": _handle_ask_provider,
    "deliberate": _handle_deliberate,
    "list_usage": _handle_list_usage,
    "refresh_models": _handle_refresh_models,
    "recommend": _handle_recommend,
    "list_reliability": _handle_list_reliability,
    "get_execution": _handle_get_execution,
    "list_executions": _handle_list_executions,
    "cancel_execution": _handle_cancel_execution,
}


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _log_internal_exception(context: str, exc: Exception) -> None:
    _log(f"{context}: {exc.__class__.__name__}")


def _error_response(request_id: Any | None, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _success_response(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _write_response(response: dict) -> None:
    with STDOUT_LOCK:
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


def _register_execution_if_needed(request_id: Any, params: dict[str, Any]) -> str | None:
    tool_name = params.get("name")
    if tool_name not in LONG_RUNNING_TOOLS:
        return None
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise ValueError("request_id inválido para execução longa: use str ou int (bool não permitido)")
    arguments = params.setdefault("arguments", {})
    provided_execution_id = arguments.get("execution_id")
    if provided_execution_id is None:
        execution_id = uuid.uuid4().hex
    else:
        if not isinstance(provided_execution_id, str) or provided_execution_id.strip() == "":
            raise ValueError("execution_id inválido: se fornecido, deve ser string não vazia")
        execution_id = provided_execution_id

    def _on_execution_update(attempt_snapshot: dict[str, Any]) -> None:
        EXECUTION_REGISTRY.update_attempt(execution_id, attempt_snapshot)
        # submit() only copies an allowlist into a bounded queue.  Importing
        # and calling Moiras happens on its sampler thread, never on the
        # stdout/stderr lifecycle callback.
        MOIRAS_SHADOW_OBSERVER.submit(attempt_snapshot)

    execution_control = ExecutionControl()
    EXECUTION_REGISTRY.create(
        execution_id=execution_id,
        request_id=request_id,
        tool=tool_name,
        control=execution_control,
    )
    arguments["execution_id"] = execution_id
    arguments["on_execution_update"] = _on_execution_update
    arguments["execution_control"] = execution_control
    arguments["_athena_registered_execution_id"] = execution_id
    return execution_id


def _resolve_registered_long_execution_id(
    request_id: Any,
    tool_name: Any,
    arguments: dict[str, Any],
) -> str | None:
    if tool_name not in LONG_RUNNING_TOOLS:
        return None
    candidate = arguments.get("_athena_registered_execution_id")
    if not isinstance(candidate, str) or candidate.strip() == "":
        return None
    if arguments.get("execution_id") != candidate:
        return None
    # Resolve through the registry's private raw-request-id index.  The
    # public record intentionally hashes request ids that look sensitive or
    # high-entropy, so comparing record["request_id"] with the raw JSON-RPC
    # id would make a valid registered execution impossible to finalize.
    record = EXECUTION_REGISTRY.get(request_id=request_id)
    if record is None:
        return None
    if record.get("execution_id") != candidate:
        return None
    if record.get("tool") != tool_name:
        return None
    return candidate


def _finalize_execution(execution_id: str | None, *, state: str | None = None) -> None:
    if execution_id is None:
        return
    EXECUTION_REGISTRY.finalize(execution_id, state=state)


def _required_args_for_tool(tool_name: str) -> list[str]:
    for tool in TOOLS:
        if tool.get("name") == tool_name:
            required = tool.get("inputSchema", {}).get("required", [])
            return [str(item) for item in required]
    return []


def _handle_request(message: dict) -> dict | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        try:
            ensure_models_fresh()
            ensure_default_combo()
        except Exception as exc:  # pragma: no cover - defensive log path
            _log_internal_exception("aviso: falha ao inicializar", exc)
        return _success_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "athena-mcp",
                    "version": __version__,
                    "description": "Athena-MCP: roteador com fallback condicionado para CLIs de agentes de IA.",
                },
            },
        )

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return _success_response(request_id, {})

    if method == "tools/list":
        return _success_response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        execution_id = _resolve_registered_long_execution_id(request_id, tool_name, arguments)
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return _error_response(request_id, -32601, f"Tool desconhecida: {tool_name}")
        try:
            _validate_tool_arguments(str(tool_name), arguments)
            for required_key in _required_args_for_tool(str(tool_name)):
                if required_key not in arguments:
                    raise KeyError(required_key)
            result = handler(arguments)
            _finalize_execution(execution_id)
            return _success_response(request_id, result)
        except KeyError as exc:
            _finalize_execution(execution_id, state="FAILED_PRELAUNCH")
            return _error_response(request_id, -32602, f"Argumento obrigatório ausente: {exc}")
        except ValueError as exc:
            _finalize_execution(execution_id, state="FAILED_PRELAUNCH")
            return _error_response(request_id, -32602, str(exc))
        except Exception as exc:
            _log_internal_exception(f"erro interno tools/call:{tool_name}", exc)
            current = EXECUTION_REGISTRY.get(execution_id=execution_id) if execution_id else None
            preserve_states = {"CANCELLED", "TIMED_OUT", "TERMINATION_UNCONFIRMED"}
            if current is not None and current.get("state") in preserve_states:
                _finalize_execution(execution_id, state=str(current.get("state")))
            else:
                _finalize_execution(execution_id, state="RESULT_INDETERMINATE")
            return _error_response(
                request_id,
                -32000,
                f"Erro interno ao executar a ferramenta '{tool_name}'.",
            )

    if request_id is None:
        return None

    return _error_response(request_id, -32601, f"Método não suportado: {method}")


def run_stdio_server() -> None:
    _log(f"Athena-MCP v{__version__} iniciado (stdio)")
    executor = ThreadPoolExecutor(max_workers=16)
    futures: set[Future] = set()
    futures_lock = Lock()

    def _prune_completed_futures() -> None:
        with futures_lock:
            done = {future for future in futures if future.done()}
            futures.difference_update(done)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error_response(None, -32700, f"JSON inválido: {exc}")
            _write_response(response)
            continue

        def _process(msg: dict) -> None:
            request_id = msg.get("id")
            execution_id: str | None = None
            try:
                if msg.get("method") == "tools/call":
                    msg_params = msg.get("params") or {}
                    msg_arguments = msg_params.get("arguments") or {}
                    execution_id = _resolve_registered_long_execution_id(
                        request_id,
                        msg_params.get("name"),
                        msg_arguments,
                    )
                response = _handle_request(msg)
                if response is not None:
                    _write_response(response)
            except Exception as exc:
                _log_internal_exception("_process falhou", exc)
                if request_id is not None:
                    _write_response(
                        _error_response(
                            request_id,
                            -32000,
                            "Erro interno no servidor MCP.",
                        )
                    )
                current = EXECUTION_REGISTRY.get(execution_id=execution_id) if execution_id else None
                preserve_states = {"CANCELLED", "TIMED_OUT", "TERMINATION_UNCONFIRMED"}
                if current is not None and current.get("state") in preserve_states:
                    _finalize_execution(execution_id, state=str(current.get("state")))
                else:
                    _finalize_execution(execution_id, state="RESULT_INDETERMINATE")

        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        is_long_call = (
            method == "tools/call"
            and request_id is not None
            and params.get("name") in LONG_RUNNING_TOOLS
        )
        if is_long_call:
            try:
                _register_execution_if_needed(request_id, params)
            except ValueError as exc:
                _write_response(_error_response(request_id, -32602, str(exc)))
                continue
            _prune_completed_futures()
            future = executor.submit(_process, message)
            with futures_lock:
                futures.add(future)
        else:
            _process(message)

    EXECUTION_REGISTRY.abandon_all_nonterminal(reason="client_abandoned")
    with futures_lock:
        pending_futures = list(futures)
    for future in pending_futures:
        future.result()
    executor.shutdown(wait=True)
    MOIRAS_SHADOW_OBSERVER.close()


if __name__ == "__main__":
    run_stdio_server()

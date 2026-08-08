"""Servidor MCP do Athena-MCP."""
from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable
from typing import Any

from athena import __version__
from athena.combos import ensure_default_combo, list_combos
from athena.models import ensure_models_fresh, refresh_model_catalog
from athena.providers import (
    ask_provider,
    ask_provider_verified,
    deliberate,
    list_providers,
)
from athena.router import run_combo
from athena.usage import get_usage

ToolHandler = Callable[[dict[str, Any]], Any]


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
    result = run_combo(
        combo_id,
        prompt,
        working_directory=arguments.get("working_directory"),
        timeout=arguments.get("timeout"),
    )
    return _text_content({
        "combo_id": combo_id,
        "result": result.to_dict(),
        "success": result.exit_code == 0,
    })


def _handle_ask_provider(arguments: dict[str, Any]) -> dict:
    fn = ask_provider_verified if arguments.get("verify") else ask_provider
    result = fn(
        arguments["provider"],
        arguments["prompt"],
        model=arguments.get("model"),
        working_directory=arguments.get("working_directory"),
        timeout=arguments.get("timeout"),
        skip_permissions=bool(arguments.get("skip_permissions", False)),
    )
    return _text_content(result.to_dict())


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
        "description": "Executa um prompt através de um combo Athena-MCP com failover automático.",
        "inputSchema": {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "combo_id": {"type": "string", "description": "ID do combo (padrão: 'default')"},
                "prompt": {"type": "string", "description": "Prompt a enviar"},
                "working_directory": {"type": "string"},
                "timeout": {"type": "integer"},
            },
        },
    },
    {
        "name": "ask_provider",
        "description": "Envia um prompt diretamente a um provider específico. Com verify=true, um verificador barato (escolhido automaticamente, grátis primeiro) checa o relatório contra evidências do projeto; relatório FALSO volta ao executor 1x e, se persistir, escala para o orquestrador.",
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
                "verify": {"type": "boolean", "description": "Ativa o verificador anti-mentira (modelo barato checa o relatório contra o projeto)"},
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
}


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _error_response(request_id: Any | None, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _success_response(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _handle_request(message: dict) -> dict | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        try:
            ensure_models_fresh()
            ensure_default_combo()
        except Exception as exc:
            _log(f"aviso: falha ao inicializar: {exc}")
        return _success_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "athena-mcp",
                    "version": __version__,
                    "description": "Athena-MCP: roteador com failover automático para CLIs de agentes de IA.",
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
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return _error_response(request_id, -32601, f"Tool desconhecida: {tool_name}")
        try:
            result = handler(arguments)
            return _success_response(request_id, result)
        except KeyError as exc:
            return _error_response(request_id, -32602, f"Argumento obrigatório ausente: {exc}")
        except Exception as exc:
            _log(traceback.format_exc())
            return _error_response(request_id, -32000, str(exc))

    if request_id is None:
        return None

    return _error_response(request_id, -32601, f"Método não suportado: {method}")


def run_stdio_server() -> None:
    _log(f"Athena-MCP v{__version__} iniciado (stdio)")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error_response(None, -32700, f"JSON inválido: {exc}")
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        response = _handle_request(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

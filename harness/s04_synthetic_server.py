"""Servidor MCP sintético temporário para a matriz S-04.

Mesmo schema das tools run_combo/ask_provider do Athena-MCP, mas não executa
nada: registra apenas o typeof de cada argumento recebido em contadores.
Nunca grava prompts ou valores — somente tipos e nomes de campos.

Uso: python synthetic_server.py <arquivo_saida.json>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/s04_typeof.json"

TOOLS = (
    {"name": "run_combo", "description": "sintetico",
     "inputSchema": {"type": "object", "required": ["attempts"], "properties": {
         "attempts": {"type": "array"},
         "profile": {"type": "string"},
         "overall_timeout_s": {"type": "number", "exclusiveMinimum": 0},
         "execution_id": {"type": "string"},
         "verification": {"type": "object"}}}},
    {"name": "ask_provider", "description": "sintetico",
     "inputSchema": {"type": "object", "required": ["provider_id", "attempts"], "properties": {
         "provider_id": {"type": "string"},
         "attempts": {"type": "array"},
         "profile": {"type": "string"},
         "task_type": {},
         "working_directory": {"type": ["string", "null"]},
         "overall_timeout_s": {"type": "number", "exclusiveMinimum": 0},
         "execution_id": {"type": "string"},
         "verification": {"type": "object"}}}},
    get_t := {"name": "get_execution", "description": "sintetico",
              "inputSchema": {"type": "object", "properties": {
                  "execution_id": {"type": "string"}, "request_id": {}}}},
)

def _typeof(v):
    if v is None: return "null"
    if isinstance(v, bool): return "boolean"
    if isinstance(v, int): return "integer"
    if isinstance(v, float): return "number"
    if isinstance(v, str): return "string"
    if isinstance(v, list): return "array"
    if isinstance(v, dict): return "object"
    return type(v).__name__

def _record(tool, args):
    data = json.loads(Path(OUT).read_text()) if os.path.exists(OUT) else {}
    counts = data.setdefault(tool, {})
    counts["_calls"] = counts.get("_calls", 0) + 1
    for k, v in (args or {}).items():
        key = f"{k}:{_typeof(v)}"
        counts[key] = counts.get(key, 0) + 1
        # dentro de attempts[], registrar tipos dos campos das tentativas
        if k == "attempts" and isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    for ik, iv in item.items():
                        ikey = f"attempts[].{ik}:{_typeof(iv)}"
                        counts[ikey] = counts.get(ikey, 0) + 1
                        if ik == "deadlines" and isinstance(iv, dict):
                            for dk, dv in iv.items():
                                dkey = f"attempts[].deadlines.{dk}:{_typeof(dv)}"
                                counts[dkey] = counts.get(dkey, 0) + 1
    with open(OUT, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)

def reply(req_id, result):
    print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}), flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except json.JSONDecodeError:
        continue
    method = req.get("method", "")
    rid = req.get("id")
    if method == "initialize":
        reply(rid, {"protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "athena-s04-synthetic", "version": "0.0.1"}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        reply(rid, {"tools": list(TOOLS)})
    elif method in ("tools/call",):
        params = req.get("params") or {}
        name = params.get("name", "?")
        _record(name, params.get("arguments"))
        payload = {"execution_id": None,
                   "result": {"state": "synthetic_probe", "exit_code": 0,
                              "stdout": "", "stderr": "", "duration_s": 0.0,
                              "expired_deadline": None, "error": None}}
        reply(rid, {"content": [{"type": "text", "text": json.dumps(payload)}]})
    elif method == "ping":
        reply(rid, {})

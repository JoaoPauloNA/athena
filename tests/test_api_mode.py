"""CFG-5: testes do modo api — contrato, segredo redacted, chamada real local.

A chamada real usa um servidor HTTP local efêmero (não mock de módulo):
verifica o caminho HTTP completo sem rede externa e sem custo.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from athena.api_mode import call_api, describe_api_call, resolve_api_call


def _spec(**over):
    base = {"mode": "api", "protocol": "openai-completions",
            "base_url": "http://127.0.0.1:1/v1"}
    base.update(over)
    return base


def test_modo_errado_rejeitado():
    with pytest.raises(ValueError, match="não é api"):
        resolve_api_call({"mode": "agent_cli", "command": "x"}, "p")


def test_protocolo_nao_suportado_rejeitado():
    with pytest.raises(ValueError, match="protocolo"):
        resolve_api_call(_spec(protocol="grpc"), "p")


def test_segredo_nao_resolvido_e_fail_closed():
    spec = _spec(secret_ref="keychain:item-inexistente-xyz")
    with pytest.raises(PermissionError, match="segredo não resolvido"):
        resolve_api_call(spec, "p",
                         secret_resolver=lambda ref: None)


def test_authorization_presente_com_token_resolver(tmp_path):
    spec = _spec(secret_ref="keychain:k")
    _url, headers, body = resolve_api_call(
        spec, "p", secret_resolver=lambda ref: "tok-fake-123")
    assert headers["Authorization"] == "Bearer tok-fake-123"
    payload = json.loads(body)
    assert payload["messages"][0]["content"] == "p"


def test_describe_sanitizado_nunca_tem_valor():
    spec = _spec(secret_ref="keychain:minha-chave-real")
    d = describe_api_call(spec)
    dumped = json.dumps(d)
    assert "minha-chave-real" not in dumped
    assert d["auth"] == "keychain:<redacted>"  # esquema visível, item nunca


@pytest.fixture()
def local_openai_server():
    """Servidor OpenAI-compatível mínimo em loopback (chamada real HTTP)."""
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen["body"] = json.loads(body)
            auth = self.headers.get("Authorization", "")
            seen["auth_prefix_ok"] = auth.startswith("Bearer ")
            out = {"choices": [{"message": {"content": "IA_GATE_OK"}}]}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format=None, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server, seen
    server.shutdown()


def test_chamada_real_http_local(local_openai_server):
    """Chamada HTTP REAL em servidor loopback — sem mock de módulo."""
    server, seen = local_openai_server
    port = server.server_port
    spec = _spec(base_url=f"http://127.0.0.1:{port}/v1")
    out = call_api(spec, "ping", model="probe-model", timeout_s=10,
                   secret_resolver=lambda ref: None)  # sem secret_ref: sem token
    assert out == "IA_GATE_OK"
    assert seen["body"]["model"] == "probe-model"
    assert seen["body"]["messages"][0]["content"] == "ping"

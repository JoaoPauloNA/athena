"""Testes do piloto Argos: contrato, autorização de host, veredito."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from harness.argos_pilot import run_pilot


@pytest.fixture()
def local_page():
    """Servir uma página mínima em loopback durante o teste."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (b"<!doctype html><html><head><title>Argos Pilot OK</title>"
                    b"</head><body><h1>ok</h1></body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # silencia log do teste
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


def test_pilot_pass_em_pagina_saudavel(local_page):
    report = run_pilot(local_page)
    assert report.verdict == "PASS"
    assert report.checks["http_2xx"]["pass"]
    assert "Argos Pilot OK" in report.checks["title_present"]["detail"]
    # screenshot é skip honesto (playwright ausente) e não derruba o veredito
    assert report.checks["screenshot_valid"].get("skip") is True


def test_host_nao_autorizado_bloqueado():
    report = run_pilot("https://example.com/pagina")
    assert report.verdict == "FAIL"
    assert not report.checks["host_authorized"]["pass"]


def test_porta_morta_falha_limpa():
    report = run_pilot("http://127.0.0.1:1/")
    assert report.verdict == "FAIL"
    assert not report.checks["http_2xx"]["pass"]


def test_relatorio_json_reproduzivel(local_page):
    import json
    r1 = json.loads(run_pilot(local_page).to_json())
    r2 = json.loads(run_pilot(local_page).to_json())
    r1.pop("url"); r2.pop("url")  # portas podem diferenciar entre runs? não: mesma fixture
    assert r1 == r2

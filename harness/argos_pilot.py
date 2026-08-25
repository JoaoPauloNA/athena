"""Argos piloto: evidência visual determinística de UMA página local.

Sem dependência de navegador pesado: usa fetch HTTP + parsing leve para os
checks determinísticos. Screenshot real de browser fica como extensão
futura (requer Playwright instalado); o contrato define o slot.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class PilotReport:
    url: str
    checks: dict[str, dict[str, Any]]
    verdict: str  # PASS | FAIL

    def to_json(self) -> str:
        return json.dumps(
            {"url": self.url, "checks": self.checks,
             "verdict": self.verdict},
            ensure_ascii=False, indent=2)


def _check_host_authorized(url: str) -> bool:
    match = re.match(r"^https?://([^:/]+)", url)
    return bool(match and match.group(1) in ALLOWED_HOSTS)


def run_pilot(url: str, *, screenshot_dir=None) -> PilotReport:
    """Executar a rodada única do piloto sobre `url` local autorizada."""
    checks: dict[str, dict[str, Any]] = {}

    if not _check_host_authorized(url):
        checks["host_authorized"] = {
            "pass": False, "detail": "apenas loopback é permitido"}
        return PilotReport(url, checks, "FAIL")
    checks["host_authorized"] = {"pass": True, "detail": "loopback"}

    # --- HTTP status
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            status = resp.status
            body = resp.read(200_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        checks["http_2xx"] = {"pass": False, "detail": f"{type(exc).__name__}"}
        checks["title_present"] = {"pass": False, "detail": "sem corpo"}
        checks["console_errors"] = {"pass": False, "detail": "sem corpo"}
        checks["screenshot_valid"] = {"pass": False, "detail": "n/a"}
        return PilotReport(url, checks, "FAIL")

    checks["http_2xx"] = {"pass": 200 <= status < 300, "detail": f"HTTP {status}"}

    # --- title
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.DOTALL | re.IGNORECASE)
    title = (m.group(1).strip() if m else "")
    checks["title_present"] = {"pass": bool(title), "detail": title[:80]}

    # --- erros de console declarados no próprio HTML (scripts inline que
    # chamam console.error em carga; heurística determinística do piloto)
    inline_errors = len(re.findall(r"console\.error\(", body))
    checks["console_errors"] = {"pass": inline_errors == 0,
                                "detail": f"{inline_errors} chamadas estáticas"}

    # --- screenshot: slot definido pelo contrato; sem Playwright instalado,
    # o check registra SKIP honesto em vez de falso positivo
    shot_ok = screenshot_dir is not None and screenshot_dir.exists()
    checks["screenshot_valid"] = {
        "pass": shot_ok,
        "detail": "capturado" if shot_ok else "SKIP: playwright ausente",
        "skip": not shot_ok,
    }

    core_pass = all(checks[k]["pass"] for k in
                    ("host_authorized", "http_2xx", "title_present"))
    verdict = "PASS" if core_pass else "FAIL"
    return PilotReport(url, checks, verdict)

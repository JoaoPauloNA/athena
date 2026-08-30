"""CFG-5: modo de execução `api` — dispatch por OpenAI-compatível.

Provedores como o CLIProxy (127.0.0.1:8317) entram por configuração:
providers.json declara {mode: api, protocol: openai-completions,
base_url, secret_ref}. O segredo é resolvido por referência na hora da
chamada e NUNCA aparece em log, exceção ou repr.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

_DEFAULT_TIMEOUT_S = 60


def _resolve_secret(secret_ref: str | None) -> str | None:
    """Resolver referência de segredo. Valor nunca é logado nem propagado."""
    if not secret_ref:
        return None
    if secret_ref.startswith("keychain:"):
        item = secret_ref.split(":", 1)[1]
        import subprocess
        proc = subprocess.run(
            ["security", "find-generic-password", "-a", "athena", "-s", item, "-w"],
            capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return proc.stdout.strip()
        return None
    if secret_ref.startswith("env:"):
        import os
        return os.environ.get(secret_ref.split(":", 1)[1])
    return None  # formato desconhecido → sem segredo (fail-closed)


def resolve_api_call(spec: dict[str, Any], prompt: str, *,
                     model: str | None = None,
                     timeout_s: float = _DEFAULT_TIMEOUT_S,
                     secret_resolver=_resolve_secret) -> tuple[str, dict[str, str], str]:
    """Montar (url, headers, body) para chamada OpenAI-completions.

    Headers retornados contêm Authorization mascarado para auditoria? Não —
    esta função retorna os headers reais para uso IMEDIATO; quem audita usa
    `describe_api_call`, que nunca inclui o valor.
    """
    mode = spec.get("mode")
    if mode != "api":
        raise ValueError(f"modo '{mode}' não é api")
    protocol = spec.get("protocol", "openai-completions")
    if protocol != "openai-completions":
        raise ValueError(f"protocolo '{protocol}' não suportado nesta fatia")
    base = str(spec["base_url"]).rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    token = secret_resolver(spec.get("secret_ref"))
    if spec.get("secret_ref") and not token:
        raise PermissionError(f"segredo não resolvido para {spec['secret_ref'].split(':')[0]}:<redacted>")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({
        "model": model or spec.get("default_model", ""),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode()
    return url, headers, body.decode()


def describe_api_call(spec: dict[str, Any]) -> dict[str, Any]:
    """Descrição sanitizada para logs/UI: sem valor de segredo."""
    return {
        "provider_mode": "api",
        "protocol": spec.get("protocol"),
        "base_url": spec.get("base_url"),
        "auth": (spec.get("secret_ref").split(":", 1)[0] + ":<redacted>"
                 if spec.get("secret_ref") else "none"),
    }


def call_api(spec: dict[str, Any], prompt: str, *,
             model: str | None = None,
             timeout_s: float = _DEFAULT_TIMEOUT_S,
             secret_resolver=_resolve_secret) -> str:
    """Executar a chamada e devolver somente o conteúdo da resposta."""
    url, headers, body = resolve_api_call(spec, prompt, model=model,
                                          timeout_s=timeout_s,
                                          secret_resolver=secret_resolver)
    req = urllib.request.Request(url, data=body.encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read())
    try:
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise RuntimeError("resposta de API em formato inesperado") from exc

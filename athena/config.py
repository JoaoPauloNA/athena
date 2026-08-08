"""Configurações e paths do Athena-MCP."""
from __future__ import annotations

import os
from pathlib import Path

# Diretório de dados do Athena-MCP
DATA_DIR = Path(os.environ.get("ATHENA_DATA_DIR", str(Path.home() / ".athena")))

# Arquivos de persistência
MODELS_CATALOG_FILE = Path(os.environ.get("ATHENA_MODELS_FILE", str(DATA_DIR / "models_catalog.json")))
COMBOS_FILE = Path(os.environ.get("ATHENA_COMBOS_FILE", str(DATA_DIR / "combos.json")))
USAGE_FILE = Path(os.environ.get("ATHENA_USAGE_FILE", str(DATA_DIR / "usage.json")))
LOGS_DIR = Path(os.environ.get("ATHENA_LOGS_DIR", str(DATA_DIR / "logs")))

# TTL do cache de modelos (dias)
MODELS_TTL_DAYS = float(os.environ.get("ATHENA_MODELS_TTL_DAYS", "5"))

# Retenção de logs (dias)
LOG_RETENTION_DAYS = int(os.environ.get("ATHENA_LOG_RETENTION_DAYS", "30"))

# Dashboard
DASHBOARD_HOST = os.environ.get("ATHENA_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("ATHENA_PORT", "20129"))

# MCP
MCP_MODE = os.environ.get("ATHENA_MCP_MODE", "stdio")

# Self-provider detection
SELF_PROVIDER_ENV = "ATHENA_SELF_PROVIDER"


def ensure_data_dir() -> None:
    """Garante que o diretório de dados e subdiretórios existem."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

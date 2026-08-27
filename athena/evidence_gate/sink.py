"""Sink local e atômico para relatórios internos sanitizados do EG-3A."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SINK_SCHEMA_VERSION = "athena.eg3a.sink.v1"
_UNSAFE_METADATA = re.compile(r"[^A-Za-z0-9_-]+")


def _sanitize_metadata(value: str, *, fallback: str) -> str:
    sanitized = _UNSAFE_METADATA.sub("-", value).strip("-_")
    return sanitized[:128] or fallback


class AtomicJsonFileSink:
    """Gravar um relatório por execução sob um diretório local explícito."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        raw_directory = os.fspath(directory)
        if not raw_directory or not os.path.isabs(raw_directory):
            raise ValueError("EG-3A sink directory must be an explicit absolute path")
        self._directory = Path(raw_directory)

    def __call__(
        self,
        report: Mapping[str, Any],
        *,
        execution_id: str,
        tool: str,
    ) -> None:
        self.write(report, execution_id=execution_id, tool=tool)

    def write(
        self,
        report: Mapping[str, Any],
        *,
        execution_id: str,
        tool: str,
    ) -> Path:
        """Escrever JSON completo via replace atômico e retornar o destino."""
        safe_execution_id = _sanitize_metadata(execution_id, fallback="execution")
        safe_tool = _sanitize_metadata(tool, fallback="tool")
        stable_id = hashlib.sha256(
            f"{tool}\0{execution_id}".encode()
        ).hexdigest()[:20]
        destination = self._directory / f"eg3a-{safe_tool}-{stable_id}.json"
        document = {
            "schema_version": SINK_SCHEMA_VERSION,
            "metadata": {
                "execution_id": safe_execution_id,
                "tool": safe_tool,
            },
            "report": dict(report),
        }
        encoded = (
            json.dumps(
                document,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._directory,
                prefix=".eg3a-",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
            directory_descriptor = os.open(self._directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        return destination

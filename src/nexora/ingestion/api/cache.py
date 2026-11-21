"""Caché en disco de las respuestas de fuentes externas.

La clave se deriva de la fuente y de sus parámetros; cada entrada guarda el
instante de escritura y el valor en JSON. La caducidad se decide por fuente: un
TTL de ``None`` significa que la entrada no caduca. El reloj se inyecta para que
las pruebas controlen el paso del tiempo sin dormir.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    """Hora actual en UTC."""
    return datetime.now(UTC)


def format_timestamp(value: datetime) -> str:
    """Marca ISO 8601 en UTC, con el desplazamiento explícito."""
    return value.astimezone(UTC).isoformat()


def parse_timestamp(value: str) -> datetime | None:
    """Lee una marca ISO 8601; devuelve ``None`` si no es válida."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def cache_key(source: str, params: Mapping[str, Any]) -> str:
    """Digest estable de la fuente y sus parámetros, ordenados de forma canónica."""
    canonical = json.dumps(
        {"source": source, "params": dict(params)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiskCache:
    """Caché de valores JSON en archivos, con caducidad opcional."""

    def __init__(
        self, directory: str | Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._directory = Path(directory)
        self._clock = clock if clock is not None else utc_now

    @property
    def directory(self) -> Path:
        """Directorio donde se guardan las entradas."""
        return self._directory

    def path_for(self, key: str) -> Path:
        """Ruta de una clave; el nombre del archivo es un digest, nunca texto libre."""
        return self._directory / f"{key}.json"

    def get(self, key: str, *, ttl_s: int | None) -> dict[str, Any] | None:
        """Devuelve el valor en caché si existe y no ha caducado."""
        path = self.path_for(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(entry, dict):
            return None
        cached_at = parse_timestamp(entry.get("cached_at", ""))
        value = entry.get("value")
        if cached_at is None or not isinstance(value, dict):
            return None
        if ttl_s is not None and self._clock() - cached_at >= timedelta(seconds=ttl_s):
            return None
        return value

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        """Escribe el valor de forma atómica, sustituyendo una entrada anterior."""
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {"cached_at": format_timestamp(self._clock()), "value": dict(value)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

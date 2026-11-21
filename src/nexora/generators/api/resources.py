"""Proyección de las filas de ``tms`` al contrato JSON y paginación del mock."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any

from nexora.common.api_contracts import (
    DEFAULT_PAGE_SIZE,
    FIRST_PAGE,
    JSON_BOOLEAN,
    JSON_DATE,
    JSON_INTEGER,
    JSON_NUMBER,
    JSON_STRING,
    JSON_TIMESTAMP,
    Field,
    ResourceContract,
)
from nexora.generators.api.repository import ResourceRepository


def _serialize(value: Any, field: Field) -> Any:
    """Convierte un valor nativo de la fuente al tipo JSON declarado en el contrato."""
    if value is None:
        return None
    if field.json_type == JSON_INTEGER:
        return int(value)
    if field.json_type == JSON_NUMBER:
        return float(value)
    if field.json_type == JSON_STRING:
        return str(value)
    if field.json_type == JSON_BOOLEAN:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "t", "1"}
        return bool(value)
    if field.json_type == JSON_DATE:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        return str(value)
    if field.json_type == JSON_TIMESTAMP:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    raise ValueError(f"{field.name}: tipo JSON desconocido {field.json_type!r}")


def project_rows(
    rows: Iterable[Mapping[str, object]], contract: ResourceContract
) -> list[dict[str, Any]]:
    """Proyecta cada fila a los campos del contrato, en su orden y con sus tipos JSON."""
    return [
        {field.name: _serialize(row.get(field.name), field) for field in contract.fields}
        for row in rows
    ]


def build_page(
    repository: ResourceRepository,
    contract: ResourceContract,
    page: int = FIRST_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Devuelve el sobre de paginación de una página del recurso."""
    total = repository.count(contract)
    total_pages = (total + page_size - 1) // page_size
    rows = repository.page(contract, page, page_size) if page <= total_pages else []
    return {
        "data": project_rows(rows, contract),
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "next_page": page + 1 if page < total_pages else None,
    }

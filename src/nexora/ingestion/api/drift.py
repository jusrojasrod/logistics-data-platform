"""Detección de drift del lado del cliente y bloqueo DQ-008.

El mock inyecta alteraciones de esquema con su propio plan (``generators/api/drift.py``);
aquí se detectan comparando cada elemento del payload con el contrato declarado, sin
reutilizar esa lógica. El payload crudo ya aterrizado no se modifica: primero se aterriza,
luego se registran las filas de drift, después se marca la ejecución como ``blocked`` y
solo entonces se lanza ``ApiSchemaDriftError`` para que la tarea falle y salte la alerta.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from nexora.common.api_contracts import (
    JSON_BOOLEAN,
    JSON_DATE,
    JSON_INTEGER,
    JSON_NUMBER,
    JSON_STRING,
    JSON_TIMESTAMP,
    SOURCE_SYSTEM,
    ResourceContract,
)
from nexora.ingestion.api.client import ApiPage
from nexora.ingestion.api.landing import (
    AppendOnlyCollection,
    DriftEntry,
    DriftResult,
    LandingResult,
    land_pages,
    record_drift,
    sha256_bytes,
)

CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_RETYPED = "retyped"
MISSING_TYPE = "missing"
NULL_TYPE = "null"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class ApiSchemaDriftError(RuntimeError):
    """El payload se conservó y el drift se registró, pero el esquema cambió."""

    def __init__(
        self,
        *,
        run_id: str,
        dataset: str,
        endpoint: str,
        drift_count: int,
        entries: tuple[DriftEntry, ...],
    ) -> None:
        self.run_id = run_id
        self.dataset = dataset
        self.endpoint = endpoint
        self.drift_count = drift_count
        self.entries = entries
        super().__init__(
            f"{run_id}/{dataset} ({endpoint}): {drift_count} cambio(s) de esquema (DQ-008)"
        )


class RunStatusWriter(Protocol):
    """Enganche para marcar la ejecución en ``meta.api_runs``.

    La tabla la crea otra parte del corte; esta interfaz deja el punto de bloqueo
    preparado sin depender de su DDL ni de su conexión.
    """

    def mark_blocked(self, *, run_id: str, dataset_name: str, drift_count: int) -> None: ...


@dataclass(frozen=True)
class DriftBlockResult:
    """Resultado de una ejecución sin drift."""

    landed: tuple[LandingResult, ...]
    drift_entries: tuple[DriftEntry, ...]
    recorded: tuple[DriftResult, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def json_type_of(value: Any) -> str:
    """Tipo JSON observado en un valor del payload, con fechas y marcas de tiempo."""
    if isinstance(value, bool):
        return JSON_BOOLEAN
    if isinstance(value, int):
        return JSON_INTEGER
    if isinstance(value, float):
        return JSON_NUMBER
    if isinstance(value, str):
        if _DATE_RE.fullmatch(value):
            return JSON_DATE
        if _TIMESTAMP_RE.fullmatch(value):
            return JSON_TIMESTAMP
        return JSON_STRING
    if value is None:
        return NULL_TYPE
    return type(value).__name__


def _entry(
    *,
    page: ApiPage,
    contract: ResourceContract,
    field: str,
    change_type: str,
    expected_type: str,
    actual_type: str,
    source_key: str,
) -> DriftEntry:
    return DriftEntry(
        endpoint=page.endpoint,
        page=page.page,
        field=field,
        change_type=change_type,
        expected_type=expected_type,
        actual_type=actual_type,
        source_key=source_key,
        payload_sha256=sha256_bytes(page.content),
    )


def detect_page_drift(page: ApiPage, contract: ResourceContract) -> tuple[DriftEntry, ...]:
    """Compara cada elemento de la página con el contrato y devuelve sus cambios."""
    declared = frozenset(contract.field_names)
    entries: list[DriftEntry] = []
    for item in page.data:
        source_key = str(item.get(contract.natural_key, ""))
        for name in item:
            if name not in declared:
                entries.append(
                    _entry(
                        page=page,
                        contract=contract,
                        field=name,
                        change_type=CHANGE_ADDED,
                        expected_type="",
                        actual_type=json_type_of(item[name]),
                        source_key=source_key,
                    )
                )
        for field in contract.fields:
            if field.name not in item:
                entries.append(
                    _entry(
                        page=page,
                        contract=contract,
                        field=field.name,
                        change_type=CHANGE_REMOVED,
                        expected_type=field.json_type,
                        actual_type=MISSING_TYPE,
                        source_key=source_key,
                    )
                )
                continue
            value = item[field.name]
            if value is None and field.nullable:
                continue
            actual = json_type_of(value)
            if actual != field.json_type:
                entries.append(
                    _entry(
                        page=page,
                        contract=contract,
                        field=field.name,
                        change_type=CHANGE_RETYPED,
                        expected_type=field.json_type,
                        actual_type=actual,
                        source_key=source_key,
                    )
                )
    return tuple(entries)


def land_and_flag_drift(
    payloads: AppendOnlyCollection,
    drift_rows: AppendOnlyCollection,
    pages: Iterable[ApiPage],
    contract: ResourceContract,
    *,
    run_id: str,
    source_system: str = SOURCE_SYSTEM,
    registry: RunStatusWriter | None = None,
    clock: Callable[[], datetime] | None = None,
) -> DriftBlockResult:
    """Aterriza, registra el drift, bloquea la ejecución y propaga el error.

    Con drift, el payload sigue en la colección de aterrizaje y las filas DQ-008
    ya están escritas antes de marcar ``blocked`` y lanzar ``ApiSchemaDriftError``.
    Sin drift no se registra nada ni se lanza ningún error.
    """
    now = clock if clock is not None else _utc_now
    page_list = tuple(pages)
    landed = land_pages(payloads, page_list, run_id=run_id, source_system=source_system, clock=now)
    entries = tuple(entry for page in page_list for entry in detect_page_drift(page, contract))
    recorded = tuple(
        record_drift(
            drift_rows,
            entry,
            run_id=run_id,
            source_system=source_system,
            detected_at=now(),
        )
        for entry in entries
    )
    if entries:
        if registry is not None:
            registry.mark_blocked(
                run_id=run_id, dataset_name=contract.dataset, drift_count=len(entries)
            )
        raise ApiSchemaDriftError(
            run_id=run_id,
            dataset=contract.dataset,
            endpoint=contract.endpoint,
            drift_count=len(entries),
            entries=entries,
        )
    return DriftBlockResult(landed=landed, drift_entries=entries, recorded=recorded)

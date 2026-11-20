"""Orquestación de una fuente de API: cliente, drift, aterrizaje y registro.

Un dataset de TMS se recorre con el cliente HTTP y se valida contra su contrato;
los datasets externos se producen con el simulador determinista y se aterrizan con
el mismo contrato de metadatos. En ambos casos el orden es el de la regla DQ-008:
primero el payload crudo, después las filas de drift, luego el estado ``blocked``
y solo al final se propaga el error. El módulo también reúne las dos consultas de
apoyo de la CLI: la comparación de claves contra ``tms`` y la frescura de cada
dataset.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
from pymongo import MongoClient

from nexora.common.api_contracts import DATASET_CONTRACTS, SOURCE_SYSTEM
from nexora.common.config import Settings, settings
from nexora.generators.api.repository import PostgresRepository
from nexora.generators.config import CITY_CATALOG
from nexora.ingestion.api.client import ApiClient, ApiPage, PageEnvelope, iter_pages
from nexora.ingestion.api.drift import ApiSchemaDriftError, RunStatusWriter, detect_page_drift
from nexora.ingestion.api.landing import (
    DRIFT_COLLECTION,
    DRIFT_DATABASE,
    PAYLOADS_COLLECTION,
    PAYLOADS_DATABASE,
    AppendOnlyCollection,
    land_pages,
    record_drift,
)
from nexora.ingestion.api.registry import (
    DATASETS,
    DATASETS_TABLE,
    FRESHNESS_SLO_MINUTES,
    META_SCHEMA,
    STATUS_FAILED,
    STATUS_OK,
    RunRegistry,
)
from nexora.ingestion.api.sources import (
    DATASET_BY_SOURCE,
    SOURCE_EXCHANGE_RATE,
    SOURCE_GEOCODING,
    SOURCE_WEATHER,
    fetch_exchange_rate,
    fetch_geocoding,
    fetch_weather,
)

EXTERNAL_ENDPOINTS: dict[str, str] = {
    "weather_api": "/v1/weather",
    "geocoding_api": "/v1/geocoding",
    "exchange_rate_api": "/v1/exchange_rate",
}

DATASET_ENDPOINTS: dict[str, str] = {
    **{name: contract.endpoint for name, contract in DATASET_CONTRACTS.items()},
    **EXTERNAL_ENDPOINTS,
}

SOURCE_BY_DATASET: dict[str, str] = {
    **{name: SOURCE_SYSTEM for name in DATASET_CONTRACTS},
    **{dataset: source for source, dataset in DATASET_BY_SOURCE.items()},
}

DATASET_NAMES: tuple[str, ...] = tuple(definition.dataset_name for definition in DATASETS)
TMS_DATASET_NAMES: tuple[str, ...] = tuple(DATASET_CONTRACTS)


class UnknownDatasetError(ValueError):
    """El dataset no pertenece a las fuentes de este corte."""


class EmptySourceTableError(RuntimeError):
    """La tabla ``tms`` está vacía: comparar 0 = 0 no sería evidencia."""

    def __init__(self, table: str, key: str) -> None:
        self.table = table
        self.key = key
        super().__init__(
            f"{table}: count(*) = 0; no se comparan claves ({key}) contra una tabla vacía"
        )


class KeyMismatchError(RuntimeError):
    """El número de claves aterrizadas no coincide con el de la fuente."""


@dataclass(frozen=True)
class DatasetRunResult:
    """Resultado observable de una ejecución por dataset."""

    run_id: str
    dataset: str
    source_system: str
    pages: int
    documents: int
    drift_count: int
    status: str


@dataclass(frozen=True)
class KeyCounts:
    """Conteo de claves de la fuente y del aterrizaje."""

    dataset: str
    table: str
    key: str
    expected: int
    landed: int

    def __str__(self) -> str:
        return (
            f"{self.dataset} ({self.table}.{self.key}): " f"esperado = aterrizado = {self.expected}"
        )


@dataclass(frozen=True)
class FreshnessRow:
    """Frescura observada de un dataset frente a su objetivo."""

    dataset: str
    minutes: float | None
    slo_minutes: int

    def __str__(self) -> str:
        if self.minutes is None:
            return f"{self.dataset}: sin datos (SLO {self.slo_minutes} min)"
        return f"{self.dataset}: {self.minutes:.1f} min (SLO {self.slo_minutes} min)"


class RunRecorder(RunStatusWriter, Protocol):
    """Registro de ejecuciones: prepara las tablas, siembra y escribe el estado."""

    def ensure_tables(self) -> None: ...

    def upsert_datasets(self, *, updated_at: datetime | None = None) -> int: ...

    def record_run(self, run_id: str, dataset_name: str, **fields: Any) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Pymongo devuelve fechas sin zona: se interpretan como UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _static_page(endpoint: str, number: int, item: Mapping[str, Any], total: int) -> ApiPage:
    """Envuelve un payload simulado con el mismo sobre que el cliente valida."""
    body: dict[str, Any] = {
        "data": [dict(item)],
        "page": number,
        "page_size": 1,
        "total_pages": total,
        "next_page": number + 1 if number < total else None,
    }
    return ApiPage(
        endpoint=endpoint,
        page=number,
        payload=body,
        envelope=PageEnvelope.model_validate(body),
        content=json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )


def collect_pages(
    dataset: str,
    *,
    client: ApiClient | None = None,
    config: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    on_date: str | None = None,
) -> tuple[ApiPage, ...]:
    """Recorre el recurso de TMS o produce los payloads simulados de una fuente."""
    conf = config if config is not None else settings
    contract = DATASET_CONTRACTS.get(dataset)
    if contract is not None:
        active = client if client is not None else ApiClient(config=conf)
        return tuple(iter_pages(active, contract.endpoint))

    source = next((name for name, value in DATASET_BY_SOURCE.items() if value == dataset), None)
    if source is None or dataset not in EXTERNAL_ENDPOINTS:
        raise UnknownDatasetError(f"dataset de API desconocido: {dataset}")

    day = on_date or (clock or _utc_now)().date().isoformat()
    if source == SOURCE_WEATHER:
        values = [fetch_weather(city.name, day, config=conf) for city in CITY_CATALOG]
    elif source == SOURCE_GEOCODING:
        values = [fetch_geocoding(city.name, config=conf) for city in CITY_CATALOG]
    elif source == SOURCE_EXCHANGE_RATE:
        values = [fetch_exchange_rate(day, config=conf)]
    else:
        raise UnknownDatasetError(f"fuente externa desconocida: {source}")
    total = len(values)
    return tuple(
        _static_page(EXTERNAL_ENDPOINTS[dataset], number, item, total)
        for number, item in enumerate(values, start=1)
    )


def extract_dataset(
    dataset: str,
    *,
    run_id: str,
    pages: Sequence[ApiPage],
    payloads: AppendOnlyCollection,
    drift_rows: AppendOnlyCollection,
    registry: RunRecorder,
    source_system: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> DatasetRunResult:
    """Aterriza un dataset, registra su drift y deja la fila ``ok`` o ``blocked``."""
    if dataset not in DATASET_ENDPOINTS:
        raise UnknownDatasetError(f"dataset de API desconocido: {dataset}")
    origin = source_system or SOURCE_BY_DATASET[dataset]
    now = clock if clock is not None else _utc_now
    page_list = tuple(pages)

    registry.ensure_tables()
    registry.upsert_datasets(updated_at=now())
    landed = land_pages(payloads, page_list, run_id=run_id, source_system=origin, clock=now)

    contract = DATASET_CONTRACTS.get(dataset)
    entries = (
        tuple(entry for page in page_list for entry in detect_page_drift(page, contract))
        if contract is not None
        else ()
    )
    for entry in entries:
        record_drift(drift_rows, entry, run_id=run_id, source_system=origin, detected_at=now())

    if entries:
        registry.mark_blocked(run_id=run_id, dataset_name=dataset, drift_count=len(entries))
        raise ApiSchemaDriftError(
            run_id=run_id,
            dataset=dataset,
            endpoint=contract.endpoint if contract else EXTERNAL_ENDPOINTS[dataset],
            drift_count=len(entries),
            entries=entries,
        )

    registry.record_run(run_id, dataset, status=STATUS_OK, finished_at=now())
    return DatasetRunResult(
        run_id=run_id,
        dataset=dataset,
        source_system=origin,
        pages=len(landed),
        documents=sum(len(page.data) for page in page_list),
        drift_count=0,
        status=STATUS_OK,
    )


def run_extraction(
    dataset: str,
    *,
    run_id: str,
    connection: Any,
    payloads: AppendOnlyCollection,
    drift_rows: AppendOnlyCollection,
    client: ApiClient | None = None,
    config: Settings | None = None,
    schema: str = META_SCHEMA,
    clock: Callable[[], datetime] | None = None,
    on_date: str | None = None,
) -> DatasetRunResult:
    """Prepara el registro, recorre la fuente y deja constancia de cualquier fallo."""
    if dataset not in DATASET_ENDPOINTS:
        raise UnknownDatasetError(f"dataset de API desconocido: {dataset}")
    now = clock if clock is not None else _utc_now
    started = now()
    registry = RunRegistry(
        connection,
        schema=schema,
        source_system=SOURCE_BY_DATASET[dataset],
        started_at=started,
    )
    try:
        pages = collect_pages(dataset, client=client, config=config, clock=now, on_date=on_date)
        registry.pages = len(pages)
        registry.documents = sum(len(page.data) for page in pages)
        return extract_dataset(
            dataset,
            run_id=run_id,
            pages=pages,
            payloads=payloads,
            drift_rows=drift_rows,
            registry=registry,
            clock=now,
        )
    except ApiSchemaDriftError:
        raise
    except Exception as exc:
        registry.ensure_tables()
        registry.record_run(
            run_id,
            dataset,
            status=STATUS_FAILED,
            finished_at=now(),
            error_class=type(exc).__name__,
            error_detail=str(exc),
        )
        raise


def count_landed_keys(collection: AppendOnlyCollection, *, run_id: str, endpoint: str) -> int:
    """Cuenta las claves del payload aterrizado para una ejecución y endpoint."""
    cursor = collection.find({"run_id": run_id, "endpoint": endpoint}, {"payload": 1})
    total = 0
    for document in cursor:
        payload = document.get("payload")
        if isinstance(payload, Mapping):
            data = payload.get("data", ())
            total += len(data) if isinstance(data, list | tuple) else 0
    return total


def verify_keys(
    dataset: str,
    *,
    run_id: str,
    repository: PostgresRepository,
    payloads: AppendOnlyCollection,
    tms_schema: str = "tms",
) -> KeyCounts:
    """Exige filas en ``tms`` antes de comparar el conteo con lo aterrizado."""
    contract = DATASET_CONTRACTS.get(dataset)
    if contract is None:
        raise UnknownDatasetError(f"verify-keys solo aplica a recursos de TMS: {dataset}")
    expected = repository.count(contract)
    if expected <= 0:
        raise EmptySourceTableError(f"{tms_schema}.{contract.resource}", contract.natural_key)
    landed = count_landed_keys(payloads, run_id=run_id, endpoint=contract.endpoint)
    if landed != expected:
        raise KeyMismatchError(
            f"{dataset}: esperado = {expected}, aterrizado = {landed} "
            f"({contract.natural_key}); no coinciden las claves de la ejecución {run_id}"
        )
    return KeyCounts(
        dataset, f"{tms_schema}.{contract.resource}", contract.natural_key, expected, landed
    )


def dataset_slos(connection: Any, *, schema: str = META_SCHEMA) -> dict[str, int]:
    """Lee los SLO de ``meta.datasets``; si no existe, usa la semilla canónica."""
    with connection.cursor() as cursor:
        cursor.execute("select to_regclass(%s)", (f"{schema}.{DATASETS_TABLE}",))
        row = cursor.fetchone()
        exists = row is not None and row[0] is not None
    if not exists:
        return {definition.dataset_name: FRESHNESS_SLO_MINUTES for definition in DATASETS}
    return {row[0]: int(row[3]) for row in RunRegistry(connection, schema=schema).datasets()}


def latest_ingested_at(collection: AppendOnlyCollection, endpoint: str) -> datetime | None:
    """Última marca de aterrizaje de un endpoint en ``raw.api_payloads``."""
    cursor = collection.aggregate(
        [
            {"$match": {"endpoint": endpoint}},
            {"$group": {"_id": None, "latest": {"$max": "$ingested_at"}}},
        ]
    )
    row = next(iter(cursor), None)
    value = row.get("latest") if row else None
    return _as_utc(value) if isinstance(value, datetime) else None


def freshness_rows(
    *,
    slos: Mapping[str, int],
    payloads: AppendOnlyCollection,
    clock: Callable[[], datetime] | None = None,
) -> tuple[FreshnessRow, ...]:
    """Calcula ``now() - max(ingested_at)`` en minutos para cada dataset."""
    now = (clock if clock is not None else _utc_now)()
    ordered = [name for name in DATASET_NAMES if name in slos]
    ordered += sorted(name for name in slos if name not in ordered)
    rows: list[FreshnessRow] = []
    for dataset in ordered:
        slo = int(slos[dataset])
        latest = latest_ingested_at(payloads, DATASET_ENDPOINTS[dataset])
        minutes = (
            None if latest is None else max(0.0, round((now - latest).total_seconds() / 60, 1))
        )
        rows.append(FreshnessRow(dataset, minutes, slo))
    return tuple(rows)


@contextmanager
def postgres_connection(
    config: Settings | None = None, dsn: str | None = None
) -> Iterator[psycopg.Connection]:
    """Conexión de lectura/escritura al ``meta`` de PostgreSQL."""
    conf = config if config is not None else settings
    with psycopg.connect(dsn or conf.etl_conninfo()) as connection:
        yield connection


@contextmanager
def mongo_collections(
    config: Settings | None = None, dsn: str | None = None
) -> Iterator[tuple[Any, Any]]:
    """Colecciones de aterrizaje y drift con el usuario de aplicación."""
    conf = config if config is not None else settings
    client = MongoClient(dsn or conf.mongo_app_dsn)
    try:
        yield (
            client[PAYLOADS_DATABASE][PAYLOADS_COLLECTION],
            client[DRIFT_DATABASE][DRIFT_COLLECTION],
        )
    finally:
        client.close()


__all__ = [
    "ApiSchemaDriftError",
    "DatasetRunResult",
    "EmptySourceTableError",
    "FreshnessRow",
    "KeyCounts",
    "KeyMismatchError",
    "RunRecorder",
    "UnknownDatasetError",
    "collect_pages",
    "count_landed_keys",
    "dataset_slos",
    "extract_dataset",
    "freshness_rows",
    "latest_ingested_at",
    "mongo_collections",
    "postgres_connection",
    "run_extraction",
    "verify_keys",
]

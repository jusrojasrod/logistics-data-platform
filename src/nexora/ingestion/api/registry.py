"""Registro de ejecuciones y datasets de la vía API en PostgreSQL.

``meta.datasets`` guarda un dataset por fuente de API con su SLO de frescura y
``meta.api_runs`` una fila por fuente y ejecución con su estado. Las tablas las
crea el propio registro en la primera ejecución, con el usuario que ejecuta la
ingesta; el esquema ya existe. Los enunciados aceptan el esquema como parámetro
para que las pruebas de integración puedan aislarse en ``it_<hex>_meta`` sin
tocar las tablas principales.

La clase :class:`RunRegistry` satisface el protocolo ``RunStatusWriter`` del
módulo de drift: ``mark_blocked`` deja la fila en ``blocked`` antes de que se
propague ``ApiSchemaDriftError``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import sql

from nexora.common.api_contracts import SOURCE_SYSTEM

META_SCHEMA = "meta"
DATASETS_TABLE = "datasets"
RUNS_TABLE = "api_runs"

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
STATUSES: frozenset[str] = frozenset({STATUS_OK, STATUS_FAILED, STATUS_BLOCKED})

FRESHNESS_SLO_MINUTES = 120
DRIFT_ERROR_CLASS = "ApiSchemaDriftError"


@dataclass(frozen=True)
class DatasetDefinition:
    """Dataset de API que se siembra en ``meta.datasets``."""

    dataset_name: str
    source_system: str
    description: str
    tier: str = "api"


DATASETS: tuple[DatasetDefinition, ...] = (
    DatasetDefinition("tms_shipments", SOURCE_SYSTEM, "Envíos del TMS recibidos por API"),
    DatasetDefinition(
        "tms_shipment_events", SOURCE_SYSTEM, "Eventos de envío del TMS recibidos por API"
    ),
    DatasetDefinition(
        "tms_carrier_incidents", SOURCE_SYSTEM, "Incidentes de transportista del TMS por API"
    ),
    DatasetDefinition("weather_api", "weather", "Clima simulado recibido por API"),
    DatasetDefinition("geocoding_api", "geocoding", "Geocodificación simulada recibida por API"),
    DatasetDefinition(
        "exchange_rate_api", "exchange_rate", "Tasa de cambio simulada recibida por API"
    ),
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def datasets_identifier(schema: str = META_SCHEMA) -> sql.Identifier:
    """Identificador cualificado de ``meta.datasets``."""
    return sql.Identifier(schema, DATASETS_TABLE)


def runs_identifier(schema: str = META_SCHEMA) -> sql.Identifier:
    """Identificador cualificado de ``meta.api_runs``."""
    return sql.Identifier(schema, RUNS_TABLE)


def create_datasets_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """DDL de ``meta.datasets``."""
    return sql.SQL(
        "create table if not exists {table} (\n"
        "    dataset_name text primary key,\n"
        "    source_system text not null,\n"
        "    tier text not null,\n"
        "    freshness_slo_minutes integer not null,\n"
        "    description text not null,\n"
        "    updated_at timestamptz not null\n"
        ")"
    ).format(table=datasets_identifier(schema))


def create_runs_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """DDL de ``meta.api_runs``."""
    return sql.SQL(
        "create table if not exists {table} (\n"
        "    run_id text not null,\n"
        "    dataset_name text not null references {datasets} (dataset_name),\n"
        "    source_system text not null,\n"
        "    started_at timestamptz not null,\n"
        "    finished_at timestamptz not null,\n"
        "    status text not null check (status in ('ok', 'failed', 'blocked')),\n"
        "    pages integer not null,\n"
        "    documents integer not null,\n"
        "    drift_count integer not null default 0,\n"
        "    error_class text,\n"
        "    error_detail text,\n"
        "    primary key (run_id, dataset_name)\n"
        ")"
    ).format(table=runs_identifier(schema), datasets=datasets_identifier(schema))


def upsert_dataset_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """Upsert de un dataset; parámetros en orden del :class:`DatasetDefinition` y ``updated_at``."""
    return sql.SQL(
        "insert into {table} "
        "(dataset_name, source_system, tier, freshness_slo_minutes, description, updated_at) "
        "values (%s, %s, %s, %s, %s, %s) "
        "on conflict (dataset_name) do update set "
        "source_system = excluded.source_system, "
        "tier = excluded.tier, "
        "freshness_slo_minutes = excluded.freshness_slo_minutes, "
        "description = excluded.description, "
        "updated_at = excluded.updated_at"
    ).format(table=datasets_identifier(schema))


def upsert_run_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """Upsert de una ejecución por ``(run_id, dataset_name)``.

    Parámetros: ``run_id``, ``dataset_name``, ``source_system``, ``started_at``,
    ``finished_at``, ``status``, ``pages``, ``documents``, ``drift_count``,
    ``error_class`` y ``error_detail``.
    """
    return sql.SQL(
        "insert into {table} "
        "(run_id, dataset_name, source_system, started_at, finished_at, status, "
        " pages, documents, drift_count, error_class, error_detail) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "on conflict (run_id, dataset_name) do update set "
        "finished_at = excluded.finished_at, "
        "status = excluded.status, "
        "pages = excluded.pages, "
        "documents = excluded.documents, "
        "drift_count = excluded.drift_count, "
        "error_class = excluded.error_class, "
        "error_detail = excluded.error_detail"
    ).format(table=runs_identifier(schema))


def datasets_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """Consulta de los datasets sembrados, ordenados por nombre."""
    return sql.SQL(
        "select dataset_name, source_system, tier, freshness_slo_minutes "
        "from {table} order by dataset_name"
    ).format(table=datasets_identifier(schema))


def runs_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """Consulta de las ejecuciones con su estado, ordenadas por dataset."""
    return sql.SQL(
        "select run_id, dataset_name, status, pages, documents, drift_count, error_class "
        "from {table} order by dataset_name"
    ).format(table=runs_identifier(schema))


class RunRegistry:
    """Registro de datasets y ejecuciones; implementa ``RunStatusWriter``.

    La conexión la aporta el llamador. Cada escritura del estado se confirma al
    terminar: el runner abre una transacción, así que sin el ``commit`` el estado
    ``blocked`` (y el ``failed`` del camino de error) desaparecería con el
    ``rollback`` que provoca la excepción que se propaga después. Con una
    conexión en autocommit no hay transacción que confirmar y el ``commit`` se
    omite. El contexto común de la ejecución (origen, inicio, páginas y
    documentos) se fija al construir el registro para que ``mark_blocked`` pueda
    completar la misma fila sin cambiar la firma del enganche de drift.
    """

    def __init__(
        self,
        connection: Any,
        *,
        schema: str = META_SCHEMA,
        source_system: str = SOURCE_SYSTEM,
        started_at: datetime | None = None,
        pages: int = 0,
        documents: int = 0,
    ) -> None:
        self.connection = connection
        self.schema = schema
        self.source_system = source_system
        self.started_at = started_at
        self.pages = pages
        self.documents = documents

    def ensure_tables(self) -> None:
        """Crea las dos tablas si no existen, primero la referenciada."""
        self.connection.execute(create_datasets_statement(self.schema))
        self.connection.execute(create_runs_statement(self.schema))

    def _commit(self) -> None:
        """Confirma lo escrito cuando la conexión no está en autocommit.

        El runner trabaja dentro de una transacción y la excepción que se
        propaga después haría ``rollback``: el estado tiene que quedar
        confirmado antes. Las conexiones en autocommit (el fixture de
        integración) ya han confirmado cada sentencia y no necesitan ``commit``.
        """
        if getattr(self.connection, "autocommit", True):
            return
        commit = getattr(self.connection, "commit", None)
        if commit is not None:
            commit()

    def upsert_datasets(self, *, updated_at: datetime | None = None) -> int:
        """Siembra o actualiza los seis datasets y devuelve cuántos se escribieron."""
        timestamp = updated_at or _utc_now()
        statement = upsert_dataset_statement(self.schema)
        for definition in DATASETS:
            self.connection.execute(
                statement,
                (
                    definition.dataset_name,
                    definition.source_system,
                    definition.tier,
                    FRESHNESS_SLO_MINUTES,
                    definition.description,
                    timestamp,
                ),
            )
        self._commit()
        return len(DATASETS)

    def record_run(
        self,
        run_id: str,
        dataset_name: str,
        *,
        status: str = STATUS_OK,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        source_system: str | None = None,
        pages: int | None = None,
        documents: int | None = None,
        drift_count: int = 0,
        error_class: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Inserta o actualiza la fila de una ejecución con su estado final."""
        if status not in STATUSES:
            raise ValueError(f"estado de ejecución no válido: {status}")
        self.connection.execute(
            upsert_run_statement(self.schema),
            (
                run_id,
                dataset_name,
                source_system or self.source_system,
                started_at or self.started_at or _utc_now(),
                finished_at or _utc_now(),
                status,
                self.pages if pages is None else pages,
                self.documents if documents is None else documents,
                drift_count,
                error_class,
                error_detail,
            ),
        )
        self._commit()

    def mark_blocked(self, *, run_id: str, dataset_name: str, drift_count: int) -> None:
        """Enganche de DQ-008: deja ``meta.api_runs`` en ``blocked`` con el conteo."""
        self.record_run(
            run_id,
            dataset_name,
            status=STATUS_BLOCKED,
            drift_count=drift_count,
            error_class=DRIFT_ERROR_CLASS,
            error_detail=f"DQ-008: {drift_count} cambio(s) de esquema",
        )

    def datasets(self) -> Iterator[tuple[Any, ...]]:
        """Devuelve los datasets registrados."""
        return iter(self.connection.execute(datasets_statement(self.schema)).fetchall())

    def runs(self) -> Iterator[tuple[Any, ...]]:
        """Devuelve las ejecuciones registradas."""
        return iter(self.connection.execute(runs_statement(self.schema)).fetchall())

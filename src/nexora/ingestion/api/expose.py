"""Exposición idempotente de los payloads de la vía API a PostgreSQL.

Los sobres aterrizados en MongoDB se leen sin modificarlos: ``data[]`` se expande
a una fila por elemento en una tabla nueva de ``raw`` con los seis metadatos de
página y la bandera de drift derivada de ``meta.api_schema_drift``. Los nombres
de base, colección, esquema y tabla se inyectan: por defecto apuntan a los
recursos de producción y las pruebas aisladas pueden sustituirlos sin tocar las
bases principales.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
from psycopg import sql
from pymongo import MongoClient

from nexora.common.api_contracts import RESOURCE_CONTRACTS
from nexora.common.config import Settings, settings
from nexora.ingestion.api.landing import (
    DRIFT_COLLECTION,
    DRIFT_DATABASE,
    METADATA_FIELDS,
    PAYLOADS_COLLECTION,
    PAYLOADS_DATABASE,
)

ITEMS_SCHEMA = "raw"
ITEMS_TABLE = "api_items"
PRIMARY_KEY: tuple[str, ...] = ("run_id", "endpoint", "page", "item_index")
DATA_FIELD = "data"

DATASET_BY_ENDPOINT: dict[str, str] = {
    contract.endpoint: contract.dataset for contract in RESOURCE_CONTRACTS.values()
}
NATURAL_KEY_BY_ENDPOINT: dict[str, str] = {
    contract.endpoint: contract.natural_key for contract in RESOURCE_CONTRACTS.values()
}
ITEM_COLUMNS: tuple[str, ...] = (
    *METADATA_FIELDS,
    "dataset_name",
    "source_key",
    "item_index",
    "payload",
    "has_drift_flag",
    "drift_fields",
    "exposed_at",
)

ENV_NAMES: dict[str, str] = {
    "payloads_database": "NEXORA_EXPOSE_PAYLOADS_DB",
    "payloads_collection": "NEXORA_EXPOSE_PAYLOADS_COLLECTION",
    "drift_database": "NEXORA_EXPOSE_DRIFT_DB",
    "drift_collection": "NEXORA_EXPOSE_DRIFT_COLLECTION",
    "items_schema": "NEXORA_EXPOSE_ITEMS_SCHEMA",
    "items_table": "NEXORA_EXPOSE_ITEMS_TABLE",
}


class ExposeError(RuntimeError):
    """Fallo al exponer o verificar los elementos de una ejecución."""


class ExposeVerificationError(ExposeError):
    """Los recuentos expuestos no coinciden con los elementos aterrizados."""


@dataclass(frozen=True)
class ResourceNames:
    """Nombres inyectables de los recursos de origen y destino."""

    payloads_database: str = PAYLOADS_DATABASE
    payloads_collection: str = PAYLOADS_COLLECTION
    drift_database: str = DRIFT_DATABASE
    drift_collection: str = DRIFT_COLLECTION
    items_schema: str = ITEMS_SCHEMA
    items_table: str = ITEMS_TABLE

    @classmethod
    def from_env(cls) -> ResourceNames:
        """Resuelve los nombres desde el entorno, con los valores de producción por defecto."""
        defaults = cls()
        return cls(
            **{
                field: os.environ.get(env_name, getattr(defaults, field))
                for field, env_name in ENV_NAMES.items()
            }
        )


@dataclass(frozen=True)
class ExposeResult:
    """Resultado de expandir los elementos de una ejecución."""

    run_id: str
    pages: int
    elements: int
    inserted: int


@dataclass(frozen=True)
class VerifyResult:
    """Resultado de comparar los recuentos de una ejecución."""

    run_id: str
    pages: int
    elements: int


class DocumentSource(Protocol):
    """Colección de solo lectura: basta con ``find`` por igualdad."""

    def find(
        self, filter: Mapping[str, Any], *args: Any, **kwargs: Any
    ) -> Iterator[Mapping[str, Any]]: ...


class ItemStore(Protocol):
    """Destino de la tabla de exposición, sin operaciones de reescritura."""

    def ensure_table(self) -> None: ...

    def insert_items(self, rows: Iterable[Mapping[str, Any]]) -> int: ...

    def counts_by_page(self, run_id: str) -> Mapping[tuple[str, int], int]: ...


def items_identifier(names: ResourceNames) -> sql.Identifier:
    """Identificador cualificado de la tabla de exposición."""
    return sql.Identifier(names.items_schema, names.items_table)


def create_items_statement(names: ResourceNames) -> sql.Composed:
    """DDL idempotente de la tabla de elementos con su clave primaria compuesta."""
    return sql.SQL(
        "create table if not exists {table} ("
        "run_id text not null, "
        "source_system text not null, "
        "endpoint text not null, "
        "page integer not null, "
        "payload_sha256 text not null, "
        "ingested_at timestamptz not null, "
        "dataset_name text not null, "
        "source_key text not null, "
        "item_index integer not null, "
        "payload jsonb not null, "
        "has_drift_flag boolean not null default false, "
        "drift_fields jsonb not null default '[]'::jsonb, "
        "exposed_at timestamptz not null, "
        "primary key (run_id, endpoint, page, item_index)"
        ")"
    ).format(table=items_identifier(names))


def insert_items_statement(names: ResourceNames) -> sql.Composed:
    """Inserción en lote que descarta la clave repetida sin reescribirla."""
    return sql.SQL(
        "insert into {table} (run_id, source_system, endpoint, page, payload_sha256, "
        "ingested_at, dataset_name, source_key, item_index, payload, has_drift_flag, "
        "drift_fields, exposed_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s::jsonb, %s, %s::jsonb, %s) "
        "on conflict (run_id, endpoint, page, item_index) do nothing"
    ).format(table=items_identifier(names))


def counts_by_page_statement(names: ResourceNames) -> sql.Composed:
    """Recuento de elementos expuestos por ``(endpoint, page)`` dentro de un ``run_id``."""
    return sql.SQL(
        "select endpoint, page, count(*) from {table} where run_id = %s group by endpoint, page"
    ).format(table=items_identifier(names))


class PostgresItemStore:
    """Almacén de la tabla de exposición sobre una conexión ``psycopg`` inyectada."""

    def __init__(self, connection: Any, names: ResourceNames | None = None) -> None:
        self._connection = connection
        self._names = names or ResourceNames()

    def ensure_table(self) -> None:
        self._connection.execute(create_items_statement(self._names))

    def insert_items(self, rows: Iterable[Mapping[str, Any]]) -> int:
        values = [tuple(row[name] for name in ITEM_COLUMNS) for row in rows]
        if not values:
            return 0
        with self._connection.cursor() as cursor:
            cursor.executemany(insert_items_statement(self._names), values)
            return int(cursor.rowcount)

    def counts_by_page(self, run_id: str) -> Mapping[tuple[str, int], int]:
        cursor = self._connection.execute(counts_by_page_statement(self._names), (run_id,))
        return {(endpoint, int(page)): int(count) for endpoint, page, count in cursor.fetchall()}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _data_of(document: Mapping[str, Any]) -> tuple[Any, ...]:
    """Elementos del sobre aterrizado, sin transformarlos."""
    payload = document.get("payload")
    if not isinstance(payload, Mapping):
        return ()
    data = payload.get(DATA_FIELD, ())
    if not isinstance(data, list | tuple):
        return ()
    return tuple(data)


def _source_key(item: Any, natural_key: str) -> str:
    """Valor de la clave natural del elemento, igual al que registra el drift."""
    if isinstance(item, Mapping):
        return str(item.get(natural_key, ""))
    return ""


def _drift_fields(
    entries: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str, int, str], tuple[str, ...]]:
    """Campos con drift indexados por la clave de cuatro partes de D4."""
    grouped: dict[tuple[str, str, int, str], set[str]] = {}
    for entry in entries:
        key = (
            str(entry["run_id"]),
            str(entry["endpoint"]),
            int(entry["page"]),
            str(entry["source_key"]),
        )
        grouped.setdefault(key, set()).add(str(entry["field"]))
    return {key: tuple(sorted(fields)) for key, fields in grouped.items()}


def expose_run(
    payloads: DocumentSource,
    drift_rows: DocumentSource,
    target: ItemStore,
    run_id: str,
    *,
    exposed_at: datetime | None = None,
) -> ExposeResult:
    """Expande un ``run_id`` y envía sus elementos al destino sin reescribir lo ya expuesto."""
    stamp = exposed_at or _utc_now()
    target.ensure_table()
    drift_index = _drift_fields(drift_rows.find({"run_id": run_id}))
    rows: list[dict[str, Any]] = []
    pages = 0
    for document in payloads.find({"run_id": run_id}):
        endpoint = str(document["endpoint"])
        if endpoint not in DATASET_BY_ENDPOINT:
            continue
        page_number = int(document["page"])
        natural_key = NATURAL_KEY_BY_ENDPOINT[endpoint]
        data = _data_of(document)
        pages += 1
        for item_index, item in enumerate(data):
            source_key = _source_key(item, natural_key)
            fields = drift_index.get((run_id, endpoint, page_number, source_key), ())
            rows.append(
                {
                    "run_id": run_id,
                    "source_system": document["source_system"],
                    "endpoint": endpoint,
                    "page": page_number,
                    "payload_sha256": document["payload_sha256"],
                    "ingested_at": document["ingested_at"],
                    "dataset_name": DATASET_BY_ENDPOINT[endpoint],
                    "source_key": source_key,
                    "item_index": item_index,
                    "payload": json.dumps(item, ensure_ascii=False, sort_keys=True),
                    "has_drift_flag": bool(fields),
                    "drift_fields": json.dumps(list(fields), ensure_ascii=False),
                    "exposed_at": stamp,
                }
            )
    inserted = target.insert_items(rows)
    return ExposeResult(run_id=run_id, pages=pages, elements=len(rows), inserted=inserted)


def verify_run(
    payloads: DocumentSource,
    target: ItemStore,
    run_id: str,
) -> VerifyResult:
    """Compara el recuento por página; sin elementos falla en lugar de comparar 0 = 0."""
    expected: dict[tuple[str, int], int] = {}
    for document in payloads.find({"run_id": run_id}):
        endpoint = str(document["endpoint"])
        if endpoint not in DATASET_BY_ENDPOINT:
            continue
        key = (endpoint, int(document["page"]))
        expected[key] = expected.get(key, 0) + len(_data_of(document))
    total = sum(expected.values())
    if total == 0:
        raise ExposeVerificationError(
            f"{run_id}: sin elementos aterrizados; verify no compara 0 = 0"
        )
    actual = target.counts_by_page(run_id)
    for key, count in expected.items():
        found = actual.get(key, 0)
        if found != count:
            raise ExposeVerificationError(
                f"{run_id} {key[0]} pagina {key[1]}: aterrizados {count}, expuestos {found}"
            )
    return VerifyResult(run_id=run_id, pages=len(expected), elements=total)


@contextmanager
def mongo_sources(
    names: ResourceNames,
    config: Settings | None = None,
    dsn: str | None = None,
) -> Iterator[tuple[Any, Any]]:
    """Colecciones inyectables de payloads y drift, cerradas siempre al terminar."""
    conf = config if config is not None else settings
    client = MongoClient(dsn or conf.mongo_app_dsn)
    try:
        yield (
            client[names.payloads_database][names.payloads_collection],
            client[names.drift_database][names.drift_collection],
        )
    finally:
        client.close()


@contextmanager
def postgres_target(
    names: ResourceNames,
    config: Settings | None = None,
    dsn: str | None = None,
) -> Iterator[PostgresItemStore]:
    """Destino de exposición sobre la DSN indicada o la del entorno."""
    conf = config if config is not None else settings
    with psycopg.connect(dsn or conf.etl_conninfo()) as connection:
        yield PostgresItemStore(connection, names)


PROG = "python -m nexora.ingestion.api.expose"


def _add_name_arguments(command: argparse.ArgumentParser) -> None:
    """Añade a un subcomando los argumentos de nombres inyectables."""
    command.add_argument("--payloads-database", default=None)
    command.add_argument("--payloads-collection", default=None)
    command.add_argument("--drift-database", default=None)
    command.add_argument("--drift-collection", default=None)
    command.add_argument("--items-schema", default=None)
    command.add_argument("--items-table", default=None)


def build_parser() -> argparse.ArgumentParser:
    """Construye el analizador con los subcomandos ``expose`` y ``verify``."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Exposición y verificación de los payloads de la vía API",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("expose", "expone los elementos de un run_id"),
        ("verify", "verifica los recuentos de un run_id"),
    ):
        command = subcommands.add_parser(name, help=help_text)
        command.add_argument("--run-id", required=True)
        command.add_argument("--pg-dsn", default=None)
        command.add_argument("--mongo-dsn", default=None)
        _add_name_arguments(command)
    return parser


def _names_from_args(args: argparse.Namespace, base: ResourceNames) -> ResourceNames:
    """Aplica solo los nombres que el CLI sustituye explícitamente."""
    overrides = {field: value for field in ENV_NAMES if (value := getattr(args, field)) is not None}
    return replace(base, **overrides) if overrides else base


def main(
    argv: Sequence[str] | None = None,
    *,
    config: Settings | None = None,
    names: ResourceNames | None = None,
) -> int:
    """Ejecuta el subcomando indicado; la DSN nunca se imprime."""
    args = build_parser().parse_args(argv)
    selected = _names_from_args(args, names or ResourceNames.from_env())
    try:
        with (
            postgres_target(selected, config, args.pg_dsn) as target,
            mongo_sources(selected, config, args.mongo_dsn) as (payloads, drift_rows),
        ):
            if args.command == "expose":
                result: ExposeResult | VerifyResult = expose_run(
                    payloads, drift_rows, target, args.run_id
                )
            else:
                result = verify_run(payloads, target, args.run_id)
        print(result)
        return 0
    except ExposeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATASET_BY_ENDPOINT",
    "ITEM_COLUMNS",
    "PRIMARY_KEY",
    "ENV_NAMES",
    "ExposeError",
    "ExposeResult",
    "ExposeVerificationError",
    "PostgresItemStore",
    "ResourceNames",
    "VerifyResult",
    "build_parser",
    "counts_by_page_statement",
    "create_items_statement",
    "expose_run",
    "insert_items_statement",
    "main",
    "mongo_sources",
    "postgres_target",
    "verify_run",
]

"""Batch loader of the synthetic source CSV files into the ``raw`` schema.

Each source entity lands in its own append-only table (``raw.shipments``,
``raw.customers``, ``raw.carriers``, ``raw.routes`` and ``raw.service_types``).
Tables are created with explicit DDL: column types and nullability come from the
contract declared here, never inferred from the CSV. Every loaded row carries
the execution metadata ``run_id``, ``source_system``, ``source_file`` (file name
only, never a path), ``source_file_hash`` (SHA-256 of the file) and
``ingested_at``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

from nexora.common.config import settings

RAW_SCHEMA: str = "raw"

ERP_SOURCE_SYSTEM: str = "erp"
TMS_SOURCE_SYSTEM: str = "tms"

HASH_CHUNK_BYTES: int = 1024 * 1024


@dataclass(frozen=True)
class Column:
    """A typed column of a ``raw`` table."""

    name: str
    sql_type: str
    nullable: bool = False
    check: str | None = None

    def definition(self) -> sql.Composed:
        """Return the column definition used inside ``CREATE TABLE``."""
        parts = [sql.Identifier(self.name), sql.SQL(self.sql_type)]
        if not self.nullable:
            parts.append(sql.SQL("not null"))
        if self.check is not None:
            parts.append(sql.SQL(f"check ({self.check})"))
        return sql.SQL(" ").join(parts)


# Execution metadata appended by the loader to every raw table, in this order.
METADATA_COLUMNS: tuple[Column, ...] = (
    Column("run_id", "text"),
    Column("source_system", "text"),
    Column("source_file", "text"),
    Column("source_file_hash", "text"),
    Column("ingested_at", "timestamptz"),
)


@dataclass(frozen=True)
class RawTable:
    """Contract of one ``raw`` table and the CSV file that feeds it."""

    name: str
    source_system: str
    columns: tuple[Column, ...]

    @property
    def file_name(self) -> str:
        return f"{self.name}.csv"

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def identifier(self) -> sql.Identifier:
        return sql.Identifier(RAW_SCHEMA, self.name)

    def create_statement(self) -> sql.Composed:
        """Return the explicit ``CREATE TABLE`` of the contract plus metadata."""
        definitions = [column.definition() for column in (*self.columns, *METADATA_COLUMNS)]
        return sql.SQL("create table if not exists {table} (\n    {columns}\n)").format(
            table=self.identifier,
            columns=sql.SQL(",\n    ").join(definitions),
        )


# Business columns of raw.shipments: 15 of the contract, plus the 5 metadata
# columns that make the 20 columns consumed downstream.
SHIPMENTS = RawTable(
    name="shipments",
    source_system=TMS_SOURCE_SYSTEM,
    columns=(
        Column("shipment_id", "text"),
        Column("customer_id", "text", nullable=True),
        Column("carrier_id", "text", nullable=True),
        Column("route_id", "text"),
        Column("service_type_id", "text"),
        Column("pickup_ts", "timestamptz"),
        Column("pickup_date", "date"),
        Column("promised_delivery_ts", "timestamptz", nullable=True),
        Column("picked_up_at", "timestamptz"),
        Column("delivered_at", "timestamptz", nullable=True),
        Column("ordered_qty", "integer", check="ordered_qty >= 1"),
        Column("delivered_qty", "integer", nullable=True),
        Column("ordered_weight_kg", "numeric(12,3)", check="ordered_weight_kg > 0"),
        Column("delivered_weight_kg", "numeric(12,3)", nullable=True),
        Column("definitive_failure_flag", "boolean"),
    ),
)

CUSTOMERS = RawTable(
    name="customers",
    source_system=ERP_SOURCE_SYSTEM,
    columns=(
        Column("customer_id", "text"),
        Column("customer_name", "text"),
        Column("segment", "text"),
        Column("industry", "text"),
        Column("region", "text"),
        Column("city", "text"),
        Column("payment_term_days", "integer"),
    ),
)

CARRIERS = RawTable(
    name="carriers",
    source_system=TMS_SOURCE_SYSTEM,
    columns=(
        Column("carrier_id", "text"),
        Column("carrier_name", "text"),
        Column("carrier_type", "text"),
        Column("service_level", "text"),
        Column("rate_index", "numeric(6,2)"),
    ),
)

ROUTES = RawTable(
    name="routes",
    source_system=TMS_SOURCE_SYSTEM,
    columns=(
        Column("route_id", "text"),
        Column("origin_city", "text"),
        Column("destination_city", "text"),
        Column("distance_km", "numeric(8,1)"),
        Column("estimated_hours", "numeric(6,1)"),
        Column("road_type", "text"),
    ),
)

SERVICE_TYPES = RawTable(
    name="service_types",
    source_system=TMS_SOURCE_SYSTEM,
    columns=(
        Column("service_type_id", "text"),
        Column("service_type_name", "text"),
        Column("sla_hours", "integer"),
    ),
)

# Load order: dimensions first, then the shipments that reference them.
RAW_TABLES: tuple[RawTable, ...] = (CUSTOMERS, CARRIERS, ROUTES, SERVICE_TYPES, SHIPMENTS)


@dataclass(frozen=True)
class LoadResult:
    """Outcome of loading one CSV file into its ``raw`` table."""

    table: str
    source_file: str
    source_file_hash: str
    rows: int


def file_sha256(path: str | Path) -> str:
    """Return the SHA-256 hex digest of the file contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(path: str | Path) -> tuple[str, ...]:
    """Return the header row of a CSV file."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return tuple(next(csv.reader(handle), ()))


def check_header(table: RawTable, path: str | Path) -> None:
    """Fail if the CSV header does not match the contract column order."""
    header = read_header(path)
    if header != table.column_names:
        raise ValueError(
            f"{Path(path).name}: cabecera {list(header)} distinta del contrato "
            f"{list(table.column_names)}"
        )


def _staging_statement(table: RawTable, staging: sql.Identifier) -> sql.Composed:
    definitions = [
        sql.SQL(" ").join([sql.Identifier(column.name), sql.SQL(column.sql_type)])
        for column in table.columns
    ]
    return sql.SQL("create temporary table {staging} ({columns}) on commit drop").format(
        staging=staging,
        columns=sql.SQL(", ").join(definitions),
    )


def load_file(
    conn: psycopg.Connection,
    table: RawTable,
    path: str | Path,
    run_id: str,
    ingested_at: datetime,
) -> LoadResult:
    """Load one CSV file into its ``raw`` table inside a single transaction.

    The file is copied into a typed temporary table, so values are cast by
    PostgreSQL against the contract types, and then appended to the raw table
    with the execution metadata.
    """
    source = Path(path)
    check_header(table, source)
    source_file_hash = file_sha256(source)
    staging = sql.Identifier(f"_stage_{table.name}")
    business = sql.SQL(", ").join(sql.Identifier(name) for name in table.column_names)
    target = sql.SQL(", ").join(
        sql.Identifier(name)
        for name in (*table.column_names, *(column.name for column in METADATA_COLUMNS))
    )

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(table.create_statement())
        cur.execute(_staging_statement(table, staging))
        copy_statement = sql.SQL(
            "copy {staging} ({columns}) from stdin with (format csv, header true)"
        ).format(staging=staging, columns=business)
        with cur.copy(copy_statement) as copy, source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
                copy.write(chunk)
        cur.execute(
            sql.SQL(
                "insert into {table} ({target}) "
                "select {business}, %s, %s, %s, %s, %s from {staging}"
            ).format(table=table.identifier, target=target, business=business, staging=staging),
            (run_id, table.source_system, source.name, source_file_hash, ingested_at),
        )
        rows = cur.rowcount

    return LoadResult(
        table=f"{RAW_SCHEMA}.{table.name}",
        source_file=source.name,
        source_file_hash=source_file_hash,
        rows=rows,
    )


def load_directory(
    conn: psycopg.Connection,
    directory: str | Path,
    run_id: str | None = None,
    tables: Sequence[RawTable] = RAW_TABLES,
) -> list[LoadResult]:
    """Load every expected CSV file of ``directory`` with the same run metadata."""
    base = Path(directory)
    missing = [table.file_name for table in tables if not (base / table.file_name).is_file()]
    if missing:
        raise FileNotFoundError(f"faltan archivos en {base}: {', '.join(missing)}")

    run = run_id if run_id is not None else uuid.uuid4().hex
    ingested_at = datetime.now(UTC)
    return [load_file(conn, table, base / table.file_name, run, ingested_at) for table in tables]


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the loader."""
    parser = argparse.ArgumentParser(
        prog="python -m nexora.ingestion.batch.loaders",
        description="Carga los CSV de origen de una carpeta en las tablas del esquema raw.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="carpeta con shipments.csv, customers.csv, carriers.csv, routes.csv y service_types.csv",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="cadena de conexión de PostgreSQL. Por defecto: NEXORA_PG_DSN",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="identificador de la ejecución. Por defecto: un UUID nuevo",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the loader and return the process exit code."""
    args = build_parser().parse_args(argv)
    dsn = args.dsn if args.dsn is not None else settings.pg_dsn
    with psycopg.connect(dsn) as conn:
        results = load_directory(conn, args.input, run_id=args.run_id)
    for result in results:
        print(
            f"cargado: {result.table} <- {result.source_file} "
            f"({result.rows} filas, sha256 {result.source_file_hash[:12]})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

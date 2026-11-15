"""Batch connectors that land the sources into the append-only ``raw`` schema.

Two ways in, both selected with ``--source``:

* ``tms``: extraction from the operational ``tms`` schema. Each
  ``tms.<table>`` (``carriers``, ``routes``, ``service_types`` and
  ``shipments``) is read by ``updated_at`` within the window
  ``(watermark, max(updated_at)]`` and appended to ``raw.<table>``. Each table
  is handled in a single transaction that first takes the advisory lock
  ``pg_advisory_xact_lock(hashtext('<schema>.<table>'))``, shared with the TMS
  publisher, and only then reads its watermark from ``meta.batch_watermarks``,
  reads the window, inserts it into ``raw`` and advances the watermark. A run
  without new rows inserts nothing and leaves the watermark as it was.
* ``files``: load of the ERP files (``customers*.csv``) into ``raw.customers``.
  A file is identified by the SHA-256 of its bytes and registered in
  ``meta.ingested_files``, one row per ``(target_table, source_file_hash)``. A
  file whose hash is already registered is skipped, so a renamed copy is not
  loaded twice, while a modified file has a new hash and is loaded.

Tables are created with explicit DDL: column types and nullability come from the
contracts declared here, never inferred from the data. Every row carries the
execution metadata ``run_id``, ``source_system``, ``source_file`` (file name or
``<schema>.<table>`` of origin, never a path), ``source_file_hash`` (SHA-256 of
the file or of the extracted batch) and ``ingested_at``. The connectors only
append to ``raw``: they never remove nor rewrite rows.

The schema names are parameters (``tms``, ``raw`` and ``meta`` by default), so
the same functions can run against isolated schemas.
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

from nexora.common.config import settings
from nexora.ingestion.batch import watermark
from nexora.ingestion.batch.readers import (
    HASH_CHUNK_BYTES,
    check_header,
    discover_files,
    file_sha256,
)

RAW_SCHEMA: str = "raw"
TMS_SCHEMA: str = "tms"
META_SCHEMA: str = watermark.META_SCHEMA

INGESTED_FILES_TABLE: str = "ingested_files"

ERP_SOURCE_SYSTEM: str = "erp"
TMS_SOURCE_SYSTEM: str = "tms"

__all__ = [
    "CARRIERS",
    "CUSTOMERS",
    "ROUTES",
    "SERVICE_TYPES",
    "SHIPMENTS",
    "Column",
    "ExtractResult",
    "FileResult",
    "RawTable",
    "build_parser",
    "check_header",
    "lock_key",
    "main",
    "run_files",
    "run_tms",
    "should_skip",
]


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


# Execution metadata appended by the connectors to every raw table, in this order.
METADATA_COLUMNS: tuple[Column, ...] = (
    Column("run_id", "text"),
    Column("source_system", "text"),
    Column("source_file", "text"),
    Column("source_file_hash", "text"),
    Column("ingested_at", "timestamptz"),
)


@dataclass(frozen=True)
class RawTable:
    """Contract of one ``raw`` table: its business columns and source system."""

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

    def qualified(self, schema: str = RAW_SCHEMA) -> sql.Identifier:
        """Return the identifier of the table inside ``schema``."""
        return sql.Identifier(schema, self.name)

    def create_statement(self, schema: str = RAW_SCHEMA) -> sql.Composed:
        """Return the explicit ``CREATE TABLE`` of the contract plus metadata."""
        definitions = [column.definition() for column in (*self.columns, *METADATA_COLUMNS)]
        return sql.SQL("create table if not exists {table} (\n    {columns}\n)").format(
            table=self.qualified(schema),
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

# Every raw table of the contract.
RAW_TABLES: tuple[RawTable, ...] = (CUSTOMERS, CARRIERS, ROUTES, SERVICE_TYPES, SHIPMENTS)

# Extraction order from tms: dimensions first, then the shipments that reference them.
TMS_TABLES: tuple[RawTable, ...] = (CARRIERS, ROUTES, SERVICE_TYPES, SHIPMENTS)

# Tables loaded from ERP files.
FILE_TABLES: tuple[RawTable, ...] = (CUSTOMERS,)


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of extracting one ``tms`` table into its ``raw`` table."""

    table: str
    source_table: str
    rows: int
    source_file_hash: str | None
    watermark_ts: datetime | None


@dataclass(frozen=True)
class FileResult:
    """Outcome of loading one ERP file into its ``raw`` table."""

    table: str
    source_file: str
    source_file_hash: str
    rows: int
    skipped: bool = False


def should_skip(file_hash: str, loaded_hashes: Collection[str]) -> bool:
    """Return whether a file must be skipped because its hash is already loaded."""
    return file_hash in loaded_hashes


def lock_key(schema: str, table: str) -> str:
    """Return the text hashed into the advisory lock of ``<schema>.<table>``.

    The TMS publisher locks the same text, so extraction and publication of one
    table never overlap.
    """
    return f"{schema}.{table}"


def _metadata_names() -> tuple[str, ...]:
    return tuple(column.name for column in METADATA_COLUMNS)


def _column_list(names: Sequence[str]) -> sql.Composed:
    return sql.SQL(", ").join(sql.Identifier(name) for name in names)


def _window_condition(window: watermark.Window) -> tuple[sql.SQL, tuple[datetime, ...]]:
    """Return the ``where`` condition of ``window`` and its parameters."""
    if window.lower is None:
        return sql.SQL("updated_at <= %s"), (window.upper,)
    return sql.SQL("updated_at > %s and updated_at <= %s"), (window.lower, window.upper)


def _batch_statement(table: RawTable, source: sql.Identifier, condition: sql.SQL) -> sql.Composed:
    """Return the count and SHA-256 of the rows of the window, in ``tms_row_id`` order."""
    return sql.SQL(
        "select count(*), encode(sha256(convert_to(coalesce("
        "string_agg(row({columns})::text, E'\\n' order by tms_row_id), ''), 'UTF8')), 'hex') "
        "from {source} where {condition}"
    ).format(columns=_column_list(table.column_names), source=source, condition=condition)


def extract_table(
    conn: psycopg.Connection,
    table: RawTable,
    run_id: str,
    ingested_at: datetime,
    tms_schema: str = TMS_SCHEMA,
    raw_schema: str = RAW_SCHEMA,
    meta_schema: str = META_SCHEMA,
) -> ExtractResult:
    """Extract the new rows of ``tms.<table>`` into ``raw.<table>`` in one transaction.

    Order inside the transaction: UTC time zone for the batch hash, advisory
    lock of the source table, and only then the read of its watermark. The
    window is ``(watermark, max(updated_at)]``; its rows are appended to
    ``raw`` with the execution metadata and the watermark moves to the upper
    bound. Without new rows nothing is inserted and the watermark is kept.
    """
    source_table = lock_key(tms_schema, table.name)
    source = sql.Identifier(tms_schema, table.name)
    target = table.qualified(raw_schema)

    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set local timezone to 'UTC'")
        cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (source_table,))
        cur.execute("select to_regclass(%s)", (source.as_string(cur),))
        row = cur.fetchone()
        if row is None or row[0] is None:
            raise LookupError(f"no existe la tabla de origen {source_table}")
        cur.execute(table.create_statement(raw_schema))
        cur.execute(watermark.create_statement(meta_schema))
        # The lock is held: read meta.batch_watermarks only now.
        cur.execute(watermark.select_statement(meta_schema), (source_table,))
        row = cur.fetchone()
        previous = row[0] if row is not None else None
        cur.execute(sql.SQL("select max(updated_at) from {source}").format(source=source))
        row = cur.fetchone()
        window = watermark.compute_window(previous, row[0] if row is not None else None)
        if window is None:
            return ExtractResult(
                table=f"{raw_schema}.{table.name}",
                source_table=source_table,
                rows=0,
                source_file_hash=None,
                watermark_ts=previous,
            )

        condition, params = _window_condition(window)
        cur.execute(_batch_statement(table, source, condition), params)
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"{source_table}: no se pudo calcular el lote")
        expected, batch_hash = row

        cur.execute(
            sql.SQL(
                "insert into {target} ({target_columns}) "
                "select {business}, %s, %s, %s, %s, %s from {source} "
                "where {condition} order by tms_row_id"
            ).format(
                target=target,
                target_columns=_column_list((*table.column_names, *_metadata_names())),
                business=_column_list(table.column_names),
                source=source,
                condition=condition,
            ),
            (run_id, table.source_system, source_table, batch_hash, ingested_at, *params),
        )
        inserted = cur.rowcount
        if inserted != expected:
            raise RuntimeError(
                f"{source_table}: se insertaron {inserted} filas y el lote tenía {expected}"
            )
        cur.execute(
            watermark.upsert_statement(meta_schema),
            (source_table, window.upper, run_id, ingested_at),
        )

    return ExtractResult(
        table=f"{raw_schema}.{table.name}",
        source_table=source_table,
        rows=inserted,
        source_file_hash=batch_hash,
        watermark_ts=window.upper,
    )


def run_tms(
    conn: psycopg.Connection,
    run_id: str | None = None,
    tms_schema: str = TMS_SCHEMA,
    raw_schema: str = RAW_SCHEMA,
    meta_schema: str = META_SCHEMA,
    tables: Sequence[RawTable] = TMS_TABLES,
) -> list[ExtractResult]:
    """Extract every ``tms`` table by watermark, one transaction per table."""
    run = run_id if run_id is not None else uuid.uuid4().hex
    ingested_at = datetime.now(UTC)
    return [
        extract_table(conn, table, run, ingested_at, tms_schema, raw_schema, meta_schema)
        for table in tables
    ]


def ingested_files_identifier(schema: str = META_SCHEMA) -> sql.Identifier:
    """Return the qualified identifier of the file registry."""
    return sql.Identifier(schema, INGESTED_FILES_TABLE)


def ingested_files_create_statement(schema: str = META_SCHEMA) -> sql.Composed:
    """Return the ``CREATE TABLE`` of the file registry."""
    return sql.SQL(
        "create table if not exists {table} (\n"
        "    target_table text not null,\n"
        "    source_file_hash text not null,\n"
        "    source_file text not null,\n"
        "    run_id text not null,\n"
        "    row_count integer not null,\n"
        "    ingested_at timestamptz not null,\n"
        "    primary key (target_table, source_file_hash)\n"
        ")"
    ).format(table=ingested_files_identifier(schema))


def registered_hashes(
    cur: psycopg.Cursor, table: RawTable, file_hash: str, meta_schema: str = META_SCHEMA
) -> set[str]:
    """Return the registered hashes of ``table`` equal to ``file_hash`` (empty if new)."""
    cur.execute(
        sql.SQL(
            "select source_file_hash from {registry} "
            "where target_table = %s and source_file_hash = %s"
        ).format(registry=ingested_files_identifier(meta_schema)),
        (table.name, file_hash),
    )
    return {row[0] for row in cur.fetchall()}


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
    raw_schema: str = RAW_SCHEMA,
    meta_schema: str = META_SCHEMA,
) -> FileResult:
    """Load one ERP file into its ``raw`` table and register it, in one transaction.

    The file is skipped if its hash is already registered for the table in
    ``meta.ingested_files``. Otherwise it is copied into a typed temporary
    table, so values are cast by PostgreSQL against the contract types, appended
    to ``raw`` with the execution metadata and registered. An advisory lock on
    the registry serializes concurrent loads of the same table.
    """
    source = Path(path)
    check_header(table, source)
    source_file_hash = file_sha256(source)
    staging = sql.Identifier(f"_stage_{table.name}")
    business = _column_list(table.column_names)
    target = table.qualified(raw_schema)
    result_table = f"{raw_schema}.{table.name}"

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "select pg_advisory_xact_lock(hashtext(%s))",
            (lock_key(meta_schema, f"{INGESTED_FILES_TABLE}.{table.name}"),),
        )
        cur.execute(table.create_statement(raw_schema))
        cur.execute(ingested_files_create_statement(meta_schema))
        if should_skip(
            source_file_hash, registered_hashes(cur, table, source_file_hash, meta_schema)
        ):
            return FileResult(
                table=result_table,
                source_file=source.name,
                source_file_hash=source_file_hash,
                rows=0,
                skipped=True,
            )
        cur.execute(_staging_statement(table, staging))
        copy_statement = sql.SQL(
            "copy {staging} ({columns}) from stdin with (format csv, header true)"
        ).format(staging=staging, columns=business)
        with cur.copy(copy_statement) as copy, source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
                copy.write(chunk)
        cur.execute(
            sql.SQL(
                "insert into {target} ({target_columns}) "
                "select {business}, %s, %s, %s, %s, %s from {staging}"
            ).format(
                target=target,
                target_columns=_column_list((*table.column_names, *_metadata_names())),
                business=business,
                staging=staging,
            ),
            (run_id, table.source_system, source.name, source_file_hash, ingested_at),
        )
        rows = cur.rowcount
        cur.execute(
            sql.SQL(
                "insert into {registry} "
                "(target_table, source_file_hash, source_file, run_id, row_count, ingested_at) "
                "values (%s, %s, %s, %s, %s, %s)"
            ).format(registry=ingested_files_identifier(meta_schema)),
            (table.name, source_file_hash, source.name, run_id, rows, ingested_at),
        )

    return FileResult(
        table=result_table,
        source_file=source.name,
        source_file_hash=source_file_hash,
        rows=rows,
    )


def run_files(
    conn: psycopg.Connection,
    directory: str | Path,
    run_id: str | None = None,
    raw_schema: str = RAW_SCHEMA,
    meta_schema: str = META_SCHEMA,
    tables: Sequence[RawTable] = FILE_TABLES,
) -> list[FileResult]:
    """Load the ``<table>*.csv`` files of ``directory``, in name order, one transaction each."""
    run = run_id if run_id is not None else uuid.uuid4().hex
    ingested_at = datetime.now(UTC)
    results = []
    for table in tables:
        for path in discover_files(directory, table.name):
            results.append(load_file(conn, table, path, run, ingested_at, raw_schema, meta_schema))
    return results


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the connectors."""
    parser = argparse.ArgumentParser(
        prog="python -m nexora.ingestion.batch.loaders",
        description=(
            "Carga el esquema raw: extrae las tablas de tms por watermark (--source tms) "
            "o carga los archivos ERP de una carpeta por hash (--source files)."
        ),
    )
    parser.add_argument(
        "--source",
        choices=("tms", "files"),
        required=True,
        help="origen: tms (tablas del esquema tms) o files (customers*.csv de --input)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="carpeta con los archivos ERP (customers*.csv). Obligatoria con --source files",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=(
            "cadena de conexión de PostgreSQL. Por defecto: NEXORA_PG_DSN con "
            "ETL_WRITER_PASSWORD del entorno o de .env"
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="identificador de la ejecución. Por defecto: un UUID nuevo",
    )
    return parser


def _print_extract(result: ExtractResult) -> None:
    if result.rows == 0:
        print(f"sin filas nuevas: {result.table} <- {result.source_table} (0 filas)")
        return
    watermark_ts = result.watermark_ts.isoformat() if result.watermark_ts else "-"
    batch_hash = (result.source_file_hash or "")[:12]
    print(
        f"extraído: {result.table} <- {result.source_table} "
        f"({result.rows} filas, sha256 {batch_hash}, watermark {watermark_ts})"
    )


def _print_file(result: FileResult) -> None:
    if result.skipped:
        print(
            f"omitido: {result.table} <- {result.source_file} "
            f"(sha256 {result.source_file_hash[:12]} ya registrado, 0 filas)"
        )
    else:
        print(
            f"cargado: {result.table} <- {result.source_file} "
            f"({result.rows} filas, sha256 {result.source_file_hash[:12]})"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one connector and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.source == "files" and args.input is None:
        parser.error("--input es obligatorio con --source files")

    run_id = args.run_id if args.run_id is not None else uuid.uuid4().hex
    dsn = args.dsn if args.dsn is not None else settings.etl_conninfo()
    print(f"run_id: {run_id}")
    with psycopg.connect(dsn) as conn:
        if args.source == "tms":
            for extracted in run_tms(conn, run_id):
                _print_extract(extracted)
        else:
            files = run_files(conn, args.input, run_id)
            if not files:
                print(f"sin archivos ERP en {args.input}")
            for loaded in files:
                _print_file(loaded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

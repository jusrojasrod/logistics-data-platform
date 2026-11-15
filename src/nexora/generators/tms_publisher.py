"""Publisher of the synthetic TMS files into the operational ``tms`` schema.

The generator writes CSV files; this module inserts the TMS entities
(``shipments``, ``carriers``, ``routes`` and ``service_types``) into
``tms.<table>`` so the batch connector can extract them by ``updated_at``
watermark. Customers belong to the ERP and are not published here.

Each ``tms`` table has a surrogate ``tms_row_id``, the business columns of the
``raw`` contract and ``updated_at``, filled by the database clock
(``clock_timestamp()``) at insert time. Dimensions are unique by their natural
id; ``tms.shipments`` keeps every copy of a ``shipment_id``, as the source does.

A cut-off date (``--until``) simulates the daily progress of the operation: a
shipment is published once its availability date is on or before the cut-off.
About 2 % of the shipments arrive late, between 2 and 7 days after their pickup
date (:func:`available_date`). All copies of a ``shipment_id`` are published
together, and a ``shipment_id`` already present in ``tms`` is never published
again. The publisher only inserts: it never updates nor deletes.

Each table is published in its own transaction, which first takes the advisory
lock ``pg_advisory_xact_lock(hashtext('<schema>.<table>'))``. The batch
extractor takes the same lock, so no row can be committed with an
``updated_at`` older than a watermark already read.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg
from psycopg import sql

from nexora.common.config import settings
from nexora.generators.config import LATE_ARRIVAL_LAG_DAYS, LATE_ARRIVAL_PCT
from nexora.ingestion.batch.loaders import (
    CARRIERS,
    ROUTES,
    SERVICE_TYPES,
    SHIPMENTS,
    RawTable,
    check_header,
)

TMS_SCHEMA: str = "tms"

SHIPMENT_ID: str = "shipment_id"
PICKUP_DATE: str = "pickup_date"


@dataclass(frozen=True)
class TmsTable:
    """An operational ``tms`` table built from the business columns of a contract."""

    contract: RawTable
    natural_key: str | None = None

    @property
    def name(self) -> str:
        return self.contract.name

    @property
    def file_name(self) -> str:
        return self.contract.file_name

    @property
    def column_names(self) -> tuple[str, ...]:
        return self.contract.column_names

    def identifier(self, schema: str = TMS_SCHEMA) -> sql.Identifier:
        return sql.Identifier(schema, self.name)

    def lock_key(self, schema: str = TMS_SCHEMA) -> str:
        """Return the text hashed into the advisory lock of this table."""
        return f"{schema}.{self.name}"

    def create_statement(self, schema: str = TMS_SCHEMA) -> sql.Composed:
        """Return the explicit ``CREATE TABLE`` of the ``tms`` table."""
        definitions = [
            sql.SQL("tms_row_id bigint generated always as identity primary key"),
            *(column.definition() for column in self.contract.columns),
            sql.SQL("updated_at timestamptz not null default clock_timestamp()"),
        ]
        if self.natural_key is not None:
            definitions.append(
                sql.SQL("unique ({key})").format(key=sql.Identifier(self.natural_key))
            )
        return sql.SQL("create table if not exists {table} (\n    {columns}\n)").format(
            table=self.identifier(schema),
            columns=sql.SQL(",\n    ").join(definitions),
        )


TMS_CARRIERS = TmsTable(CARRIERS, natural_key="carrier_id")
TMS_ROUTES = TmsTable(ROUTES, natural_key="route_id")
TMS_SERVICE_TYPES = TmsTable(SERVICE_TYPES, natural_key="service_type_id")
TMS_SHIPMENTS = TmsTable(SHIPMENTS)

# Publication order: dimensions first, then the shipments that reference them.
TMS_TABLES: tuple[TmsTable, ...] = (TMS_CARRIERS, TMS_ROUTES, TMS_SERVICE_TYPES, TMS_SHIPMENTS)


@dataclass(frozen=True)
class PublishResult:
    """Outcome of publishing one CSV file into its ``tms`` table."""

    table: str
    source_file: str
    selected: int
    inserted: int


def available_date(shipment_id: str, pickup_date: date) -> date:
    """Return the date on which a shipment becomes available in the TMS.

    The first 8 hex digits of the SHA-256 of ``shipment_id`` give an integer
    ``x``. The shipment is late when ``x % 100 < LATE_ARRIVAL_PCT``, and then it
    arrives ``min_lag + (x // 100) % (max_lag - min_lag + 1)`` days after
    ``pickup_date``; otherwise it is available on ``pickup_date``.
    """
    x = int(hashlib.sha256(shipment_id.encode("utf-8")).hexdigest()[:8], 16)
    if x % 100 >= LATE_ARRIVAL_PCT:
        return pickup_date
    min_lag, max_lag = LATE_ARRIVAL_LAG_DAYS
    return pickup_date + timedelta(days=min_lag + (x // 100) % (max_lag - min_lag + 1))


def group_available_dates(rows: Iterable[dict[str, str]]) -> dict[str, date]:
    """Return the availability date of each ``shipment_id``.

    A group is available when its last copy is, so all copies of a
    ``shipment_id`` are always published together.
    """
    dates: dict[str, date] = {}
    for row in rows:
        shipment_id = row[SHIPMENT_ID]
        current = available_date(shipment_id, date.fromisoformat(row[PICKUP_DATE]))
        previous = dates.get(shipment_id)
        if previous is None or current > previous:
            dates[shipment_id] = current
    return dates


def select_shipments(
    rows: Sequence[dict[str, str]], until: date | None = None
) -> list[dict[str, str]]:
    """Return the rows whose ``shipment_id`` group is available on or before ``until``.

    Without ``until`` every row is selected. The input order is preserved.
    """
    if until is None:
        return list(rows)
    dates = group_available_dates(rows)
    return [row for row in rows if dates[row[SHIPMENT_ID]] <= until]


def read_rows(table: TmsTable, path: str | Path) -> list[dict[str, str]]:
    """Return the rows of a CSV file after checking its header against the contract."""
    check_header(table.contract, path)
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def to_csv(columns: Sequence[str], rows: Iterable[dict[str, str]]) -> bytes:
    """Serialize rows as CSV with header; empty fields are read as NULL by ``COPY``."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[column] for column in columns])
    return buffer.getvalue().encode("utf-8")


def _staging_statement(table: TmsTable, staging: sql.Identifier) -> sql.Composed:
    definitions = [
        sql.SQL("ord bigint generated always as identity"),
        *(
            sql.SQL(" ").join([sql.Identifier(column.name), sql.SQL(column.sql_type)])
            for column in table.contract.columns
        ),
    ]
    return sql.SQL("create temporary table {staging} ({columns}) on commit drop").format(
        staging=staging,
        columns=sql.SQL(", ").join(definitions),
    )


def _insert_statement(table: TmsTable, staging: sql.Identifier, schema: str) -> sql.Composed:
    business = sql.SQL(", ").join(sql.Identifier(name) for name in table.column_names)
    source = sql.SQL(", ").join(sql.Identifier("s", name) for name in table.column_names)
    if table.natural_key is not None:
        return sql.SQL(
            "insert into {table} ({business}) select {source} from {staging} s "
            "order by s.ord on conflict ({key}) do nothing"
        ).format(
            table=table.identifier(schema),
            business=business,
            source=source,
            staging=staging,
            key=sql.Identifier(table.natural_key),
        )
    return sql.SQL(
        "insert into {table} ({business}) select {source} from {staging} s "
        "where not exists (select 1 from {table} t where t.{id} = s.{id}) "
        "order by s.ord"
    ).format(
        table=table.identifier(schema),
        business=business,
        source=source,
        staging=staging,
        id=sql.Identifier(SHIPMENT_ID),
    )


def publish_rows(
    conn: psycopg.Connection,
    table: TmsTable,
    rows: Sequence[dict[str, str]],
    source_file: str,
    schema: str = TMS_SCHEMA,
) -> PublishResult:
    """Insert the new rows of one table inside a single transaction.

    The advisory lock of the table is taken before any other statement, so
    ``updated_at`` is evaluated after the lock is held.
    """
    staging = sql.Identifier(f"_publish_{table.name}")
    business = sql.SQL(", ").join(sql.Identifier(name) for name in table.column_names)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (table.lock_key(schema),))
        cur.execute(table.create_statement(schema))
        cur.execute(_staging_statement(table, staging))
        copy_statement = sql.SQL(
            "copy {staging} ({columns}) from stdin with (format csv, header true)"
        ).format(staging=staging, columns=business)
        with cur.copy(copy_statement) as copy:
            copy.write(to_csv(table.column_names, rows))
        cur.execute(_insert_statement(table, staging, schema))
        inserted = cur.rowcount
    return PublishResult(
        table=f"{schema}.{table.name}",
        source_file=source_file,
        selected=len(rows),
        inserted=inserted,
    )


def publish_directory(
    conn: psycopg.Connection,
    directory: str | Path,
    until: date | None = None,
    schema: str = TMS_SCHEMA,
    tables: Sequence[TmsTable] = TMS_TABLES,
) -> list[PublishResult]:
    """Publish the TMS files of ``directory`` into ``schema``, up to ``until``."""
    base = Path(directory)
    missing = [table.file_name for table in tables if not (base / table.file_name).is_file()]
    if missing:
        raise FileNotFoundError(f"faltan archivos en {base}: {', '.join(missing)}")

    results = []
    for table in tables:
        rows = read_rows(table, base / table.file_name)
        if table.name == TMS_SHIPMENTS.name:
            rows = select_shipments(rows, until)
        results.append(publish_rows(conn, table, rows, table.file_name, schema))
    return results


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the publisher."""
    parser = argparse.ArgumentParser(
        prog="python -m nexora.generators.tms_publisher",
        description=(
            "Publica los CSV de envíos y dimensiones TMS de una carpeta en el esquema tms. "
            "Solo inserta filas nuevas."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="carpeta con shipments.csv, carriers.csv, routes.csv y service_types.csv",
    )
    parser.add_argument(
        "--until",
        type=date.fromisoformat,
        default=None,
        help=(
            "fecha de corte YYYY-MM-DD: publica los envíos disponibles hasta ese día. "
            "Por defecto: todos"
        ),
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=(
            "cadena de conexión de PostgreSQL. Por defecto: NEXORA_TMS_DSN con "
            "TMS_WRITER_PASSWORD del entorno o de .env"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the publisher and return the process exit code."""
    args = build_parser().parse_args(argv)
    dsn = args.dsn if args.dsn is not None else settings.tms_conninfo()
    with psycopg.connect(dsn) as conn:
        results = publish_directory(conn, args.input, until=args.until)
    for result in results:
        print(
            f"publicado: {result.table} <- {result.source_file} "
            f"({result.inserted} filas nuevas de {result.selected})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

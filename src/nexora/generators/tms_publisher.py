"""Publisher of the synthetic TMS files into the operational ``tms`` schema.

The generator writes CSV files; this module inserts every TMS entity into
``tms.<table>`` so the batch connector can extract them by ``updated_at``
watermark: the dimensions (``carriers``, ``routes``, ``service_types``), the
``shipments``, the fleet catalogs (``vehicles``, ``drivers``), the ``trips``
with their ``trip_legs``, the ``shipment_legs`` that place a shipment on a leg,
and the ``shipment_stops``, ``shipment_events``, ``fuel_transactions``,
``carrier_tenders`` and ``carrier_incidents``. Customers belong to the ERP and
are not published here.

Each ``tms`` table has a surrogate ``tms_row_id``, the business columns of the
``raw`` contract and ``updated_at``, filled by the database clock
(``clock_timestamp()``) at insert time. Dimensions and fleet catalogs are unique
by their natural id. The other tables keep every copy of a row, as the source
does, and the new ones get a non-unique index on their natural key;
``tms.shipments`` keeps the definition it always had, without that index.

A cut-off date (``--until``) simulates the daily progress of the operation. A
row is published once its availability date is on or before the cut-off:

* dimensions and fleet catalogs are always available;
* a shipment is available on its pickup date, or between 2 and 7 days later for
  about 2 % of them (:func:`available_date`); all copies of a ``shipment_id``
  share the date of the group;
* trips by the UTC date of ``start_ts``, and their legs with the trip;
* fuel purchases by the UTC date of ``txn_ts``, never before their trip starts;
* stops and shipment legs with their shipment;
* events (``event_ts``), tenders (``offered_ts``) and incidents
  (``reported_ts``) on the UTC date of that timestamp plus the delay of their
  shipment, and never before the shipment itself. A timestamp after the end of
  the cut-off date, which only a defective row can carry, takes the date of the
  shipment.

Without ``--until`` every row is published. A row whose natural key is already
present in ``tms`` is never published again, and the copies of a row, written
right after it, are always published together. The publisher only inserts.

Each table is published in its own transaction, which first takes the advisory
lock ``pg_advisory_xact_lock(hashtext('<schema>.<table>'))``. The batch
extractor takes the same lock, so no row can be committed with an
``updated_at`` older than a watermark already read. Inside the transaction the
CSV file is read in blocks of ``PUBLISH_BLOCK_ROWS`` rows: each block is copied
into a temporary table and inserted from there, so memory does not grow with
the size of the file.

A folder whose ``defect_manifest.json`` declares the ``benchmark`` scale is
rejected before connecting: that profile is only generated to files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg import sql

from nexora.common.config import settings
from nexora.generators.config import LATE_ARRIVAL_LAG_DAYS, LATE_ARRIVAL_PCT
from nexora.generators.defects import MANIFEST_FILE_NAME
from nexora.generators.shipments import CUTOFF_END
from nexora.ingestion.batch.loaders import (
    CARRIER_INCIDENTS,
    CARRIER_TENDERS,
    CARRIERS,
    DRIVERS,
    FUEL_TRANSACTIONS,
    ROUTES,
    SERVICE_TYPES,
    SHIPMENT_EVENTS,
    SHIPMENT_LEGS,
    SHIPMENT_STOPS,
    SHIPMENTS,
    TRIP_LEGS,
    TRIPS,
    VEHICLES,
    RawTable,
)
from nexora.ingestion.batch.readers import iter_row_blocks

TMS_SCHEMA: str = "tms"

# Rows per block read from a CSV file and copied into the temporary table. A
# block only grows past this size to keep the copies of a row together.
PUBLISH_BLOCK_ROWS: int = 50_000

# Profiles that are generated to files but never published into tms.
UNPUBLISHABLE_SCALES: frozenset[str] = frozenset({"benchmark"})

SHIPMENT_ID: str = "shipment_id"
PICKUP_DATE: str = "pickup_date"
TRIP_ID: str = "trip_id"
TRIP_START: str = "start_ts"

# Availability rules of a table under --until.
ALWAYS: str = "always"
SHIPMENT_GROUP: str = "shipment_group"
OWN_DATE: str = "own_date"
WITH_TRIP: str = "with_trip"
WITH_SHIPMENT: str = "with_shipment"
SHIPMENT_CHILD: str = "shipment_child"

_DATED_RULES: frozenset[str] = frozenset({OWN_DATE, SHIPMENT_CHILD})
_RULES: frozenset[str] = frozenset(
    {ALWAYS, SHIPMENT_GROUP, OWN_DATE, WITH_TRIP, WITH_SHIPMENT, SHIPMENT_CHILD}
)


@dataclass(frozen=True)
class TmsTable:
    """An operational ``tms`` table built from the business columns of a contract.

    ``natural_key`` identifies a row of the source. With ``unique`` the table
    has a unique constraint on it; with ``indexed`` a non-unique index. The
    availability rule (``availability`` and, when it depends on a timestamp of
    the row, ``date_column``) decides when a row is published under ``--until``.
    """

    contract: RawTable
    natural_key: tuple[str, ...]
    unique: bool = False
    indexed: bool = False
    availability: str = ALWAYS
    date_column: str | None = None

    def __post_init__(self) -> None:
        if not self.natural_key or any(
            column not in self.contract.column_names for column in self.natural_key
        ):
            raise ValueError(f"{self.name}: clave natural fuera del contrato")
        if self.unique and self.indexed:
            raise ValueError(f"{self.name}: unique ya crea su índice")
        if self.availability not in _RULES:
            raise ValueError(f"{self.name}: regla de disponibilidad desconocida")
        if (self.availability in _DATED_RULES) != (self.date_column is not None):
            raise ValueError(f"{self.name}: columna de fecha incoherente con la regla")
        if self.date_column is not None and self.date_column not in self.contract.column_names:
            raise ValueError(f"{self.name}: columna de fecha fuera del contrato")

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

    def _key_list(self) -> sql.Composed:
        return sql.SQL(", ").join(sql.Identifier(column) for column in self.natural_key)

    def create_statement(self, schema: str = TMS_SCHEMA) -> sql.Composed:
        """Return the explicit ``CREATE TABLE`` of the ``tms`` table."""
        definitions = [
            sql.SQL("tms_row_id bigint generated always as identity primary key"),
            *(column.definition() for column in self.contract.columns),
            sql.SQL("updated_at timestamptz not null default clock_timestamp()"),
        ]
        if self.unique:
            definitions.append(sql.SQL("unique ({key})").format(key=self._key_list()))
        return sql.SQL("create table if not exists {table} (\n    {columns}\n)").format(
            table=self.identifier(schema),
            columns=sql.SQL(",\n    ").join(definitions),
        )

    def index_statement(self, schema: str = TMS_SCHEMA) -> sql.Composed | None:
        """Return the non-unique index on the natural key, or ``None`` without one."""
        if not self.indexed:
            return None
        return sql.SQL("create index if not exists {index} on {table} ({key})").format(
            index=sql.Identifier(f"{self.name}_natural_key_idx"),
            table=self.identifier(schema),
            key=self._key_list(),
        )

    def ddl_statements(self, schema: str = TMS_SCHEMA) -> tuple[sql.Composed, ...]:
        """Return the statements that create the table and, if any, its index."""
        index = self.index_statement(schema)
        create = self.create_statement(schema)
        return (create,) if index is None else (create, index)


TMS_CARRIERS = TmsTable(CARRIERS, ("carrier_id",), unique=True)
TMS_ROUTES = TmsTable(ROUTES, ("route_id",), unique=True)
TMS_SERVICE_TYPES = TmsTable(SERVICE_TYPES, ("service_type_id",), unique=True)
TMS_SHIPMENTS = TmsTable(SHIPMENTS, (SHIPMENT_ID,), availability=SHIPMENT_GROUP)
TMS_VEHICLES = TmsTable(VEHICLES, ("vehicle_id",), unique=True)
TMS_DRIVERS = TmsTable(DRIVERS, ("driver_id",), unique=True)
TMS_TRIPS = TmsTable(TRIPS, (TRIP_ID,), indexed=True, availability=OWN_DATE, date_column=TRIP_START)
TMS_TRIP_LEGS = TmsTable(TRIP_LEGS, (TRIP_ID, "leg_sequence"), indexed=True, availability=WITH_TRIP)
TMS_SHIPMENT_LEGS = TmsTable(
    SHIPMENT_LEGS,
    (SHIPMENT_ID, TRIP_ID, "leg_sequence"),
    indexed=True,
    availability=WITH_SHIPMENT,
)
TMS_SHIPMENT_STOPS = TmsTable(
    SHIPMENT_STOPS, (SHIPMENT_ID, "stop_sequence"), indexed=True, availability=WITH_SHIPMENT
)
TMS_SHIPMENT_EVENTS = TmsTable(
    SHIPMENT_EVENTS,
    ("event_id",),
    indexed=True,
    availability=SHIPMENT_CHILD,
    date_column="event_ts",
)
TMS_FUEL_TRANSACTIONS = TmsTable(
    FUEL_TRANSACTIONS,
    ("fuel_txn_id",),
    indexed=True,
    availability=OWN_DATE,
    date_column="txn_ts",
)
TMS_CARRIER_TENDERS = TmsTable(
    CARRIER_TENDERS,
    ("tender_id",),
    indexed=True,
    availability=SHIPMENT_CHILD,
    date_column="offered_ts",
)
TMS_CARRIER_INCIDENTS = TmsTable(
    CARRIER_INCIDENTS,
    ("incident_id",),
    indexed=True,
    availability=SHIPMENT_CHILD,
    date_column="reported_ts",
)

# Publication order, the same as the extraction order: dimensions and shipments
# first, then the fleet catalogs, trips and legs, and last the entities that
# hang from shipments and trips.
TMS_TABLES: tuple[TmsTable, ...] = (
    TMS_CARRIERS,
    TMS_ROUTES,
    TMS_SERVICE_TYPES,
    TMS_SHIPMENTS,
    TMS_VEHICLES,
    TMS_DRIVERS,
    TMS_TRIPS,
    TMS_TRIP_LEGS,
    TMS_SHIPMENT_LEGS,
    TMS_SHIPMENT_STOPS,
    TMS_SHIPMENT_EVENTS,
    TMS_FUEL_TRANSACTIONS,
    TMS_CARRIER_TENDERS,
    TMS_CARRIER_INCIDENTS,
)


@dataclass(frozen=True)
class PublishResult:
    """Outcome of publishing one CSV file into its ``tms`` table."""

    table: str
    source_file: str
    selected: int
    inserted: int


class UnpublishableScaleError(ValueError):
    """The folder holds a profile that is only generated to files."""


def arrival_lag(shipment_id: str) -> timedelta:
    """Return how long after its pickup date a shipment reaches the TMS.

    The first 8 hex digits of the SHA-256 of ``shipment_id`` give an integer
    ``x``. The shipment is late when ``x % 100 < LATE_ARRIVAL_PCT``, and then it
    arrives ``min_lag + (x // 100) % (max_lag - min_lag + 1)`` days after its
    pickup date; otherwise the lag is zero.
    """
    x = int(hashlib.sha256(shipment_id.encode("utf-8")).hexdigest()[:8], 16)
    if x % 100 >= LATE_ARRIVAL_PCT:
        return timedelta(0)
    min_lag, max_lag = LATE_ARRIVAL_LAG_DAYS
    return timedelta(days=min_lag + (x // 100) % (max_lag - min_lag + 1))


def available_date(shipment_id: str, pickup_date: date) -> date:
    """Return the date on which a shipment becomes available in the TMS.

    It is ``pickup_date`` plus :func:`arrival_lag` of ``shipment_id``.
    """
    return pickup_date + arrival_lag(shipment_id)


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


def parse_ts(value: str) -> datetime:
    """Parse an ISO 8601 timestamp of the CSV files as an aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"marca de tiempo sin zona horaria: {value}")
    return parsed.astimezone(UTC)


def utc_date(value: str) -> date:
    """Return the UTC calendar date of an ISO 8601 timestamp."""
    return parse_ts(value).date()


def shipment_child_date(timestamp: str, shipment_id: str, shipment_ready: date) -> date:
    """Return the availability date of a row that hangs from a shipment.

    It is the UTC date of ``timestamp`` plus the delay of the shipment, and never
    earlier than ``shipment_ready``. A timestamp after the end of the cut-off
    date takes ``shipment_ready``.
    """
    moment = parse_ts(timestamp)
    if moment > CUTOFF_END:
        return shipment_ready
    return max(moment.date() + arrival_lag(shipment_id), shipment_ready)


@dataclass
class AvailabilityIndex:
    """Availability dates of shipments and trips, read lazily from a folder.

    Only the identifiers and one date per shipment or trip are kept in memory.
    """

    directory: Path
    block_rows: int = PUBLISH_BLOCK_ROWS
    _shipments: dict[str, date] | None = field(default=None, repr=False)
    _trips: dict[str, date] | None = field(default=None, repr=False)

    def shipment_dates(self) -> dict[str, date]:
        """Return ``shipment_id`` → availability date of its group of copies."""
        if self._shipments is None:
            dates: dict[str, date] = {}
            path = self.directory / SHIPMENTS.file_name
            for block in iter_row_blocks(path, SHIPMENTS, self.block_rows, (SHIPMENT_ID,)):
                for shipment_id, ready in group_available_dates(block).items():
                    previous = dates.get(shipment_id)
                    if previous is None or ready > previous:
                        dates[shipment_id] = ready
            self._shipments = dates
        return self._shipments

    def trip_dates(self) -> dict[str, date]:
        """Return ``trip_id`` → UTC date of the trip ``start_ts``."""
        if self._trips is None:
            dates: dict[str, date] = {}
            path = self.directory / TRIPS.file_name
            for block in iter_row_blocks(path, TRIPS, self.block_rows, (TRIP_ID,)):
                for row in block:
                    dates[row[TRIP_ID]] = utc_date(row[TRIP_START])
            self._trips = dates
        return self._trips

    def prepare(self, table: TmsTable) -> None:
        """Load the dates that the rule of ``table`` needs."""
        if table.availability in {SHIPMENT_GROUP, WITH_SHIPMENT, SHIPMENT_CHILD}:
            self.shipment_dates()
        elif table.availability == WITH_TRIP:
            self.trip_dates()

    def _shipment(self, table: TmsTable, shipment_id: str) -> date:
        try:
            return self.shipment_dates()[shipment_id]
        except KeyError as error:
            raise ValueError(
                f"{table.file_name}: {SHIPMENT_ID} {shipment_id} sin fila en "
                f"{SHIPMENTS.file_name}"
            ) from error

    def _trip(self, table: TmsTable, trip_id: str) -> date:
        try:
            return self.trip_dates()[trip_id]
        except KeyError as error:
            raise ValueError(
                f"{table.file_name}: {TRIP_ID} {trip_id} sin fila en {TRIPS.file_name}"
            ) from error

    def available_on(self, table: TmsTable, row: dict[str, str]) -> date | None:
        """Return the availability date of ``row``; ``None`` means always available."""
        rule = table.availability
        if rule == ALWAYS:
            return None
        if rule == SHIPMENT_GROUP:
            return self.shipment_dates()[row[SHIPMENT_ID]]
        if rule == WITH_SHIPMENT:
            return self._shipment(table, row[SHIPMENT_ID])
        if rule == WITH_TRIP:
            return self._trip(table, row[TRIP_ID])
        if table.date_column is None:
            raise ValueError(f"{table.name}: la regla {rule} necesita una columna de fecha")
        if rule == OWN_DATE:
            return utc_date(row[table.date_column])
        shipment_id = row[SHIPMENT_ID]
        return shipment_child_date(
            row[table.date_column], shipment_id, self._shipment(table, shipment_id)
        )

    def select(
        self, table: TmsTable, rows: Iterable[dict[str, str]], until: date | None
    ) -> list[dict[str, str]]:
        """Return the rows available on or before ``until``, in input order."""
        if until is None or table.availability == ALWAYS:
            return list(rows)
        selected = []
        for row in rows:
            ready = self.available_on(table, row)
            if ready is None or ready <= until:
                selected.append(row)
        return selected


def to_csv(columns: Sequence[str], rows: Iterable[dict[str, str]]) -> bytes:
    """Serialize rows as CSV with header; empty fields are read as NULL by ``COPY``."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[column] for column in columns])
    return buffer.getvalue().encode("utf-8")


def staging_identifier(table: TmsTable) -> sql.Identifier:
    """Return the temporary table that receives the blocks of ``table``."""
    return sql.Identifier(f"_publish_{table.name}")


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


def _clear_staging_statement(staging: sql.Identifier) -> sql.Composed:
    return sql.SQL("truncate {staging}").format(staging=staging)


def insert_statement(table: TmsTable, staging: sql.Identifier, schema: str) -> sql.Composed:
    """Return the insert of the staged rows whose natural key is not in ``tms`` yet."""
    business = sql.SQL(", ").join(sql.Identifier(name) for name in table.column_names)
    source = sql.SQL(", ").join(sql.Identifier("s", name) for name in table.column_names)
    if table.unique:
        return sql.SQL(
            "insert into {table} ({business}) select {source} from {staging} s "
            "order by s.ord on conflict ({key}) do nothing"
        ).format(
            table=table.identifier(schema),
            business=business,
            source=source,
            staging=staging,
            key=sql.SQL(", ").join(sql.Identifier(column) for column in table.natural_key),
        )
    match = sql.SQL(" and ").join(
        sql.SQL("t.{column} = s.{column}").format(column=sql.Identifier(column))
        for column in table.natural_key
    )
    return sql.SQL(
        "insert into {table} ({business}) select {source} from {staging} s "
        "where not exists (select 1 from {table} t where {match}) "
        "order by s.ord"
    ).format(
        table=table.identifier(schema),
        business=business,
        source=source,
        staging=staging,
        match=match,
    )


def publish_blocks(
    conn: psycopg.Connection,
    table: TmsTable,
    blocks: Iterable[Sequence[dict[str, str]]],
    source_file: str,
    schema: str = TMS_SCHEMA,
) -> PublishResult:
    """Insert the new rows of one table, block by block, in a single transaction.

    The advisory lock of the table is taken before any other statement, so
    ``updated_at`` is evaluated after the lock is held. Each non-empty block is
    copied into the temporary table, inserted from there and cleared before
    the next one. The copies of a row must travel in the same block.
    """
    staging = staging_identifier(table)
    business = sql.SQL(", ").join(sql.Identifier(name) for name in table.column_names)
    copy_statement = sql.SQL(
        "copy {staging} ({columns}) from stdin with (format csv, header true)"
    ).format(staging=staging, columns=business)
    selected = 0
    inserted = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (table.lock_key(schema),))
        for statement in table.ddl_statements(schema):
            cur.execute(statement)
        cur.execute(_staging_statement(table, staging))
        for rows in blocks:
            if not rows:
                continue
            with cur.copy(copy_statement) as copy:
                copy.write(to_csv(table.column_names, rows))
            cur.execute(insert_statement(table, staging, schema))
            selected += len(rows)
            inserted += cur.rowcount
            cur.execute(_clear_staging_statement(staging))
    return PublishResult(
        table=f"{schema}.{table.name}",
        source_file=source_file,
        selected=selected,
        inserted=inserted,
    )


def read_scale(directory: str | Path) -> str | None:
    """Return the ``scale`` declared by the manifest of ``directory``, if any."""
    path = Path(directory) / MANIFEST_FILE_NAME
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    scale = manifest.get("scale") if isinstance(manifest, dict) else None
    return scale if isinstance(scale, str) else None


def check_publishable(directory: str | Path) -> None:
    """Fail if the manifest of ``directory`` declares a profile that is never published."""
    scale = read_scale(directory)
    if scale in UNPUBLISHABLE_SCALES:
        raise UnpublishableScaleError(
            f"{Path(directory)}: el perfil {scale} solo se genera a archivos; "
            "no se publica en tms"
        )


def publish_directory(
    conn: psycopg.Connection,
    directory: str | Path,
    until: date | None = None,
    schema: str = TMS_SCHEMA,
    tables: Sequence[TmsTable] = TMS_TABLES,
    block_rows: int = PUBLISH_BLOCK_ROWS,
) -> list[PublishResult]:
    """Publish the TMS files of ``directory`` into ``schema``, up to ``until``."""
    base = Path(directory)
    check_publishable(base)
    missing = [table.file_name for table in tables if not (base / table.file_name).is_file()]
    if missing:
        raise FileNotFoundError(f"faltan archivos en {base}: {', '.join(missing)}")

    index = AvailabilityIndex(base, block_rows)
    results = []
    for table in tables:
        if until is not None:
            index.prepare(table)
        blocks = (
            index.select(table, block, until)
            for block in iter_row_blocks(
                base / table.file_name, table.contract, block_rows, table.natural_key
            )
        )
        results.append(publish_blocks(conn, table, blocks, table.file_name, schema))
    return results


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the publisher."""
    parser = argparse.ArgumentParser(
        prog="python -m nexora.generators.tms_publisher",
        description=(
            "Publica en el esquema tms los CSV de todas las entidades TMS de una carpeta: "
            "envíos, dimensiones, flota, viajes, tramos, paradas, eventos, combustible, "
            "ofertas e incidencias. Solo inserta filas nuevas. Rechaza el perfil benchmark."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help=(
            "carpeta con "
            + ", ".join(table.file_name for table in TMS_TABLES[:-1])
            + f" y {TMS_TABLES[-1].file_name}"
        ),
    )
    parser.add_argument(
        "--until",
        type=date.fromisoformat,
        default=None,
        help=(
            "fecha de corte YYYY-MM-DD: publica las filas disponibles hasta ese día. "
            "Por defecto: todas"
        ),
    )
    parser.add_argument(
        "--schema",
        default=TMS_SCHEMA,
        help=f"esquema de destino. Por defecto: {TMS_SCHEMA}",
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
    try:
        check_publishable(args.input)
    except UnpublishableScaleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    dsn = args.dsn if args.dsn is not None else settings.tms_conninfo()
    with psycopg.connect(dsn) as conn:
        results = publish_directory(conn, args.input, until=args.until, schema=args.schema)
    for result in results:
        print(
            f"publicado: {result.table} <- {result.source_file} "
            f"({result.inserted} filas nuevas de {result.selected})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

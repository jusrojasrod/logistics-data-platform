"""Pruebas del publicador TMS: tablas y DDL, reglas de disponibilidad, publicación
por bloques con una conexión simulada y rechazo del perfil ``benchmark``.

Sin base de datos: la conexión registra las sentencias en memoria.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nexora.generators import tms_publisher
from nexora.generators.config import IMPOSSIBLE_DATE_SHIFT_DAYS, LATE_ARRIVAL_LAG_DAYS
from nexora.generators.dataset import write_dataset
from nexora.generators.defects import inject_defects
from nexora.generators.shipments import CUTOFF_END, generate_shipments
from nexora.generators.tms_publisher import (
    TMS_CARRIER_INCIDENTS,
    TMS_CARRIER_TENDERS,
    TMS_FUEL_TRANSACTIONS,
    TMS_SHIPMENT_EVENTS,
    TMS_SHIPMENT_LEGS,
    TMS_SHIPMENT_STOPS,
    TMS_SHIPMENTS,
    TMS_TABLES,
    TMS_TRIP_LEGS,
    TMS_TRIPS,
    AvailabilityIndex,
    TmsTable,
    UnpublishableScaleError,
    arrival_lag,
    available_date,
    build_parser,
    check_publishable,
    group_available_dates,
    publish_blocks,
    publish_directory,
    select_shipments,
)
from nexora.ingestion.batch import loaders


def _late_rows(rows: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if available_date(row["shipment_id"], date.fromisoformat(row["pickup_date"]))
        != date.fromisoformat(row["pickup_date"])
    ]


def test_available_date_is_deterministic() -> None:
    pickup = date(2026, 11, 1)
    first = [available_date(f"SHP-{i:06d}", pickup) for i in range(1, 501)]
    second = [available_date(f"SHP-{i:06d}", pickup) for i in range(1, 501)]
    assert first == second


def test_available_date_lag_within_range() -> None:
    pickup = date(2026, 11, 1)
    min_lag, max_lag = LATE_ARRIVAL_LAG_DAYS
    lags = {(available_date(f"SHP-{i:06d}", pickup) - pickup).days for i in range(1, 5001)}
    assert 0 in lags
    assert lags - {0}
    assert all(lag == 0 or min_lag <= lag <= max_lag for lag in lags)


def test_late_share_between_one_and_three_percent() -> None:
    rows = generate_shipments(2000, 42)
    share = len(_late_rows(rows)) / len(rows)
    assert 0.01 <= share <= 0.03


def test_copies_of_a_shipment_share_the_date() -> None:
    rows = inject_defects(generate_shipments(500, 42, 1), 42).rows
    ids = [row["shipment_id"] for row in rows]
    copied = {shipment_id for shipment_id in ids if ids.count(shipment_id) > 1}
    assert copied
    dates = group_available_dates(rows)
    for row in rows:
        if row["shipment_id"] in copied:
            own = available_date(row["shipment_id"], date.fromisoformat(row["pickup_date"]))
            assert own == dates[row["shipment_id"]]


def test_select_shipments_keeps_groups_together() -> None:
    rows = inject_defects(generate_shipments(500, 42, 1), 42).rows
    until = date(2026, 11, 7)
    selected = select_shipments(rows, until)
    selected_ids = {row["shipment_id"] for row in selected}
    for row in rows:
        assert (row["shipment_id"] in selected_ids) == (
            group_available_dates([row])[row["shipment_id"]] <= until
        )
    assert sum(row["shipment_id"] in selected_ids for row in rows) == len(selected)
    assert 0 < len(selected) < len(rows)


def test_select_shipments_without_until_keeps_all_rows() -> None:
    rows = generate_shipments(50, 42)
    assert select_shipments(rows) == list(rows)


def test_late_rows_wait_for_their_date() -> None:
    rows = generate_shipments(2000, 42)
    late = _late_rows(rows)
    assert late
    row = late[0]
    pickup = date.fromisoformat(row["pickup_date"])
    ready = available_date(row["shipment_id"], pickup)
    assert row not in select_shipments(rows, ready - date.resolution)
    assert row in select_shipments(rows, ready)


def test_parser_reads_until_as_date() -> None:
    args = build_parser().parse_args(["--input", "data/demo", "--until", "2026-11-03"])
    assert args.until == date(2026, 11, 3)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--input", "data/demo", "--until", "03/11/2026"])


# --- Tablas y DDL ---------------------------------------------------------------

NEW_TABLES = (
    "vehicles",
    "drivers",
    "trips",
    "trip_legs",
    "shipment_legs",
    "shipment_stops",
    "shipment_events",
    "fuel_transactions",
    "carrier_tenders",
    "carrier_incidents",
)

# Definición de las cuatro tablas que ya existían en tms, carácter a carácter.
EXISTING_DDL = {
    "carriers": (
        'create table if not exists "tms"."carriers" (\n'
        "    tms_row_id bigint generated always as identity primary key,\n"
        '    "carrier_id" text not null,\n'
        '    "carrier_name" text not null,\n'
        '    "carrier_type" text not null,\n'
        '    "service_level" text not null,\n'
        '    "rate_index" numeric(6,2) not null,\n'
        "    updated_at timestamptz not null default clock_timestamp(),\n"
        '    unique ("carrier_id")\n'
        ")"
    ),
    "routes": (
        'create table if not exists "tms"."routes" (\n'
        "    tms_row_id bigint generated always as identity primary key,\n"
        '    "route_id" text not null,\n'
        '    "origin_city" text not null,\n'
        '    "destination_city" text not null,\n'
        '    "distance_km" numeric(8,1) not null,\n'
        '    "estimated_hours" numeric(6,1) not null,\n'
        '    "road_type" text not null,\n'
        "    updated_at timestamptz not null default clock_timestamp(),\n"
        '    unique ("route_id")\n'
        ")"
    ),
    "service_types": (
        'create table if not exists "tms"."service_types" (\n'
        "    tms_row_id bigint generated always as identity primary key,\n"
        '    "service_type_id" text not null,\n'
        '    "service_type_name" text not null,\n'
        '    "sla_hours" integer not null,\n'
        "    updated_at timestamptz not null default clock_timestamp(),\n"
        '    unique ("service_type_id")\n'
        ")"
    ),
    "shipments": (
        'create table if not exists "tms"."shipments" (\n'
        "    tms_row_id bigint generated always as identity primary key,\n"
        '    "shipment_id" text not null,\n'
        '    "customer_id" text,\n'
        '    "carrier_id" text,\n'
        '    "route_id" text not null,\n'
        '    "service_type_id" text not null,\n'
        '    "pickup_ts" timestamptz not null,\n'
        '    "pickup_date" date not null,\n'
        '    "promised_delivery_ts" timestamptz,\n'
        '    "picked_up_at" timestamptz not null,\n'
        '    "delivered_at" timestamptz,\n'
        '    "ordered_qty" integer not null check (ordered_qty >= 1),\n'
        '    "delivered_qty" integer,\n'
        '    "ordered_weight_kg" numeric(12,3) not null check (ordered_weight_kg > 0),\n'
        '    "delivered_weight_kg" numeric(12,3),\n'
        '    "definitive_failure_flag" boolean not null,\n'
        "    updated_at timestamptz not null default clock_timestamp()\n"
        ")"
    ),
}


def by_name(name: str) -> TmsTable:
    return next(table for table in TMS_TABLES if table.name == name)


def ddl_text(table: TmsTable, schema: str = "tms") -> list[str]:
    return [statement.as_string(None) for statement in table.ddl_statements(schema)]


def test_tables_follow_the_extraction_order_and_contracts() -> None:
    assert [table.name for table in TMS_TABLES] == [table.name for table in loaders.TMS_TABLES]
    assert [table.name for table in TMS_TABLES] == [
        "carriers",
        "routes",
        "service_types",
        "shipments",
        *NEW_TABLES,
    ]
    for table, contract in zip(TMS_TABLES, loaders.TMS_TABLES, strict=True):
        assert table.contract is contract
        assert table.contract in loaders.RAW_TABLES
    assert TMS_SHIPMENTS.lock_key() == "tms.shipments"
    assert TMS_SHIPMENT_EVENTS.lock_key("it_x_tms") == "it_x_tms.shipment_events"


def test_index_only_on_the_eight_new_tables_without_unique() -> None:
    indexed = [table.name for table in TMS_TABLES if table.indexed]
    unique = [table.name for table in TMS_TABLES if table.unique]
    assert indexed == list(NEW_TABLES[2:])
    assert unique == ["carriers", "routes", "service_types", "vehicles", "drivers"]
    for table in TMS_TABLES:
        statements = ddl_text(table)
        indexes = [text for text in statements if text.startswith("create index")]
        assert len(indexes) == (1 if table.indexed else 0)
        assert ("unique (" in statements[0]) == table.unique


def test_ddl_of_existing_tables_is_unchanged_and_without_index() -> None:
    for name, expected in EXISTING_DDL.items():
        statements = ddl_text(by_name(name))
        assert statements == [expected]
        assert all("create index" not in text for text in statements)


def test_ddl_of_indexed_and_unique_new_tables() -> None:
    trip_legs = ddl_text(TMS_TRIP_LEGS, "it_x_tms")
    assert trip_legs[0].startswith('create table if not exists "it_x_tms"."trip_legs" (')
    assert '"arrival_ts" timestamptz,' in trip_legs[0]
    assert '"departure_ts" timestamptz not null,' in trip_legs[0]
    assert "unique" not in trip_legs[0]
    assert trip_legs[1] == (
        'create index if not exists "trip_legs_natural_key_idx" '
        'on "it_x_tms"."trip_legs" ("trip_id", "leg_sequence")'
    )
    vehicles = ddl_text(by_name("vehicles"))
    assert len(vehicles) == 1
    assert vehicles[0].endswith('    unique ("vehicle_id")\n)')
    drivers = ddl_text(by_name("drivers"))
    assert drivers[0].endswith('    unique ("driver_id")\n)')


def test_insert_statements_by_natural_key() -> None:
    staging = tms_publisher.staging_identifier(TMS_SHIPMENT_LEGS)
    legs = tms_publisher.insert_statement(TMS_SHIPMENT_LEGS, staging, "tms").as_string(None)
    assert (
        'where not exists (select 1 from "tms"."shipment_legs" t where '
        't."shipment_id" = s."shipment_id" and t."trip_id" = s."trip_id" and '
        't."leg_sequence" = s."leg_sequence")'
    ) in legs
    vehicles = by_name("vehicles")
    text = tms_publisher.insert_statement(
        vehicles, tms_publisher.staging_identifier(vehicles), "tms"
    ).as_string(None)
    assert text.endswith('on conflict ("vehicle_id") do nothing')


def test_table_definition_is_validated() -> None:
    with pytest.raises(ValueError, match="clave natural"):
        TmsTable(loaders.TRIPS, ("missing",))
    with pytest.raises(ValueError, match="unique"):
        TmsTable(loaders.TRIPS, ("trip_id",), unique=True, indexed=True)
    with pytest.raises(ValueError, match="columna de fecha"):
        TmsTable(loaders.TRIPS, ("trip_id",), availability=tms_publisher.OWN_DATE)


# --- Disponibilidad -------------------------------------------------------------

SEED = 42


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("publish")
    write_dataset(out, "demo", 500, SEED, 1)
    return out


@pytest.fixture(scope="module")
def index(dataset: Path) -> AvailabilityIndex:
    return AvailabilityIndex(dataset)


def read_csv(directory: Path, table: TmsTable) -> list[dict[str, str]]:
    with (directory / table.file_name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def utc_day(value: str) -> date:
    return datetime.fromisoformat(value).astimezone(UTC).date()


def late_shipment(index: AvailabilityIndex, rows: list[dict[str, str]]) -> dict[str, str]:
    for row in rows:
        if arrival_lag(row["shipment_id"]) > timedelta(0):
            return row
    raise AssertionError("sin envíos tardíos en la muestra")


def test_index_dates_match_the_shipment_rule(dataset: Path, index: AvailabilityIndex) -> None:
    rows = read_csv(dataset, TMS_SHIPMENTS)
    assert index.shipment_dates() == group_available_dates(rows)
    trips = read_csv(dataset, TMS_TRIPS)
    assert index.trip_dates() == {row["trip_id"]: utc_day(row["start_ts"]) for row in trips}
    for row in rows[:50]:
        pickup = date.fromisoformat(row["pickup_date"])
        assert available_date(row["shipment_id"], pickup) == pickup + arrival_lag(
            row["shipment_id"]
        )


def test_event_of_a_late_shipment_is_not_published_before_it(
    dataset: Path, index: AvailabilityIndex
) -> None:
    events = read_csv(dataset, TMS_SHIPMENT_EVENTS)
    late_ids = {
        row["shipment_id"] for row in events if arrival_lag(row["shipment_id"]) > timedelta(0)
    }
    assert late_ids
    ready_of = index.shipment_dates()
    for row in events:
        ready = index.available_on(TMS_SHIPMENT_EVENTS, row)
        assert ready is not None
        assert ready >= ready_of[row["shipment_id"]]
        if datetime.fromisoformat(row["event_ts"]) <= CUTOFF_END:
            lag = arrival_lag(row["shipment_id"])
            assert ready == max(utc_day(row["event_ts"]) + lag, ready_of[row["shipment_id"]])
    # Evento sintético del día de recogida de un envío tardío: espera a su envío.
    shipment = late_shipment(index, read_csv(dataset, TMS_SHIPMENTS))
    shipment_ready = ready_of[shipment["shipment_id"]]
    event = {**events[0], "shipment_id": shipment["shipment_id"], "event_ts": shipment["pickup_ts"]}
    assert index.available_on(TMS_SHIPMENT_EVENTS, event) == shipment_ready
    assert date.fromisoformat(shipment["pickup_date"]) < shipment_ready
    before = shipment_ready - timedelta(days=1)
    assert index.select(TMS_SHIPMENT_EVENTS, [event], before) == []
    assert index.select(TMS_SHIPMENT_EVENTS, [event], shipment_ready) == [event]
    assert index.select(TMS_SHIPMENTS, [shipment], before) == []


def test_event_with_impossible_date_takes_its_shipment_date(
    dataset: Path, index: AvailabilityIndex
) -> None:
    events = read_csv(dataset, TMS_SHIPMENT_EVENTS)
    impossible = [row for row in events if datetime.fromisoformat(row["event_ts"]) > CUTOFF_END]
    assert impossible
    ready_of = index.shipment_dates()
    for row in impossible:
        assert index.available_on(TMS_SHIPMENT_EVENTS, row) == ready_of[row["shipment_id"]]
    # La misma regla sobre una fecha desplazada como la del defecto.
    clean = next(row for row in events if datetime.fromisoformat(row["event_ts"]) <= CUTOFF_END)
    shifted = datetime.fromisoformat(clean["event_ts"]) + timedelta(days=IMPOSSIBLE_DATE_SHIFT_DAYS)
    defective = {**clean, "event_ts": shifted.isoformat()}
    assert index.available_on(TMS_SHIPMENT_EVENTS, defective) == ready_of[clean["shipment_id"]]
    assert index.select(TMS_SHIPMENT_EVENTS, [defective], ready_of[clean["shipment_id"]]) == [
        defective
    ]


def test_stops_and_shipment_legs_take_the_shipment_date(
    dataset: Path, index: AvailabilityIndex
) -> None:
    ready_of = index.shipment_dates()
    for table in (TMS_SHIPMENT_STOPS, TMS_SHIPMENT_LEGS):
        rows = read_csv(dataset, table)
        assert rows
        for row in rows:
            assert index.available_on(table, row) == ready_of[row["shipment_id"]]


def test_fuel_by_txn_ts_date_and_never_before_its_trip(
    dataset: Path, index: AvailabilityIndex
) -> None:
    trip_ready = index.trip_dates()
    fuel = read_csv(dataset, TMS_FUEL_TRANSACTIONS)
    assert fuel
    for row in fuel:
        ready = index.available_on(TMS_FUEL_TRANSACTIONS, row)
        assert ready == utc_day(row["txn_ts"])
        assert ready >= trip_ready[row["trip_id"]]
    for row in read_csv(dataset, TMS_TRIP_LEGS):
        assert index.available_on(TMS_TRIP_LEGS, row) == trip_ready[row["trip_id"]]
    for row in read_csv(dataset, TMS_TRIPS):
        assert index.available_on(TMS_TRIPS, row) == utc_day(row["start_ts"])


def test_children_never_before_their_parents(dataset: Path, index: AvailabilityIndex) -> None:
    ready_of = index.shipment_dates()
    trip_ready = index.trip_dates()
    for table in (TMS_CARRIER_TENDERS, TMS_CARRIER_INCIDENTS):
        for row in read_csv(dataset, table):
            assert index.available_on(table, row) >= ready_of[row["shipment_id"]]
    for row in read_csv(dataset, TMS_SHIPMENT_LEGS):
        assert index.available_on(TMS_SHIPMENT_LEGS, row) >= trip_ready[row["trip_id"]]
    for table in TMS_TABLES:
        if table.availability == tms_publisher.ALWAYS:
            assert index.available_on(table, read_csv(dataset, table)[0]) is None


def test_child_without_shipment_fails(index: AvailabilityIndex, dataset: Path) -> None:
    row = {**read_csv(dataset, TMS_SHIPMENT_STOPS)[0], "shipment_id": "SHP-999999"}
    with pytest.raises(ValueError, match="sin fila en shipments.csv"):
        index.available_on(TMS_SHIPMENT_STOPS, row)


# --- Publicación por bloques con una conexión simulada ---------------------------


class FakeCursor:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log
        self.rowcount = -1
        self.staged = 0

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: Any, params: Any = None) -> None:
        text = statement if isinstance(statement, str) else statement.as_string(None)
        self.log.append(("execute", text))
        if text.startswith("insert"):
            self.rowcount = self.staged
        if text.startswith("truncate"):
            self.staged = 0

    @contextmanager
    def copy(self, statement: Any) -> Iterator[FakeCursor]:
        self.log.append(("copy", statement.as_string(None)))
        yield self

    def write(self, data: bytes) -> None:
        rows = list(csv.DictReader(data.decode("utf-8").splitlines()))
        self.staged += len(rows)
        self.log.append(("data", rows))


class FakeConnection:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []
        self.transactions = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.transactions += 1
        self.log.append(("begin", None))
        yield
        self.log.append(("commit", None))

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.log)


def statements(log: list[tuple[str, Any]]) -> list[str]:
    return [value for kind, value in log if kind == "execute"]


def test_publish_blocks_locks_first_and_clears_staging_per_block() -> None:
    conn = FakeConnection()
    rows = [
        {name: "x" for name in TMS_SHIPMENT_EVENTS.column_names} | {"event_id": f"EVT-{i}"}
        for i in range(5)
    ]
    blocks = [rows[:2], [], rows[2:]]

    result = publish_blocks(conn, TMS_SHIPMENT_EVENTS, blocks, "shipment_events.csv", "it_x")

    executed = statements(conn.log)
    assert executed[0] == "select pg_advisory_xact_lock(hashtext(%s))"
    assert executed[1].startswith('create table if not exists "it_x"."shipment_events"')
    assert executed[2].startswith("create index if not exists")
    assert executed[3].startswith('create temporary table "_publish_shipment_events"')
    body = executed[4:]
    assert [text.split(" ", 1)[0] for text in body] == ["insert", "truncate"] * 2
    assert body[1] == 'truncate "_publish_shipment_events"'
    assert all("delete" not in text and "update " not in text for text in executed)
    assert conn.transactions == 1
    assert [kind for kind, _ in conn.log].count("copy") == 2
    assert result == tms_publisher.PublishResult(
        "it_x.shipment_events", "shipment_events.csv", 5, 5
    )


def published(conn: FakeConnection) -> dict[str, list[dict[str, str]]]:
    current = ""
    out: dict[str, list[dict[str, str]]] = {}
    for kind, value in conn.log:
        if kind == "execute" and value.startswith("create table"):
            current = value.split('"')[3]
            out.setdefault(current, [])
        if kind == "data":
            out[current].extend(value)
    return out


def test_publish_directory_without_until_sends_every_row_in_blocks(dataset: Path) -> None:
    conn = FakeConnection()

    results = publish_directory(conn, dataset, schema="it_x", block_rows=97)

    sent = published(conn)
    assert [result.table for result in results] == [f"it_x.{t.name}" for t in TMS_TABLES]
    assert conn.transactions == len(TMS_TABLES)
    for table, result in zip(TMS_TABLES, results, strict=True):
        assert sent[table.name] == read_csv(dataset, table)
        assert result.selected == len(sent[table.name])
    copies = [len(value) for kind, value in conn.log if kind == "data"]
    assert max(copies) <= 97 + 2


def test_publish_directory_with_until_keeps_children_behind_parents(dataset: Path) -> None:
    until = date(2026, 11, 7)
    conn = FakeConnection()

    publish_directory(conn, dataset, until=until, schema="it_x", block_rows=50)

    sent = published(conn)
    shipments = {row["shipment_id"] for row in sent["shipments"]}
    trips = {row["trip_id"] for row in sent["trips"]}
    legs = {(row["trip_id"], row["leg_sequence"]) for row in sent["trip_legs"]}
    assert 0 < len(sent["shipments"]) < len(read_csv(dataset, TMS_SHIPMENTS))
    for name in (
        "shipment_legs",
        "shipment_stops",
        "shipment_events",
        "carrier_tenders",
        "carrier_incidents",
    ):
        assert sent[name]
        assert {row["shipment_id"] for row in sent[name]} <= shipments
    assert {(row["trip_id"], row["leg_sequence"]) for row in sent["shipment_legs"]} <= legs
    assert {row["trip_id"] for row in sent["trip_legs"]} <= trips
    assert {row["trip_id"] for row in sent["fuel_transactions"]} <= trips
    assert all(utc_day(row["txn_ts"]) <= until for row in sent["fuel_transactions"])
    for name in ("carriers", "routes", "service_types", "vehicles", "drivers"):
        assert len(sent[name]) == len(read_csv(dataset, by_name(name)))
    ids = [row["event_id"] for row in sent["shipment_events"]]
    copied = {event_id for event_id in ids if ids.count(event_id) > 1}
    all_ids = [row["event_id"] for row in read_csv(dataset, TMS_SHIPMENT_EVENTS)]
    assert all(ids.count(event_id) == all_ids.count(event_id) for event_id in copied)


def test_publish_directory_reports_missing_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="shipment_events.csv"):
        publish_directory(FakeConnection(), tmp_path, tables=(TMS_SHIPMENT_EVENTS,))


# --- Perfil benchmark y línea de comandos ----------------------------------------


def write_manifest(directory: Path, scale: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "defect_manifest.json").write_text(
        json.dumps({"scale": scale}) + "\n", encoding="utf-8"
    )
    return directory


def test_benchmark_folder_is_rejected_before_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("no debe conectar")

    monkeypatch.setattr(tms_publisher.psycopg, "connect", refuse)
    monkeypatch.setattr(tms_publisher, "settings", None)
    folder = write_manifest(tmp_path / "guard", "benchmark")

    assert tms_publisher.main(["--input", str(folder)]) == 2
    assert "benchmark" in capsys.readouterr().err
    with pytest.raises(UnpublishableScaleError, match="benchmark"):
        publish_directory(FakeConnection(), folder)


def test_other_scales_and_missing_manifest_are_publishable(tmp_path: Path) -> None:
    check_publishable(write_manifest(tmp_path / "demo", "demo"))
    check_publishable(write_manifest(tmp_path / "local", "local"))
    check_publishable(tmp_path / "empty")


def test_parser_options() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    for option in ("--input", "--until", "--schema", "--dsn"):
        assert option in help_text
    flat = " ".join(help_text.split())
    for table in TMS_TABLES:
        assert table.file_name in flat
    args = parser.parse_args(["--input", "data/demo"])
    assert args.schema == "tms"
    assert args.until is None
    assert parser.parse_args(["--input", "x", "--schema", "it_x_tms"]).schema == "it_x_tms"

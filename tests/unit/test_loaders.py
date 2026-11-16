"""Pruebas unitarias del cargador batch: hash SHA-256 de archivo, decisión de
omitir un archivo ya cargado, contratos de las tablas TMS, ERP y CRM, búsqueda
de archivos por tabla, hash del lote leído por bloques y opciones de la línea
de comandos.

No usan base de datos: los archivos se crean en ``tmp_path``, la decisión de
omitir se toma solo con la función pura ``should_skip`` y el hash del lote se
calcula sobre un cursor simulado que entrega filas de texto.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from psycopg import sql

from nexora.generators.crm import CONTACTS_COLUMNS, CONTRACTS_COLUMNS, LEADS_COLUMNS
from nexora.generators.dimensions import CUSTOMERS_COLUMNS
from nexora.generators.erp import (
    CARRIER_SETTLEMENTS_COLUMNS,
    INVOICE_LINES_COLUMNS,
    INVOICES_COLUMNS,
    PAYMENTS_COLUMNS,
    SHIPMENT_COSTS_COLUMNS,
    TARIFFS_COLUMNS,
)
from nexora.generators.fleet import DRIVERS_COLUMNS, VEHICLES_COLUMNS
from nexora.generators.operations import (
    CARRIER_INCIDENTS_COLUMNS,
    CARRIER_TENDERS_COLUMNS,
    FUEL_TRANSACTIONS_COLUMNS,
    SHIPMENT_EVENTS_COLUMNS,
    SHIPMENT_LEGS_COLUMNS,
    SHIPMENT_STOPS_COLUMNS,
    TRIP_LEGS_COLUMNS,
    TRIPS_COLUMNS,
)
from nexora.ingestion.batch import loaders
from nexora.ingestion.batch.loaders import (
    CARRIER_INCIDENTS,
    CARRIER_SETTLEMENTS,
    CARRIER_TENDERS,
    CONTACTS,
    CONTRACTS,
    CRM_SOURCE_SYSTEM,
    CUSTOMERS,
    DRIVERS,
    ERP_SOURCE_SYSTEM,
    FILE_TABLES,
    FUEL_TRANSACTIONS,
    HASH_FETCH_ROWS,
    INVOICE_LINES,
    INVOICES,
    LATE_DIRECTORY,
    LEADS,
    METADATA_COLUMNS,
    PAYMENTS,
    RAW_TABLES,
    SHIPMENT_COSTS,
    SHIPMENT_EVENTS,
    SHIPMENT_LEGS,
    SHIPMENT_STOPS,
    SHIPMENTS,
    TARIFFS,
    TMS_TABLES,
    TRIP_LEGS,
    TRIPS,
    VEHICLES,
    RawTable,
    build_parser,
    should_skip,
    stream_batch_hash,
)
from nexora.ingestion.batch.readers import HASH_CHUNK_BYTES, discover_files, file_sha256

SAMPLE_BYTES = b"shipment_id,customer_id\nSHP-000001,CUS-0001\n"

# Valores de referencia publicados de SHA-256 (FIPS 180-2).
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def write_bytes(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_hash_matches_hashlib_on_same_bytes(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "shipments.csv", SAMPLE_BYTES)

    assert file_sha256(path) == hashlib.sha256(SAMPLE_BYTES).hexdigest()


@pytest.mark.parametrize(
    ("content", "expected"),
    [(b"", EMPTY_SHA256), (b"abc", ABC_SHA256)],
    ids=["empty", "abc"],
)
def test_hash_matches_known_value(tmp_path: Path, content: bytes, expected: str) -> None:
    path = write_bytes(tmp_path / "file.csv", content)

    assert file_sha256(path) == expected


def test_hash_accepts_str_path(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "file.csv", b"abc")

    assert file_sha256(str(path)) == ABC_SHA256


def test_hash_spanning_several_chunks_matches_hashlib(tmp_path: Path) -> None:
    content = bytes(range(256)) * ((2 * HASH_CHUNK_BYTES) // 256 + 7)
    path = write_bytes(tmp_path / "large.csv", content)

    assert len(content) > 2 * HASH_CHUNK_BYTES
    assert file_sha256(path) == hashlib.sha256(content).hexdigest()


def test_hash_is_lowercase_hex_of_64_chars(tmp_path: Path) -> None:
    digest = file_sha256(write_bytes(tmp_path / "file.csv", SAMPLE_BYTES))

    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)


def test_same_content_different_name_gives_same_hash(tmp_path: Path) -> None:
    first = write_bytes(tmp_path / "shipments.csv", SAMPLE_BYTES)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    second = write_bytes(other_dir / "shipments_copy.csv", SAMPLE_BYTES)

    assert file_sha256(first) == file_sha256(second)


def test_one_different_byte_changes_hash(tmp_path: Path) -> None:
    changed = bytearray(SAMPLE_BYTES)
    changed[-2] ^= 0x01
    original = write_bytes(tmp_path / "original.csv", SAMPLE_BYTES)
    modified = write_bytes(tmp_path / "modified.csv", bytes(changed))

    assert len(changed) == len(SAMPLE_BYTES)
    assert file_sha256(original) != file_sha256(modified)


def test_should_skip_with_empty_collection_is_false() -> None:
    assert should_skip(ABC_SHA256, set()) is False
    assert should_skip(ABC_SHA256, ()) is False


def test_should_skip_when_hash_is_loaded_is_true() -> None:
    assert should_skip(ABC_SHA256, {ABC_SHA256}) is True
    assert should_skip(ABC_SHA256, [EMPTY_SHA256, ABC_SHA256]) is True


def test_should_skip_with_only_other_hashes_is_false() -> None:
    assert should_skip(ABC_SHA256, {EMPTY_SHA256}) is False
    assert should_skip(ABC_SHA256, [EMPTY_SHA256, "0" * 64]) is False


def test_directory_with_loaded_and_new_file_skips_only_loaded(tmp_path: Path) -> None:
    loaded_file = write_bytes(tmp_path / "customers.csv", b"customer_id\nCUS-0001\n")
    new_file = write_bytes(tmp_path / "carriers.csv", b"carrier_id\nCAR-001\n")
    loaded = {file_sha256(loaded_file)}

    decisions = {
        path.name: should_skip(file_sha256(path), loaded) for path in sorted(tmp_path.glob("*.csv"))
    }

    assert decisions == {"carriers.csv": False, "customers.csv": True}
    assert file_sha256(new_file) not in loaded


def test_renamed_copy_of_loaded_file_is_skipped(tmp_path: Path) -> None:
    loaded_file = write_bytes(tmp_path / "shipments.csv", SAMPLE_BYTES)
    renamed_copy = write_bytes(tmp_path / "shipments_resent.csv", SAMPLE_BYTES)
    loaded = {file_sha256(loaded_file)}

    assert should_skip(file_sha256(renamed_copy), loaded) is True


# --- Contratos de las tablas TMS ---------------------------------------------

GENERATOR_COLUMNS: dict[str, tuple[RawTable, tuple[str, ...]]] = {
    "vehicles": (VEHICLES, VEHICLES_COLUMNS),
    "drivers": (DRIVERS, DRIVERS_COLUMNS),
    "trips": (TRIPS, TRIPS_COLUMNS),
    "trip_legs": (TRIP_LEGS, TRIP_LEGS_COLUMNS),
    "shipment_legs": (SHIPMENT_LEGS, SHIPMENT_LEGS_COLUMNS),
    "shipment_stops": (SHIPMENT_STOPS, SHIPMENT_STOPS_COLUMNS),
    "shipment_events": (SHIPMENT_EVENTS, SHIPMENT_EVENTS_COLUMNS),
    "fuel_transactions": (FUEL_TRANSACTIONS, FUEL_TRANSACTIONS_COLUMNS),
    "carrier_tenders": (CARRIER_TENDERS, CARRIER_TENDERS_COLUMNS),
    "carrier_incidents": (CARRIER_INCIDENTS, CARRIER_INCIDENTS_COLUMNS),
}

NULLABLE_COLUMNS: dict[str, set[str]] = {
    "vehicles": set(),
    "drivers": set(),
    "trips": {"end_ts"},
    "trip_legs": {"arrival_ts"},
    "shipment_legs": set(),
    "shipment_stops": {"actual_ts"},
    "shipment_events": {"event_type", "location_city"},
    "fuel_transactions": {"gallons", "total_amount"},
    "carrier_tenders": {"responded_ts", "response_status", "accepted_rate_amount"},
    "carrier_incidents": {"claim_amount", "resolved_ts"},
}


@pytest.mark.parametrize("name", list(GENERATOR_COLUMNS))
def test_contract_equals_generator_columns(name: str) -> None:
    table, generator_columns = GENERATOR_COLUMNS[name]

    assert table.name == name
    assert table.source_system == "tms"
    assert table.column_names == generator_columns


@pytest.mark.parametrize("name", list(NULLABLE_COLUMNS))
def test_contract_nullable_columns(name: str) -> None:
    table, _ = GENERATOR_COLUMNS[name]

    assert {column.name for column in table.columns if column.nullable} == NULLABLE_COLUMNS[name]


def test_tms_tables_order() -> None:
    assert [table.name for table in TMS_TABLES] == [
        "carriers",
        "routes",
        "service_types",
        "shipments",
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
    ]


def test_raw_tables_include_every_tms_and_file_table() -> None:
    names = [table.name for table in RAW_TABLES]

    assert len(names) == len(set(names)) == 24
    assert set(names) == {table.name for table in TMS_TABLES} | {t.name for t in FILE_TABLES}
    assert not {table.name for table in TMS_TABLES} & {t.name for t in FILE_TABLES}


def test_new_raw_table_ddl_has_metadata_and_no_unique() -> None:
    statement = SHIPMENT_EVENTS.create_statement("it_x_raw").as_string(None)

    assert statement.startswith('create table if not exists "it_x_raw"."shipment_events" (')
    assert '"event_type" text,' in statement
    assert '"event_ts" timestamptz not null' in statement
    for column in METADATA_COLUMNS:
        assert f'"{column.name}" {column.sql_type} not null' in statement
    assert "unique" not in statement
    assert "primary key" not in statement


# --- Contratos de las tablas ERP y CRM ---------------------------------------

FILE_GENERATOR_COLUMNS: dict[str, tuple[RawTable, tuple[str, ...], str]] = {
    "customers": (CUSTOMERS, CUSTOMERS_COLUMNS, "erp"),
    "tariffs": (TARIFFS, TARIFFS_COLUMNS, "erp"),
    "contacts": (CONTACTS, CONTACTS_COLUMNS, "crm"),
    "contracts": (CONTRACTS, CONTRACTS_COLUMNS, "crm"),
    "leads": (LEADS, LEADS_COLUMNS, "crm"),
    "invoices": (INVOICES, INVOICES_COLUMNS, "erp"),
    "invoice_lines": (INVOICE_LINES, INVOICE_LINES_COLUMNS, "erp"),
    "payments": (PAYMENTS, PAYMENTS_COLUMNS, "erp"),
    "carrier_settlements": (CARRIER_SETTLEMENTS, CARRIER_SETTLEMENTS_COLUMNS, "erp"),
    "shipment_costs": (SHIPMENT_COSTS, SHIPMENT_COSTS_COLUMNS, "erp"),
}

NEW_FILE_TABLES = [name for name in FILE_GENERATOR_COLUMNS if name != "customers"]

FILE_NULLABLE_COLUMNS: dict[str, set[str]] = {
    "tariffs": {"valid_to"},
    "contacts": set(),
    "contracts": {"end_date"},
    "leads": {"expected_close_date"},
    "invoices": set(),
    "invoice_lines": set(),
    "payments": set(),
    "carrier_settlements": set(),
    "shipment_costs": set(),
}

# Tipo SQL de las columnas no textuales de cada tabla nueva; el resto es ``text``.
FILE_TYPED_COLUMNS: dict[str, dict[str, str]] = {
    "tariffs": {
        "valid_from": "date",
        "valid_to": "date",
        "base_amount": "numeric(14,2)",
        "per_km_amount": "numeric(14,2)",
        "fuel_surcharge_pct": "numeric(5,2)",
    },
    "contacts": {},
    "contracts": {
        "start_date": "date",
        "end_date": "date",
        "payment_term_days": "integer",
        "otif_target_pct": "numeric(5,2)",
    },
    "leads": {
        "created_date": "date",
        "estimated_value_amount": "numeric(14,2)",
        "expected_close_date": "date",
    },
    "invoices": {"invoice_date": "date", "due_date": "date", "total_amount": "numeric(14,2)"},
    "invoice_lines": {
        "line_number": "integer",
        "tariff_amount": "numeric(14,2)",
        "billed_revenue_amount": "numeric(14,2)",
        "tax_amount": "numeric(14,2)",
    },
    "payments": {"paid_date": "date", "payment_amount": "numeric(14,2)"},
    "carrier_settlements": {
        "settled_date": "date",
        "payable_amount": "numeric(14,2)",
        "deduction_amount": "numeric(14,2)",
        "fuel_surcharge_amount": "numeric(14,2)",
    },
    "shipment_costs": {"cost_date": "date", "cost_amount": "numeric(14,2)"},
}


def test_source_system_constants() -> None:
    assert (ERP_SOURCE_SYSTEM, CRM_SOURCE_SYSTEM) == ("erp", "crm")


def test_late_directory_constant() -> None:
    assert LATE_DIRECTORY == "late"


def test_file_tables_order_and_source_system() -> None:
    assert [(table.name, table.source_system) for table in FILE_TABLES] == [
        ("customers", "erp"),
        ("tariffs", "erp"),
        ("contacts", "crm"),
        ("contracts", "crm"),
        ("leads", "crm"),
        ("invoices", "erp"),
        ("invoice_lines", "erp"),
        ("payments", "erp"),
        ("carrier_settlements", "erp"),
        ("shipment_costs", "erp"),
    ]


@pytest.mark.parametrize("name", list(FILE_GENERATOR_COLUMNS))
def test_file_contract_equals_generator_columns(name: str) -> None:
    table, generator_columns, source_system = FILE_GENERATOR_COLUMNS[name]

    assert table.name == name
    assert table.source_system == source_system
    assert table.column_names == generator_columns


@pytest.mark.parametrize("name", NEW_FILE_TABLES)
def test_file_contract_nullable_columns(name: str) -> None:
    table, _, _ = FILE_GENERATOR_COLUMNS[name]

    assert {column.name for column in table.columns if column.nullable} == FILE_NULLABLE_COLUMNS[
        name
    ]


@pytest.mark.parametrize("name", NEW_FILE_TABLES)
def test_file_contract_types(name: str) -> None:
    table, _, _ = FILE_GENERATOR_COLUMNS[name]
    typed = FILE_TYPED_COLUMNS[name]

    assert {column.name: column.sql_type for column in table.columns} == {
        column: typed.get(column, "text") for column in table.column_names
    }
    assert all(column.check is None for column in table.columns)


@pytest.mark.parametrize("name", NEW_FILE_TABLES)
def test_file_table_ddl_has_metadata_and_no_unique(name: str) -> None:
    table, _, _ = FILE_GENERATOR_COLUMNS[name]
    statement = table.create_statement("it_x_raw").as_string(None)

    assert statement.startswith(f'create table if not exists "it_x_raw"."{name}" (')
    for column in table.columns:
        null = "," if column.nullable else " not null,"
        assert f'"{column.name}" {column.sql_type}{null}' in statement
    for column in METADATA_COLUMNS:
        assert f'"{column.name}" {column.sql_type} not null' in statement
    assert "unique" not in statement
    assert "primary key" not in statement


# Los 24 CSV de la carpeta principal de un conjunto generado.
DATASET_FILE_NAMES = [f"{table.name}.csv" for table in RAW_TABLES]


def test_discover_files_returns_only_its_table(tmp_path: Path) -> None:
    for name in DATASET_FILE_NAMES:
        write_bytes(tmp_path / name, b"x\n")

    assert len(DATASET_FILE_NAMES) == 24
    for table in FILE_TABLES:
        assert [path.name for path in discover_files(tmp_path, table.name)] == [f"{table.name}.csv"]


def test_discover_files_in_late_directory(tmp_path: Path) -> None:
    for name in DATASET_FILE_NAMES:
        write_bytes(tmp_path / name, b"x\n")
    late = tmp_path / LATE_DIRECTORY
    late.mkdir()
    for name in NEW_FILE_TABLES:
        write_bytes(late / f"{name}_late.csv", b"x\n")

    for table in FILE_TABLES:
        main_files = [path.name for path in discover_files(tmp_path, table.name)]
        late_files = [path.name for path in discover_files(late, table.name)]
        assert main_files == [f"{table.name}.csv"]
        assert late_files == ([] if table.name == "customers" else [f"{table.name}_late.csv"])


def test_files_help_names_erp_and_crm(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    text = capsys.readouterr().out

    assert "ERP y CRM" in text
    assert "customers*.csv" not in text
    assert f"{LATE_DIRECTORY}/" in text


def test_main_without_files_names_erp_and_crm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    class FakeContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(loaders.psycopg, "connect", lambda dsn: FakeContext())
    monkeypatch.setattr(loaders, "run_files", lambda *args, **kwargs: [])

    code = loaders.main(["--source", "files", "--input", str(tmp_path), "--dsn", "host=invalid"])

    assert code == 0
    assert f"sin archivos ERP ni CRM en {tmp_path}" in capsys.readouterr().out


def test_module_has_no_string_agg() -> None:
    assert "string_agg" not in inspect.getsource(loaders)


# --- Hash del lote por bloques -----------------------------------------------


class FakeNamedCursor:
    """Cursor de servidor simulado: entrega filas ``(texto,)`` por bloques."""

    def __init__(self, rows: Sequence[str]) -> None:
        self.rows = [(text,) for text in rows]
        self.position = 0
        self.statement: str | None = None
        self.params: tuple[Any, ...] | None = None
        self.fetch_sizes: list[int] = []

    def __enter__(self) -> FakeNamedCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: sql.Composable, params: tuple[Any, ...]) -> None:
        self.statement = statement.as_string(None)
        self.params = params

    def fetchmany(self, size: int) -> list[tuple[str]]:
        self.fetch_sizes.append(size)
        block = self.rows[self.position : self.position + size]
        self.position += len(block)
        return block


class FakeConnection:
    def __init__(self, rows: Sequence[str]) -> None:
        self.cursor_obj = FakeNamedCursor(rows)
        self.cursor_names: list[str | None] = []

    def cursor(self, name: str | None = None) -> FakeNamedCursor:
        self.cursor_names.append(name)
        return self.cursor_obj


BATCH_ROWS = (
    '(SHP-000001,CUS-0001,"2026-11-01 10:00:00+00")',
    "(SHP-000002,,áéí)",
    "(SHP-000003,CUS-0003,)",
    "(SHP-000004,CUS-0004,x)",
    "(SHP-000005,CUS-0005,y)",
)
WINDOW = sql.SQL("updated_at > %s and updated_at <= %s")


def reference_hash(rows: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def run_hash(rows: Sequence[str], fetch_rows: int = HASH_FETCH_ROWS) -> tuple[FakeConnection, Any]:
    conn = FakeConnection(rows)
    result = stream_batch_hash(
        conn,  # type: ignore[arg-type]
        SHIPMENTS,
        sql.Identifier("it_x_tms", "shipments"),
        WINDOW,
        ("lower", "upper"),
        fetch_rows,
    )
    return conn, result


@pytest.mark.parametrize("fetch_rows", [1, 2, 5, HASH_FETCH_ROWS], ids=lambda n: f"fetch_{n}")
def test_stream_hash_equals_joined_rows(fetch_rows: int) -> None:
    _, (count, digest) = run_hash(BATCH_ROWS, fetch_rows)

    assert count == len(BATCH_ROWS)
    assert digest == reference_hash(BATCH_ROWS)


def test_stream_hash_reads_in_blocks_of_fetch_rows() -> None:
    conn, _ = run_hash(BATCH_ROWS, 2)

    assert conn.cursor_obj.fetch_sizes == [2, 2, 2, 2]
    assert conn.cursor_names == ["batch_hash_shipments"]


def test_stream_hash_single_row_has_no_separator() -> None:
    _, (count, digest) = run_hash(BATCH_ROWS[:1], 1)

    assert count == 1
    assert digest == hashlib.sha256(BATCH_ROWS[0].encode("utf-8")).hexdigest()


def test_stream_hash_empty_window_is_hash_of_empty_string() -> None:
    _, (count, digest) = run_hash((), 3)

    assert count == 0
    assert digest == EMPTY_SHA256


def test_stream_hash_statement_orders_by_tms_row_id_with_params() -> None:
    conn, _ = run_hash(BATCH_ROWS, 3)
    columns = ", ".join(f'"{name}"' for name in SHIPMENTS.column_names)

    assert conn.cursor_obj.statement == (
        f'select row({columns})::text from "it_x_tms"."shipments" '
        "where updated_at > %s and updated_at <= %s order by tms_row_id"
    )
    assert conn.cursor_obj.params == ("lower", "upper")


def test_stream_hash_rejects_fetch_rows_below_one() -> None:
    with pytest.raises(ValueError, match="fetch_rows"):
        run_hash(BATCH_ROWS, 0)


def test_extract_and_run_tms_accept_fetch_rows() -> None:
    for function in (loaders.extract_table, loaders.run_tms):
        parameter = inspect.signature(function).parameters["fetch_rows"]
        assert parameter.default == HASH_FETCH_ROWS


# --- Opciones de esquema -----------------------------------------------------


def test_schema_options_default_to_tms_raw_meta() -> None:
    args = build_parser().parse_args(["--source", "tms"])

    assert (args.tms_schema, args.raw_schema, args.meta_schema) == ("tms", "raw", "meta")


def test_main_passes_schema_options_to_run_tms(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    def fake_run_tms(conn: object, run_id: str, **kwargs: Any) -> list[object]:
        calls.append({"run_id": run_id, **kwargs})
        return []

    monkeypatch.setattr(loaders.psycopg, "connect", lambda dsn: FakeContext())
    monkeypatch.setattr(loaders, "run_tms", fake_run_tms)

    code = loaders.main(
        [
            "--source",
            "tms",
            "--dsn",
            "host=invalid",
            "--run-id",
            "r1",
            "--tms-schema",
            "it_a_tms",
            "--raw-schema",
            "it_a_raw",
            "--meta-schema",
            "it_a_meta",
        ]
    )

    assert code == 0
    assert calls == [
        {
            "run_id": "r1",
            "tms_schema": "it_a_tms",
            "raw_schema": "it_a_raw",
            "meta_schema": "it_a_meta",
        }
    ]

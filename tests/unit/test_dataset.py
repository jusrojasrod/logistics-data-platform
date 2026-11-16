"""Pruebas de la escritura en flujo del conjunto completo: equivalencia de
``shipments.csv`` con la inyección sobre la lista, determinismo, manifiesto
reproducible desde los CSV, referencias de los hijos a envíos base, archivos
operativos independientes del ERP y del CRM, y reparto de las filas ERP y CRM
entre el archivo principal y el tardío."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nexora.generators import dataset
from nexora.generators.config import HISTORY_MONTHS, UNCLOSED_MIN_AGE_DAYS
from nexora.generators.dataset import (
    LATE_FILE_KEYS,
    TMS_TABLE_ORDER,
    DatasetSummary,
    write_dataset,
)
from nexora.generators.defects import MANIFEST_FILE_NAME, RATE_DECIMALS, inject_defects
from nexora.generators.dimensions import DimensionTable
from nexora.generators.erp import ShipmentFinance
from nexora.generators.late_files import is_late, late_file_name, late_key
from nexora.generators.shipments import CUTOFF_END, generate_shipments

SEED = 42
SHIPMENTS = 2000

CHILD_TABLES = (
    "shipment_stops",
    "shipment_events",
    "carrier_tenders",
    "carrier_incidents",
    "shipment_legs",
)

# Tablas ERP y CRM en el orden del manifiesto, sin ``customers``.
LATE_TABLES = (
    "tariffs",
    "contacts",
    "contracts",
    "leads",
    "invoices",
    "invoice_lines",
    "payments",
    "carrier_settlements",
    "shipment_costs",
)

# Archivos que ya existían antes del ERP y del CRM: las 14 tablas TMS y los clientes.
OPERATIONAL_FILES = tuple(f"{name}.csv" for name in (*TMS_TABLE_ORDER, "customers"))

UNCLOSED_REPORTED_BEFORE = CUTOFF_END - timedelta(days=UNCLOSED_MIN_AGE_DAYS)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def read_rows(directory: Path, table: str) -> list[dict[str, str]]:
    return read_csv(directory / f"{table}.csv")[1]


def file_digests(directory: Path) -> dict[str, str]:
    """Huella de cada archivo de la carpeta, subcarpeta ``late/`` incluida."""
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory) -> DatasetSummary:
    out = tmp_path_factory.mktemp("dataset")
    return write_dataset(out, "demo", SHIPMENTS, SEED, HISTORY_MONTHS)


@pytest.fixture(scope="module")
def tables(written: DatasetSummary) -> dict[str, list[dict[str, str]]]:
    return {name: read_rows(written.directory, name) for name in TMS_TABLE_ORDER}


@pytest.fixture(scope="module")
def base_shipments() -> tuple[dict[str, str], ...]:
    return generate_shipments(SHIPMENTS, SEED, HISTORY_MONTHS)


def test_streamed_shipments_equal_list_injection(
    tables: dict[str, list[dict[str, str]]], base_shipments: tuple[dict[str, str], ...]
) -> None:
    expected = inject_defects(base_shipments, SEED).rows
    assert tables["shipments"] == list(expected)


def test_two_runs_write_identical_bytes(written: DatasetSummary, tmp_path: Path) -> None:
    second = write_dataset(tmp_path / "second", "demo", SHIPMENTS, SEED, HISTORY_MONTHS)
    assert file_digests(second.directory) == file_digests(written.directory)
    assert second == DatasetSummary(
        directory=second.directory,
        shipments=written.shipments,
        files=written.files,
        tables=written.tables,
        defects=written.defects,
        late_files=written.late_files,
        manifest=second.manifest,
    )


def test_summary_counts_match_files(written: DatasetSummary) -> None:
    for name, rows in written.files.items():
        with (written.directory / name).open(encoding="utf-8", newline="") as handle:
            assert sum(1 for _ in csv.DictReader(handle)) == rows, name
    assert set(written.files) == {
        *OPERATIONAL_FILES,
        *(f"{name}.csv" for name in LATE_TABLES),
        *(f"late/{name}_late.csv" for name in LATE_TABLES),
    }


def test_operational_files_do_not_depend_on_erp_and_crm(
    written: DatasetSummary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Las 15 tablas de antes salen con los mismos bytes sin generar ERP ni CRM."""

    def empty(table: DimensionTable) -> DimensionTable:
        return DimensionTable(table.name, table.source_system, table.columns, ())

    real_contacts, real_contracts = dataset.build_contacts, dataset.build_contracts
    real_leads, real_tariffs = dataset.build_leads, dataset.build_tariffs
    monkeypatch.setattr(dataset, "build_contacts", lambda c: empty(real_contacts(c)))
    monkeypatch.setattr(dataset, "build_contracts", lambda c: empty(real_contracts(c)))
    monkeypatch.setattr(dataset, "build_leads", lambda c, seed: empty(real_leads(c, seed)))
    monkeypatch.setattr(
        dataset, "build_tariffs", lambda combos, seed, months: empty(real_tariffs(combos, seed))
    )
    monkeypatch.setattr(dataset.TariffCombos, "add", lambda self, row: None)
    monkeypatch.setattr(
        dataset, "shipment_finance", lambda row, context, rng, ids: ShipmentFinance()
    )

    bare = write_dataset(tmp_path / "bare", "demo", SHIPMENTS, SEED, HISTORY_MONTHS)
    assert all(entry.rows == 0 for entry in bare.late_files)
    with_erp = file_digests(written.directory)
    without_erp = file_digests(bare.directory)
    assert {name: with_erp[name] for name in OPERATIONAL_FILES} == {
        name: without_erp[name] for name in OPERATIONAL_FILES
    }
    assert bare.tables == written.tables
    assert bare.defects == written.defects


def test_late_files_manifest_matches_csv_counts(written: DatasetSummary) -> None:
    manifest = json.loads((written.directory / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert list(manifest)[-1] == "late_files"
    assert tuple(LATE_FILE_KEYS) == LATE_TABLES
    expected = []
    for name in LATE_TABLES:
        main = read_rows(written.directory, name)
        late = read_csv(written.directory / late_file_name(name))[1]
        expected.append(
            {
                "table": name,
                "file": f"late/{name}_late.csv",
                "key": list(LATE_FILE_KEYS[name]),
                "rows": len(main) + len(late),
                "late_rows": len(late),
            }
        )
        assert main, name
    assert manifest["late_files"] == expected
    assert [entry.to_dict() for entry in written.late_files] == expected


def test_each_erp_and_crm_row_lives_in_one_folder(written: DatasetSummary) -> None:
    """Cada fila va al tardío si y solo si ``is_late`` de su clave natural."""
    for name in LATE_TABLES:
        columns = LATE_FILE_KEYS[name]
        main_header, main = read_csv(written.directory / f"{name}.csv")
        late_header, late = read_csv(written.directory / late_file_name(name))
        assert main_header == late_header, name
        main_keys = [late_key(row, columns) for row in main]
        late_keys = [late_key(row, columns) for row in late]
        assert not any(is_late(key) for key in main_keys), name
        assert all(is_late(key) for key in late_keys), name
        assert not set(main_keys) & set(late_keys), name
        assert len(set(main_keys)) + len(set(late_keys)) == len(main) + len(late), name
    assert any(entry.late_rows for entry in written.late_files)


def test_manifest_tables_are_base_rows_in_order(
    written: DatasetSummary, tables: dict[str, list[dict[str, str]]]
) -> None:
    assert tuple(written.tables) == TMS_TABLE_ORDER
    assert written.shipments == SHIPMENTS
    distinct = {
        "shipments": "shipment_id",
        "shipment_events": "event_id",
    }
    for name, rows in tables.items():
        key = distinct.get(name)
        base = len({row[key] for row in rows}) if key else len(rows)
        assert written.tables[name] == base, name


def _empty(column: str) -> Callable[[dict[str, str]], bool]:
    return lambda row: row[column] == ""


def _distinct_gap(rows: list[dict[str, str]], key: str) -> int:
    return len(rows) - len({row[key] for row in rows})


def _eligible(row: dict[str, str]) -> bool:
    return ts(row["reported_ts"]) < UNCLOSED_REPORTED_BEFORE


def test_manifest_counts_and_rates_reproduce_from_csv(
    written: DatasetSummary, tables: dict[str, list[dict[str, str]]]
) -> None:
    """Cada conteo y cada ``rate`` salen de leer los CSV.

    El denominador es la base de la tabla, salvo en ``unclosed``: solo las
    incidencias reportadas antes de ``CUTOFF_END`` menos 30 días.
    """
    predicates: dict[tuple[str, str, str], Callable[[dict[str, str]], bool]] = {
        ("shipments", "critical_null", "promised_delivery_ts"): _empty("promised_delivery_ts"),
        ("shipments", "critical_null", "customer_id"): _empty("customer_id"),
        ("shipments", "critical_null", "carrier_id"): _empty("carrier_id"),
        ("shipments", "delivered_before_pickup", "delivered_at"): lambda row: bool(
            row["delivered_at"]
        )
        and ts(row["delivered_at"]) < ts(row["picked_up_at"]),
        ("shipment_events", "critical_null", "event_type"): _empty("event_type"),
        ("shipment_events", "critical_null", "location_city"): _empty("location_city"),
        ("shipment_events", "impossible_date", "event_ts"): lambda row: ts(row["event_ts"])
        > CUTOFF_END,
        ("fuel_transactions", "critical_null", "gallons"): _empty("gallons"),
        ("fuel_transactions", "critical_null", "total_amount"): _empty("total_amount"),
        ("carrier_tenders", "no_response", "response_status"): _empty("response_status"),
        ("carrier_incidents", "unclosed", "resolved_ts"): lambda row: row["resolved_ts"] == ""
        and _eligible(row),
    }
    duplicates = {
        ("shipments", "duplicate", "shipment_id"): "shipment_id",
        ("shipment_events", "duplicate", "event_id"): "event_id",
    }
    keys = [(entry.table, entry.type, entry.column) for entry in written.defects]
    assert sorted(keys) == sorted([*predicates, *duplicates])
    assert len(keys) == 13

    for entry in written.defects:
        key = (entry.table, entry.type, entry.column)
        rows = tables[entry.table]
        if key in duplicates:
            count = _distinct_gap(rows, duplicates[key])
        else:
            count = sum(1 for row in rows if predicates[key](row))
        base = written.tables[entry.table]
        if entry.type == "unclosed":
            base = sum(1 for row in rows if _eligible(row))
        assert entry.count == count, key
        assert entry.rate == (round(count / base, RATE_DECIMALS) if base else 0.0), key


def test_entity_defects_are_present(written: DatasetSummary) -> None:
    counts = {(entry.table, entry.type): entry.count for entry in written.defects}
    for key in (
        ("shipment_events", "duplicate"),
        ("shipment_events", "impossible_date"),
        ("carrier_tenders", "no_response"),
        ("fuel_transactions", "critical_null"),
    ):
        assert counts[key] > 0, key


def test_one_defect_per_event(tables: dict[str, list[dict[str, str]]]) -> None:
    for row in tables["shipment_events"]:
        defects = (
            (row["event_type"] == "")
            + (row["location_city"] == "")
            + (ts(row["event_ts"]) > CUTOFF_END)
        )
        assert defects <= 1, row["event_id"]


def test_children_reference_base_shipments(
    tables: dict[str, list[dict[str, str]]], base_shipments: tuple[dict[str, str], ...]
) -> None:
    base_ids = {row["shipment_id"] for row in base_shipments}
    for name in CHILD_TABLES:
        referenced = {row["shipment_id"] for row in tables[name]}
        assert referenced, name
        assert referenced <= base_ids, name


def test_children_come_from_clean_rows(
    tables: dict[str, list[dict[str, str]]], base_shipments: tuple[dict[str, str], ...]
) -> None:
    """Los defectos de envíos no se propagan: los hijos usan la fila base limpia."""
    clean_carrier = {row["shipment_id"]: row["carrier_id"] for row in base_shipments}
    nulled = {row["shipment_id"] for row in tables["shipments"] if row["carrier_id"] == ""}
    assert nulled
    for row in tables["carrier_incidents"]:
        assert row["carrier_id"] == clean_carrier[row["shipment_id"]]
    accepted = [row for row in tables["carrier_tenders"] if row["response_status"] == "accepted"]
    assert any(row["shipment_id"] in nulled for row in accepted)
    for row in accepted:
        assert row["carrier_id"] == clean_carrier[row["shipment_id"]]

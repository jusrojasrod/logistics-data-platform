"""Pruebas de integración de los archivos ERP y CRM contra PostgreSQL.

Sobre un generado pequeño propio (500 envíos base, semilla 42, tres meses,
para que pagos y liquidaciones tengan filas antes de la fecha de corte) cargan
con ``run_files`` la carpeta principal y después su subcarpeta de tardíos en
``it_<hex>_raw``, con el registro de archivos en ``it_<hex>_meta``. Comprueban
que cada tabla recibe las filas de sus CSV, que una segunda carga de ambas
carpetas no inserta nada, que el registro guarda dos hashes por tabla, que los
metadatos de ejecución están completos y que las claves entre las tablas
cargadas son coherentes. Todo ocurre en esquemas ``it_<hex>_*``; ``tms``,
``raw`` y ``meta`` no se tocan.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import sql

from nexora.generators.dataset import write_dataset
from nexora.ingestion.batch.loaders import (
    CUSTOMERS,
    FILE_TABLES,
    LATE_DIRECTORY,
    run_files,
)

if TYPE_CHECKING:
    from conftest import Schemas

SCALE = "demo"
SHIPMENTS = 500
SEED = 42
MONTHS = 3

LATE_SUFFIX = "_late"
NEW_TABLES = tuple(t for t in FILE_TABLES if t.name != CUSTOMERS.name)
NEW_NAMES = tuple(t.name for t in NEW_TABLES)
SOURCE_SYSTEMS = {t.name: t.source_system for t in FILE_TABLES}
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Cada consulta cuenta las filas de una tabla nueva sin su referencia en ``raw``.
ORPHAN_CHECKS: dict[str, str] = {
    "invoice_lines_without_invoice": "select count(*) from {raw}.invoice_lines c where not exists "
    "(select 1 from {raw}.invoices p where p.invoice_id = c.invoice_id)",
    "payments_without_invoice": "select count(*) from {raw}.payments c where not exists "
    "(select 1 from {raw}.invoices p where p.invoice_id = c.invoice_id)",
    "tariffs_without_customer": "select count(*) from {raw}.tariffs c where not exists "
    "(select 1 from {raw}.customers p where p.customer_id = c.customer_id)",
    "contacts_without_customer": "select count(*) from {raw}.contacts c where not exists "
    "(select 1 from {raw}.customers p where p.customer_id = c.customer_id)",
    "contracts_without_customer": "select count(*) from {raw}.contracts c where not exists "
    "(select 1 from {raw}.customers p where p.customer_id = c.customer_id)",
    "leads_without_customer": "select count(*) from {raw}.leads c where not exists "
    "(select 1 from {raw}.customers p where p.customer_id = c.customer_id)",
    "invoices_without_customer": "select count(*) from {raw}.invoices c where not exists "
    "(select 1 from {raw}.customers p where p.customer_id = c.customer_id)",
    "costs_with_carrier_not_owned": "select count(*) from {raw}.shipment_costs "
    "where not (carrier_id = any(%(owned)s))",
    "settlements_with_carrier_not_third_party": "select count(*) from {raw}.carrier_settlements "
    "where not (carrier_id = any(%(third_party)s))",
    "shipments_with_costs_and_settlements": "select count(*) from "
    "(select shipment_id from {raw}.shipment_costs "
    "intersect select shipment_id from {raw}.carrier_settlements) s",
}


@pytest.fixture()
def erp_crm_dir(tmp_path: Path) -> Path:
    """Carpeta con todos los CSV del generador, sus tardíos y su manifiesto.

    500 envíos base (semilla 42, tres meses): con un solo mes, los pagos y las
    liquidaciones caen después de la fecha de corte y sus archivos quedan vacíos.
    """
    write_dataset(tmp_path, SCALE, SHIPMENTS, SEED, MONTHS)
    return tmp_path


def scalar(conn: psycopg.Connection, query: sql.Composable, params: object = None) -> object:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def csv_rows(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def main_file(erp_crm_dir: Path, name: str) -> Path:
    return erp_crm_dir / f"{name}.csv"


def late_file(erp_crm_dir: Path, name: str) -> Path:
    return erp_crm_dir / LATE_DIRECTORY / f"{name}{LATE_SUFFIX}.csv"


def load(conn: psycopg.Connection, s: Schemas, directory: Path, run_id: str) -> list:
    return run_files(conn, directory, run_id=run_id, raw_schema=s.raw, meta_schema=s.meta)


def load_both(conn: psycopg.Connection, s: Schemas, erp_crm_dir: Path, run: str) -> tuple:
    main = load(conn, s, erp_crm_dir, f"{run}-main")
    late = load(conn, s, erp_crm_dir / LATE_DIRECTORY, f"{run}-late")
    return main, late


def raw_count(conn: psycopg.Connection, s: Schemas, name: str) -> int:
    value = scalar(conn, sql.SQL("select count(*) from {}").format(sql.Identifier(s.raw, name)))
    assert isinstance(value, int)
    return value


def carriers_by_type(erp_crm_dir: Path) -> dict[str, list[str]]:
    by_type: dict[str, list[str]] = {}
    with main_file(erp_crm_dir, "carriers").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            by_type.setdefault(row["carrier_type"], []).append(row["carrier_id"])
    return by_type


def test_late_files_exist_for_every_new_table(erp_crm_dir: Path):
    """El generado tiene un CSV principal y uno tardío con filas por tabla nueva."""
    for name in NEW_NAMES:
        assert csv_rows(main_file(erp_crm_dir, name)) > 0, name
        assert csv_rows(late_file(erp_crm_dir, name)) > 0, name


def test_main_load_matches_main_files(pg: psycopg.Connection, schemas: Schemas, erp_crm_dir: Path):
    """Carga de la carpeta: cada tabla recibe las filas de su CSV principal."""
    results = load(pg, schemas, erp_crm_dir, "run-main")

    loaded = {r.source_file: (r.rows, r.skipped) for r in results}
    expected = {
        f"{t.name}.csv": (csv_rows(main_file(erp_crm_dir, t.name)), False) for t in FILE_TABLES
    }
    assert loaded == expected
    for table in FILE_TABLES:
        assert raw_count(pg, schemas, table.name) == expected[f"{table.name}.csv"][0], table.name


def test_late_load_matches_late_files(pg: psycopg.Connection, schemas: Schemas, erp_crm_dir: Path):
    """Carga de ``late/``: cada tabla nueva suma las filas de su CSV tardío."""
    load(pg, schemas, erp_crm_dir, "run-main")

    results = load(pg, schemas, erp_crm_dir / LATE_DIRECTORY, "run-late")

    loaded = {r.source_file: (r.rows, r.skipped) for r in results}
    expected = {
        f"{name}{LATE_SUFFIX}.csv": (csv_rows(late_file(erp_crm_dir, name)), False)
        for name in NEW_NAMES
    }
    assert loaded == expected
    for name in NEW_NAMES:
        total = csv_rows(main_file(erp_crm_dir, name)) + csv_rows(late_file(erp_crm_dir, name))
        assert raw_count(pg, schemas, name) == total, name
        late_rows = scalar(
            pg,
            sql.SQL("select count(*) from {} where run_id = %s").format(
                sql.Identifier(schemas.raw, name)
            ),
            ("run-late",),
        )
        assert late_rows == csv_rows(late_file(erp_crm_dir, name)), name


def test_second_load_of_both_inserts_no_rows(
    pg: psycopg.Connection, schemas: Schemas, erp_crm_dir: Path
):
    """Segunda carga de la carpeta y de ``late/``: 0 filas y todo omitido."""
    load_both(pg, schemas, erp_crm_dir, "run-1")
    before = {t.name: raw_count(pg, schemas, t.name) for t in FILE_TABLES}
    registry = sql.SQL("select count(*) from {}").format(
        sql.Identifier(schemas.meta, "ingested_files")
    )
    registered = scalar(pg, registry)

    main, late = load_both(pg, schemas, erp_crm_dir, "run-2")

    assert len(main) == len(FILE_TABLES)
    assert len(late) == len(NEW_TABLES)
    assert [(r.rows, r.skipped) for r in (*main, *late)] == [(0, True)] * (len(main) + len(late))
    assert {t.name: raw_count(pg, schemas, t.name) for t in FILE_TABLES} == before
    assert scalar(pg, registry) == registered


def test_registry_has_two_hashes_per_new_table(
    pg: psycopg.Connection, schemas: Schemas, erp_crm_dir: Path
):
    """``ingested_files``: dos hashes por tabla nueva y ``source_file`` sin ``/``."""
    load_both(pg, schemas, erp_crm_dir, "run")

    rows = pg.execute(
        sql.SQL(
            "select target_table, source_file, source_file_hash, row_count from {} order by 1, 2"
        ).format(sql.Identifier(schemas.meta, "ingested_files"))
    ).fetchall()

    by_table: dict[str, list[tuple]] = {}
    for target_table, source_file, source_file_hash, row_count in rows:
        assert "/" not in source_file
        assert HASH_PATTERN.match(source_file_hash), source_file
        by_table.setdefault(target_table, []).append((source_file, source_file_hash, row_count))
    assert sorted(by_table) == sorted(t.name for t in FILE_TABLES)
    assert [f for f, _, _ in by_table[CUSTOMERS.name]] == [f"{CUSTOMERS.name}.csv"]
    for name in NEW_NAMES:
        entries = by_table[name]
        assert [(f, n) for f, _, n in entries] == [
            (f"{name}.csv", csv_rows(main_file(erp_crm_dir, name))),
            (f"{name}{LATE_SUFFIX}.csv", csv_rows(late_file(erp_crm_dir, name))),
        ]
        assert len({h for _, h, _ in entries}) == 2, name


def test_metadata_is_complete(pg: psycopg.Connection, schemas: Schemas, erp_crm_dir: Path):
    """Metadatos sin nulos, hash de 64 hex y ``source_system`` ``erp`` o ``crm``."""
    load_both(pg, schemas, erp_crm_dir, "run")

    for table in FILE_TABLES:
        missing, bad_file, bad_hash, systems = pg.execute(
            sql.SQL(
                "select count(*) filter (where run_id is null or source_system is null "
                "or source_file is null or source_file_hash is null or ingested_at is null), "
                "count(*) filter (where source_file like '%/%'), "
                "count(*) filter (where source_file_hash !~ '^[0-9a-f]{{64}}$'), "
                "array_agg(distinct source_system) from {}"
            ).format(sql.Identifier(schemas.raw, table.name))
        ).fetchone()
        assert (missing, bad_file, bad_hash) == (0, 0, 0), table.name
        assert systems == [SOURCE_SYSTEMS[table.name]], table.name
    assert {SOURCE_SYSTEMS[name] for name in ("contacts", "contracts", "leads")} == {"crm"}
    assert {
        SOURCE_SYSTEMS[name] for name in NEW_NAMES if name not in ("contacts", "contracts", "leads")
    } == {"erp"}


def test_no_orphans_after_both_loads(pg: psycopg.Connection, schemas: Schemas, erp_crm_dir: Path):
    """Tras cargar carpeta y ``late/``: 0 huérfanos entre las tablas cargadas."""
    load_both(pg, schemas, erp_crm_dir, "run")
    carriers = carriers_by_type(erp_crm_dir)
    assert carriers.get("owned") and carriers.get("third_party")
    params = {"owned": carriers["owned"], "third_party": carriers["third_party"]}
    for name in NEW_NAMES:
        assert raw_count(pg, schemas, name) > 0, name

    raw = sql.Identifier(schemas.raw)
    orphans = {
        check: scalar(pg, sql.SQL(query).format(raw=raw), params)
        for check, query in ORPHAN_CHECKS.items()
    }

    assert orphans == dict.fromkeys(ORPHAN_CHECKS, 0)

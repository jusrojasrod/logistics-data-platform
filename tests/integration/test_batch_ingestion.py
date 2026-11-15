"""Pruebas de integración de la ingesta batch contra PostgreSQL.

Publican en ``it_<hex>_tms`` un generado pequeño (500 envíos base con defectos,
entre ellos copias de un mismo ``shipment_id``), extraen a ``it_<hex>_raw`` por
watermark y cargan los archivos ERP con su registro en ``it_<hex>_meta``.

El avance diario se simula con dos fechas de corte: ``D-1 = 2026-11-07`` y
``D = 2026-11-08``. Las filas que se publican con ``D`` incluyen envíos tardíos
(``pickup_date`` anterior a ``D-1``) y copias de un mismo ``shipment_id``. Las
pruebas comprueban relaciones entre ``tms`` y ``raw``, no cifras fijas.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
from psycopg import sql

from nexora.generators.tms_publisher import publish_directory
from nexora.ingestion.batch.loaders import SHIPMENTS, run_files, run_tms

if TYPE_CHECKING:
    from conftest import Schemas

D_MINUS_1 = date(2026, 11, 7)
D = date(2026, 11, 8)

CUSTOMERS_FILE = "customers.csv"


def scalar(conn: psycopg.Connection, query: sql.Composable, params: tuple = ()) -> object:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def table(schema: str, name: str) -> sql.Identifier:
    return sql.Identifier(schema, name)


def watermarks(conn: psycopg.Connection, s: Schemas) -> list[tuple]:
    query = sql.SQL("select * from {} order by source_table").format(
        table(s.meta, "batch_watermarks")
    )
    return conn.execute(query).fetchall()


def shipments_watermark(conn: psycopg.Connection, s: Schemas) -> object:
    query = sql.SQL("select watermark_ts from {} where source_table = %s").format(
        table(s.meta, "batch_watermarks")
    )
    return scalar(conn, query, (f"{s.tms}.shipments",))


def inserted(results: list, name: str) -> int:
    return next(result.rows for result in results if result.table.endswith(f".{name}"))


def extract(conn: psycopg.Connection, s: Schemas, run_id: str) -> list:
    return run_tms(conn, run_id=run_id, tms_schema=s.tms, raw_schema=s.raw, meta_schema=s.meta)


def publish(conn: psycopg.Connection, s: Schemas, directory: Path, until: date | None) -> None:
    publish_directory(conn, directory, until=until, schema=s.tms)


def window_counts(conn: psycopg.Connection, s: Schemas) -> tuple[int, int, int]:
    """Filas de ``tms.shipments`` posteriores al watermark: total, tardías y con copias."""
    query = sql.SQL(
        "with w as (select s.* from {tms} s where s.updated_at > %s) "
        "select count(*), "
        "count(*) filter (where pickup_date < %s), "
        "(select count(*) from (select shipment_id from w group by 1 having count(*) > 1) c) "
        "from w"
    ).format(tms=table(s.tms, "shipments"))
    row = conn.execute(query, (shipments_watermark(conn, s), D_MINUS_1)).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def advance_to_d(conn: psycopg.Connection, s: Schemas, directory: Path) -> None:
    """Publica hasta ``D-1``, extrae y publica hasta ``D`` (sin extraer)."""
    publish(conn, s, directory, D_MINUS_1)
    extract(conn, s, "run-d-minus-1")
    publish(conn, s, directory, D)


def test_tms_has_duplicate_shipment_ids(pg: psycopg.Connection, schemas: Schemas, source_dir: Path):
    """``tms`` con duplicados de ``shipment_id``: la fuente conserva todas las copias."""
    publish(pg, schemas, source_dir, None)
    total, distinct = pg.execute(
        sql.SQL("select count(*), count(distinct shipment_id) from {}").format(
            table(schemas.tms, "shipments")
        )
    ).fetchone()
    assert total > distinct


def test_window_of_d_has_late_rows_and_copies(
    pg: psycopg.Connection, schemas: Schemas, source_dir: Path
):
    """La ventana de ``D`` contiene tardías y copias de un mismo ``shipment_id``."""
    advance_to_d(pg, schemas, source_dir)
    total, late, with_copies = window_counts(pg, schemas)
    assert total > 0
    assert late >= 1
    assert with_copies >= 1


def test_reextraction_inserts_no_rows(pg: psycopg.Connection, schemas: Schemas, source_dir: Path):
    """Reextracción sin filas nuevas: inserta 0 y no cambia el watermark."""
    publish(pg, schemas, source_dir, D_MINUS_1)
    first = extract(pg, schemas, "run-first")
    assert inserted(first, SHIPMENTS.name) > 0
    before = watermarks(pg, schemas)
    assert len(before) == 4

    second = extract(pg, schemas, "run-second")

    assert [result.rows for result in second] == [0, 0, 0, 0]
    assert watermarks(pg, schemas) == before
    for name in ("shipments", "carriers", "routes", "service_types"):
        assert scalar(
            pg, sql.SQL("select count(*) from {}").format(table(schemas.raw, name))
        ) == scalar(pg, sql.SQL("select count(*) from {}").format(table(schemas.tms, name)))


def test_late_rows_are_extracted_in_window(
    pg: psycopg.Connection, schemas: Schemas, source_dir: Path
):
    """Tardíos: la extracción de ``D`` inserta exactamente las filas posteriores al watermark."""
    advance_to_d(pg, schemas, source_dir)
    previous = shipments_watermark(pg, schemas)
    expected, late, _ = window_counts(pg, schemas)
    assert late >= 1

    results = extract(pg, schemas, "run-d")

    assert inserted(results, SHIPMENTS.name) == expected
    raw_shipments = table(schemas.raw, "shipments")
    tms_shipments = table(schemas.tms, "shipments")
    raw_late = scalar(
        pg,
        sql.SQL("select count(*) from {} where run_id = %s and pickup_date < %s").format(
            raw_shipments
        ),
        ("run-d", D_MINUS_1),
    )
    assert raw_late == late
    columns = sql.SQL(", ").join(sql.Identifier(name) for name in SHIPMENTS.column_names)
    difference = sql.SQL(
        "select count(*) from ("
        "(select {c} from {raw} where run_id = %s "
        "except all select {c} from {tms} where updated_at > %s) "
        "union all "
        "(select {c} from {tms} where updated_at > %s "
        "except all select {c} from {raw} where run_id = %s)) d"
    ).format(c=columns, raw=raw_shipments, tms=tms_shipments)
    assert scalar(pg, difference, ("run-d", previous, previous, "run-d")) == 0


def test_shipment_id_in_a_single_run_id(pg: psycopg.Connection, schemas: Schemas, source_dir: Path):
    """Ningún ``shipment_id`` aparece en dos ``run_id``, con duplicados presentes en ``raw``."""
    advance_to_d(pg, schemas, source_dir)
    extract(pg, schemas, "run-d")
    publish(pg, schemas, source_dir, None)
    extract(pg, schemas, "run-rest")

    shipments = table(schemas.raw, "shipments")
    total, distinct, runs = pg.execute(
        sql.SQL(
            "select count(*), count(distinct shipment_id), count(distinct run_id) from {}"
        ).format(shipments)
    ).fetchone()
    assert total > distinct
    assert runs == 3
    assert total == scalar(
        pg, sql.SQL("select count(*) from {}").format(table(schemas.tms, "shipments"))
    )
    split = scalar(
        pg,
        sql.SQL(
            "select count(*) from (select shipment_id from {} group by 1 "
            "having count(distinct run_id) > 1) s"
        ).format(shipments),
    )
    assert split == 0


def load_files(conn: psycopg.Connection, s: Schemas, directory: Path, run_id: str) -> list:
    return run_files(conn, directory, run_id=run_id, raw_schema=s.raw, meta_schema=s.meta)


def erp_dir(source_dir: Path, tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("erp")
    shutil.copy(source_dir / CUSTOMERS_FILE, directory / CUSTOMERS_FILE)
    return directory


def file_rows(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle) - 1


def test_file_reload_inserts_no_rows(
    pg: psycopg.Connection, schemas: Schemas, source_dir: Path, tmp_path_factory
):
    """Recarga de archivo: el mismo archivo no vuelve a cargarse."""
    directory = erp_dir(source_dir, tmp_path_factory)
    first = load_files(pg, schemas, directory, "run-files-1")
    assert [(r.source_file, r.rows, r.skipped) for r in first] == [
        (CUSTOMERS_FILE, file_rows(directory / CUSTOMERS_FILE), False)
    ]

    second = load_files(pg, schemas, directory, "run-files-2")

    assert [(r.source_file, r.rows, r.skipped) for r in second] == [(CUSTOMERS_FILE, 0, True)]
    assert scalar(
        pg, sql.SQL("select count(*) from {}").format(table(schemas.raw, "customers"))
    ) == file_rows(directory / CUSTOMERS_FILE)


def test_renamed_copy_is_skipped(
    pg: psycopg.Connection, schemas: Schemas, source_dir: Path, tmp_path_factory
):
    """Copia renombrada: mismo contenido con otro nombre, se omite por hash."""
    directory = erp_dir(source_dir, tmp_path_factory)
    load_files(pg, schemas, directory, "run-files-1")
    shutil.copy(directory / CUSTOMERS_FILE, directory / "customers_copy.csv")

    results = load_files(pg, schemas, directory, "run-files-2")

    assert sorted((r.source_file, r.rows, r.skipped) for r in results) == [
        ("customers.csv", 0, True),
        ("customers_copy.csv", 0, True),
    ]
    assert (
        scalar(pg, sql.SQL("select count(*) from {}").format(table(schemas.meta, "ingested_files")))
        == 1
    )


def test_modified_file_is_loaded(
    pg: psycopg.Connection, schemas: Schemas, source_dir: Path, tmp_path_factory
):
    """Archivo modificado: otro hash, se carga y queda una fila por tabla y hash."""
    directory = erp_dir(source_dir, tmp_path_factory)
    load_files(pg, schemas, directory, "run-files-1")
    lines = (directory / CUSTOMERS_FILE).read_text(encoding="utf-8").splitlines(keepends=True)
    fields = lines[1].split(",")
    fields[5] = "Tunja"
    lines[1] = ",".join(fields)
    (directory / "customers_late.csv").write_text("".join(lines), encoding="utf-8")

    results = load_files(pg, schemas, directory, "run-files-2")

    loaded = {r.source_file: (r.rows, r.skipped) for r in results}
    assert loaded["customers.csv"] == (0, True)
    assert loaded["customers_late.csv"] == (len(lines) - 1, False)
    registry = table(schemas.meta, "ingested_files")
    assert pg.execute(
        sql.SQL(
            "select target_table, count(*), count(distinct source_file_hash) from {} group by 1"
        ).format(registry)
    ).fetchall() == [("customers", 2, 2)]

"""Pruebas de integración de las entidades TMS contra PostgreSQL.

Sobre el mismo generado pequeño que ``test_batch_ingestion.py`` comprueban que
cada defecto declarado en el manifiesto se reproduce con SQL una vez publicado,
que el hash de lote leído por bloques coincide con la definición de referencia
calculada en la base con ``string_agg`` y que la extracción se detiene con
``LookupError`` si falta una tabla de origen. Todo ocurre en esquemas
``it_<hex>_*``; ``tms``, ``raw`` y ``meta`` no se tocan.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import sql

from nexora.generators.defects import MANIFEST_FILE_NAME
from nexora.generators.shipments import CUTOFF_END
from nexora.generators.tms_publisher import TMS_CARRIERS, publish_directory
from nexora.ingestion.batch.loaders import (
    SHIPMENT_EVENTS,
    SHIPMENTS,
    TMS_TABLES,
    RawTable,
    run_tms,
    stream_batch_hash,
)

if TYPE_CHECKING:
    from conftest import Schemas

D_MINUS_1 = date(2026, 11, 7)
D = date(2026, 11, 8)

HASH_TABLES: tuple[RawTable, ...] = (SHIPMENTS, SHIPMENT_EVENTS)
HEX_64 = re.compile(r"[0-9a-f]{64}")

# Expresión que cuenta cada tipo de defecto del manifiesto; ``{column}`` es la
# columna declarada en la entrada y ``%(cut)s`` el final del día de corte.
DEFECT_COUNTS: dict[str, str] = {
    "duplicate": "count(*) - count(distinct {column})",
    "critical_null": "count(*) filter (where {column} is null)",
    "delivered_before_pickup": "count(*) filter (where delivered_at < picked_up_at)",
    "impossible_date": "count(*) filter (where {column} > %(cut)s)",
    "no_response": "count(*) filter (where {column} is null)",
    "unclosed": "count(*) filter (where {column} is null "
    "and reported_ts < %(cut)s - interval '30 days')",
}


def scalar(conn: psycopg.Connection, query: sql.Composable, params: object = ()) -> object:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def table(schema: str, name: str) -> sql.Identifier:
    return sql.Identifier(schema, name)


def extract(conn: psycopg.Connection, s: Schemas, run_id: str, **options: object) -> list:
    return run_tms(
        conn, run_id=run_id, tms_schema=s.tms, raw_schema=s.raw, meta_schema=s.meta, **options
    )


def read_manifest(directory: Path) -> dict:
    with (directory / MANIFEST_FILE_NAME).open(encoding="utf-8") as handle:
        return json.load(handle)


def cutoff_end(manifest: dict) -> datetime:
    """Último instante del día de corte declarado en el manifiesto, en UTC."""
    return datetime.combine(date.fromisoformat(manifest["cutoff_date"]), time(23, 59, 59), UTC)


def defect_count(conn: psycopg.Connection, s: Schemas, entry: dict, cut: datetime) -> int:
    query = sql.SQL("select " + DEFECT_COUNTS[entry["type"]] + " from {source}").format(
        column=sql.Identifier(entry["column"]), source=table(s.tms, entry["table"])
    )
    value = scalar(conn, query, {"cut": cut})
    assert isinstance(value, int)
    return value


def test_manifest_entries_reproduce_after_full_publish(
    pg: psycopg.Connection, schemas: Schemas, source_dir: Path
):
    """Publicado sin ``--until``, cada entrada del manifiesto se reproduce con SQL."""
    manifest = read_manifest(source_dir)
    publish_directory(pg, source_dir, until=None, schema=schemas.tms)
    cut = cutoff_end(manifest)
    assert cut == CUTOFF_END

    expected = {(d["table"], d["type"], d["column"]): d["count"] for d in manifest["defects"]}
    found = {
        (d["table"], d["type"], d["column"]): defect_count(pg, schemas, d, cut)
        for d in manifest["defects"]
    }

    assert len(expected) == 13
    assert expected[("shipment_events", "impossible_date", "event_ts")] > 0
    assert found == expected


def reference_hash(
    conn: psycopg.Connection,
    raw_table: RawTable,
    source: sql.Identifier,
    condition: sql.SQL,
    params: tuple,
) -> tuple[int, str]:
    """Conteo y SHA-256 del lote calculados en la base con ``string_agg``."""
    columns = sql.SQL(", ").join(sql.Identifier(name) for name in raw_table.column_names)
    query = sql.SQL(
        "select count(*), encode(sha256(convert_to(coalesce("
        "string_agg(row({columns})::text, E'\\n' order by tms_row_id), ''), 'UTF8')), 'hex') "
        "from {source} where {condition}"
    ).format(columns=columns, source=source, condition=condition)
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0], row[1]


def window_of(
    conn: psycopg.Connection, s: Schemas, name: str
) -> tuple[sql.SQL, tuple[datetime, ...]]:
    """Condición de la ventana pendiente de ``<tms>.<name>`` y sus parámetros."""
    upper = scalar(conn, sql.SQL("select max(updated_at) from {}").format(table(s.tms, name)))
    exists = scalar(conn, "select to_regclass(%s) is not null", (f"{s.meta}.batch_watermarks",))
    lower = None
    if exists:
        row = conn.execute(
            sql.SQL("select watermark_ts from {} where source_table = %s").format(
                table(s.meta, "batch_watermarks")
            ),
            (f"{s.tms}.{name}",),
        ).fetchone()
        lower = None if row is None else row[0]
    assert isinstance(upper, datetime)
    if lower is None:
        return sql.SQL("updated_at <= %s"), (upper,)
    return sql.SQL("updated_at > %s and updated_at <= %s"), (lower, upper)


def check_window_hashes(conn: psycopg.Connection, s: Schemas) -> dict[str, str]:
    """Compara, por tabla, el hash en flujo (1 fila y por defecto) con la referencia."""
    hashes = {}
    for raw_table in HASH_TABLES:
        condition, params = window_of(conn, s, raw_table.name)
        source = table(s.tms, raw_table.name)
        with conn.transaction():
            conn.execute("set local timezone to 'UTC'")
            reference = reference_hash(conn, raw_table, source, condition, params)
            one_row = stream_batch_hash(conn, raw_table, source, condition, params, fetch_rows=1)
            default = stream_batch_hash(conn, raw_table, source, condition, params)
        assert reference[0] > 0, raw_table.name
        assert one_row == reference, raw_table.name
        assert default == reference, raw_table.name
        hashes[raw_table.name] = reference[1]
    return hashes


def raw_hashes(conn: psycopg.Connection, s: Schemas, name: str, run_id: str) -> list[str]:
    query = sql.SQL("select distinct source_file_hash from {} where run_id = %s").format(
        table(s.raw, name)
    )
    return [row[0] for row in conn.execute(query, (run_id,)).fetchall()]


def test_stream_hash_matches_string_agg(pg: psycopg.Connection, schemas: Schemas, source_dir: Path):
    """Hash con bloque de 1 fila y por defecto = ``string_agg``, en la primera ventana y en la de ``D``."""
    publish_directory(pg, source_dir, until=D_MINUS_1, schema=schemas.tms)
    first = check_window_hashes(pg, schemas)
    extract(pg, schemas, "run-d-minus-1")

    publish_directory(pg, source_dir, until=D, schema=schemas.tms)
    second = check_window_hashes(pg, schemas)
    extract(pg, schemas, "run-d")

    for name in first:
        assert raw_hashes(pg, schemas, name, "run-d-minus-1") == [first[name]]
        assert raw_hashes(pg, schemas, name, "run-d") == [second[name]]
        assert first[name] != second[name]


def test_source_file_hash_has_64_hex(pg: psycopg.Connection, schemas: Schemas, source_dir: Path):
    """``source_file_hash`` de 64 hex en cada tabla extraída, con bloque de 1 fila y por defecto."""
    publish_directory(pg, source_dir, until=D_MINUS_1, schema=schemas.tms)
    first = extract(pg, schemas, "run-one-row", fetch_rows=1)
    publish_directory(pg, source_dir, until=D, schema=schemas.tms)
    second = extract(pg, schemas, "run-default")

    for results, run_id in ((first, "run-one-row"), (second, "run-default")):
        for result in results:
            name = result.table.rsplit(".", 1)[1]
            stored = raw_hashes(pg, schemas, name, run_id)
            if result.rows == 0:
                assert result.source_file_hash is None
                assert stored == []
                continue
            assert HEX_64.fullmatch(result.source_file_hash), name
            assert stored == [result.source_file_hash], name
    assert all(result.rows > 0 for result in first)


def regclass_exists(conn: psycopg.Connection, schema: str, name: str) -> bool:
    value = scalar(conn, "select to_regclass(%s) is not null", (f"{schema}.{name}",))
    assert isinstance(value, bool)
    return value


def test_empty_tms_raises_lookup_error_without_watermarks(pg: psycopg.Connection, schemas: Schemas):
    """``tms`` vacío: ``LookupError`` en la primera tabla y sin watermarks."""
    with pytest.raises(LookupError, match=re.escape(f"{schemas.tms}.carriers")):
        extract(pg, schemas, "run-empty")

    assert not regclass_exists(pg, schemas.meta, "batch_watermarks")
    assert not regclass_exists(pg, schemas.raw, "carriers")


def test_only_carriers_raises_lookup_error_on_routes(
    pg: psycopg.Connection, schemas: Schemas, source_dir: Path
):
    """Solo ``carriers``: ``LookupError`` en ``routes`` con ``carriers`` ya extraída."""
    publish_directory(pg, source_dir, until=None, schema=schemas.tms, tables=(TMS_CARRIERS,))
    assert TMS_TABLES[0].name == "carriers"
    assert TMS_TABLES[1].name == "routes"

    with pytest.raises(LookupError, match=re.escape(f"{schemas.tms}.routes")):
        extract(pg, schemas, "run-carriers")

    tms_carriers = scalar(
        pg, sql.SQL("select count(*) from {}").format(table(schemas.tms, "carriers"))
    )
    raw_carriers = scalar(
        pg,
        sql.SQL("select count(*) from {} where run_id = %s").format(table(schemas.raw, "carriers")),
        ("run-carriers",),
    )
    assert isinstance(tms_carriers, int)
    assert tms_carriers > 0
    assert raw_carriers == tms_carriers
    marks = pg.execute(
        sql.SQL("select source_table, watermark_ts, run_id from {}").format(
            table(schemas.meta, "batch_watermarks")
        )
    ).fetchall()
    top = scalar(
        pg, sql.SQL("select max(updated_at) from {}").format(table(schemas.tms, "carriers"))
    )
    assert marks == [(f"{schemas.tms}.carriers", top, "run-carriers")]
    assert not regclass_exists(pg, schemas.raw, "routes")

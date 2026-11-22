"""Pruebas de integración del paso de exposición contra recursos aislados.

Cada prueba usa colecciones ``it_<hex>_*`` creadas con ``nexora_app`` dentro de
``raw`` y ``meta`` y un esquema ``it_<hex>_raw`` en PostgreSQL, con payloads y
entradas de drift deterministas. Los nombres se inyectan en el paso de exposición
desde esas fixtures, de modo que nunca se escribe en ``raw.api_payloads``,
``meta.api_schema_drift`` ni en el esquema ``raw`` principal. Los recursos
temporales se borran siempre al terminar, incluso si la prueba falla.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from psycopg import sql

import nexora.ingestion.api.expose as expose
from nexora.common.api_contracts import (
    CARRIER_INCIDENTS_CONTRACT,
    SHIPMENT_EVENTS_CONTRACT,
    SHIPMENTS_CONTRACT,
)
from nexora.ingestion.api.client import ApiPage, PageEnvelope
from nexora.ingestion.api.expose import (
    ExposeVerificationError,
    PostgresItemStore,
    ResourceNames,
    expose_run,
    verify_run,
)
from nexora.ingestion.api.landing import (
    DRIFT_COLLECTION,
    DRIFT_DATABASE,
    PAYLOADS_COLLECTION,
    PAYLOADS_DATABASE,
    DriftEntry,
    land_pages,
    record_drift,
)

SOURCE = "tms"
TIMESTAMP = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
EXPOSED_AT = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_temporary_resources_left(pg: psycopg.Connection, mongo_app: Any) -> Any:
    """Comprueba al terminar que no quedan esquemas ni colecciones ``it_<hex>_*``."""
    yield
    schemas = [
        row[0]
        for row in pg.execute(
            "select schema_name from information_schema.schemata where schema_name like 'it\\_%'"
        ).fetchall()
    ]
    assert not schemas, f"quedaron esquemas temporales: {schemas}"
    collections = [
        f"{db.name}.{name}"
        for db in (mongo_app[PAYLOADS_DATABASE], mongo_app[DRIFT_DATABASE])
        for name in db.list_collection_names()
        if name.startswith("it_")
    ]
    assert not collections, f"quedaron colecciones temporales: {collections}"


def _page(endpoint: str, *, number: int = 1, items: list[dict[str, Any]]) -> ApiPage:
    """Página determinista con los elementos indicados y el sobre completo."""
    body: dict[str, Any] = {
        "data": items,
        "page": number,
        "page_size": 200,
        "total_pages": 1,
        "next_page": None,
    }
    return ApiPage(
        endpoint=endpoint,
        page=number,
        payload=body,
        envelope=PageEnvelope.model_validate(body),
        content=json.dumps(body).encode(),
    )


def _shipment(identifier: str) -> dict[str, Any]:
    return {"shipment_id": identifier, "ordered_qty": 1}


def _event(identifier: str) -> dict[str, Any]:
    return {"event_id": identifier, "severity": "info"}


def _incident(identifier: str) -> dict[str, Any]:
    return {"incident_id": identifier, "severity": "low"}


def _names(mongo_api_collections: Any, schemas: Any) -> ResourceNames:
    """Nombres inyectados desde las fixtures, con los valores de producción por defecto."""
    return replace(
        ResourceNames(),
        payloads_collection=mongo_api_collections.payloads.name,
        drift_collection=mongo_api_collections.drift.name,
        items_schema=schemas.raw,
    )


def _store(pg: psycopg.Connection, names: ResourceNames) -> PostgresItemStore:
    return PostgresItemStore(pg, names)


def _land(
    mongo_api_collections: Any, run_id: str, pages: list[ApiPage], timestamp: datetime
) -> None:
    land_pages(
        mongo_api_collections.payloads,
        pages,
        run_id=run_id,
        source_system=SOURCE,
        clock=lambda: timestamp,
    )


def _record_drift(
    mongo_api_collections: Any,
    *,
    run_id: str,
    endpoint: str,
    page: int,
    source_key: str,
    field: str,
    timestamp: datetime,
) -> None:
    entry = DriftEntry(
        endpoint=endpoint,
        page=page,
        field=field,
        change_type="retyped",
        expected_type="number",
        actual_type="string",
        source_key=source_key,
        payload_sha256="a" * 64,
    )
    record_drift(
        mongo_api_collections.drift,
        entry,
        run_id=run_id,
        source_system=SOURCE,
        detected_at=timestamp,
    )


def test_exposes_one_row_per_item_into_the_injected_schema(
    pg: psycopg.Connection, schemas: Any, mongo_api_collections: Any
) -> None:
    names = _names(mongo_api_collections, schemas)
    assert names.payloads_collection != PAYLOADS_COLLECTION
    assert names.drift_collection != DRIFT_COLLECTION
    assert names.items_schema != "raw"
    assert names.items_table == ResourceNames().items_table

    run_id = "it-expose-basic"
    pages = [
        _page(SHIPMENTS_CONTRACT.endpoint, items=[_shipment("s-1"), _shipment("s-2")]),
        _page(SHIPMENT_EVENTS_CONTRACT.endpoint, items=[_event("e-1")]),
        _page(CARRIER_INCIDENTS_CONTRACT.endpoint, items=[_incident("i-1")]),
    ]
    _land(mongo_api_collections, run_id, pages, TIMESTAMP)
    store = _store(pg, names)
    result = expose_run(
        mongo_api_collections.payloads,
        mongo_api_collections.drift,
        store,
        run_id,
        exposed_at=EXPOSED_AT,
    )

    assert (result.pages, result.elements, result.inserted) == (3, 4, 4)
    table = sql.Identifier(names.items_schema, names.items_table)
    rows = pg.execute(
        sql.SQL(
            "select endpoint, page, item_index, source_key, dataset_name, payload, "
            "payload_sha256, ingested_at, exposed_at from {} where run_id = %s "
            "order by endpoint, item_index"
        ).format(table),
        (run_id,),
    ).fetchall()
    assert len(rows) == sum(len(page.payload["data"]) for page in pages) == 4
    assert {row[0] for row in rows} == {page.endpoint for page in pages}
    for row in rows:
        assert all(value is not None for value in (row[4], row[5], row[6], row[7], row[8]))
        assert row[8] == EXPOSED_AT
        assert isinstance(row[5], dict)
    by_endpoint = {row[0]: row for row in rows}
    assert by_endpoint[SHIPMENTS_CONTRACT.endpoint][3] == "s-2"
    assert by_endpoint[SHIPMENTS_CONTRACT.endpoint][4] == "tms_shipments"
    assert by_endpoint[SHIPMENT_EVENTS_CONTRACT.endpoint][3] == "e-1"
    assert by_endpoint[CARRIER_INCIDENTS_CONTRACT.endpoint][3] == "i-1"

    verified = verify_run(mongo_api_collections.payloads, store, run_id)
    assert (verified.pages, verified.elements) == (3, 4)


def test_repeat_run_id_is_idempotent_on_the_isolated_table(
    pg: psycopg.Connection, schemas: Any, mongo_api_collections: Any
) -> None:
    names = _names(mongo_api_collections, schemas)
    run_id = "it-expose-idempotent"
    pages = [
        _page(SHIPMENTS_CONTRACT.endpoint, items=[_shipment("s-1"), _shipment("s-2")]),
        _page(SHIPMENTS_CONTRACT.endpoint, number=2, items=[_shipment("s-3")]),
    ]
    _land(mongo_api_collections, run_id, pages, TIMESTAMP)
    store = _store(pg, names)
    table = sql.Identifier(names.items_schema, names.items_table)
    count_hashes = sql.SQL(
        "select count(*), count(distinct payload_sha256), min(exposed_at) "
        "from {} where run_id = %s"
    ).format(table)

    first = expose_run(
        mongo_api_collections.payloads,
        mongo_api_collections.drift,
        store,
        run_id,
        exposed_at=EXPOSED_AT,
    )
    before = pg.execute(count_hashes, (run_id,)).fetchone()
    second = expose_run(
        mongo_api_collections.payloads,
        mongo_api_collections.drift,
        store,
        run_id,
        exposed_at=LATER,
    )
    after = pg.execute(count_hashes, (run_id,)).fetchone()

    assert (first.inserted, second.inserted) == (3, 0)
    assert second.elements == first.elements == 3
    assert before == after == (3, 2, EXPOSED_AT)


def test_verify_fails_on_empty_run_id_instead_of_zero_equals_zero(
    pg: psycopg.Connection, schemas: Any, mongo_api_collections: Any
) -> None:
    store = _store(pg, _names(mongo_api_collections, schemas))

    with pytest.raises(ExposeVerificationError, match="sin elementos"):
        verify_run(mongo_api_collections.payloads, store, "it-expose-no-pages")

    empty_run = "it-expose-empty-data"
    _land(
        mongo_api_collections,
        empty_run,
        [_page(SHIPMENTS_CONTRACT.endpoint, items=[])],
        TIMESTAMP,
    )
    with pytest.raises(ExposeVerificationError, match="sin elementos"):
        verify_run(mongo_api_collections.payloads, store, empty_run)


def test_drift_flag_comes_from_the_isolated_meta_collection(
    pg: psycopg.Connection, schemas: Any, mongo_api_collections: Any
) -> None:
    names = _names(mongo_api_collections, schemas)
    run_id = "it-expose-drift"
    endpoint = SHIPMENTS_CONTRACT.endpoint
    _land(
        mongo_api_collections,
        run_id,
        [_page(endpoint, items=[_shipment("s-1"), _shipment("s-2")])],
        TIMESTAMP,
    )
    _record_drift(
        mongo_api_collections,
        run_id=run_id,
        endpoint=endpoint,
        page=1,
        source_key="s-1",
        field="ordered_weight_kg",
        timestamp=TIMESTAMP,
    )

    expose_run(
        mongo_api_collections.payloads,
        mongo_api_collections.drift,
        _store(pg, names),
        run_id,
        exposed_at=EXPOSED_AT,
    )

    rows = pg.execute(
        sql.SQL("select source_key, has_drift_flag, drift_fields from {} where run_id = %s").format(
            sql.Identifier(names.items_schema, names.items_table)
        ),
        (run_id,),
    ).fetchall()
    by_key = {row[0]: row for row in rows}
    assert by_key["s-1"][1] is True
    assert by_key["s-1"][2] == ["ordered_weight_kg"]
    assert by_key["s-2"][1] is False
    assert by_key["s-2"][2] == []


def test_defaults_without_injection_keep_production_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = ResourceNames()
    assert (defaults.payloads_database, defaults.payloads_collection) == (
        PAYLOADS_DATABASE,
        PAYLOADS_COLLECTION,
    )
    assert (defaults.drift_database, defaults.drift_collection) == (
        DRIFT_DATABASE,
        DRIFT_COLLECTION,
    )
    assert (defaults.items_schema, defaults.items_table) == ("raw", "api_items")

    for env_name in expose.ENV_NAMES.values():
        monkeypatch.delenv(env_name, raising=False)
    assert ResourceNames.from_env() == defaults

    monkeypatch.setenv("NEXORA_EXPOSE_ITEMS_SCHEMA", "it_env_raw")
    from_env = ResourceNames.from_env()
    assert from_env.items_schema == "it_env_raw"
    assert from_env.items_table == defaults.items_table
    assert from_env.payloads_collection == defaults.payloads_collection
    assert from_env.drift_collection == defaults.drift_collection

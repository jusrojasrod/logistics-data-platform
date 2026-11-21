"""Pruebas unitarias del paso de exposición con dobles de MongoDB y PostgreSQL.

Los dobles solo ofrecen lectura e inserción con la misma clave primaria de la
tabla nueva, de modo que se comprueban el grano, los metadatos, la idempotencia y
la bandera de drift sin abrir conexiones ni tocar las bases principales.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import nexora.ingestion.api.expose as expose
from nexora.ingestion.api.expose import (
    ITEM_COLUMNS,
    ResourceNames,
    create_items_statement,
    expose_run,
    verify_run,
)
from nexora.ingestion.api.landing import (
    DRIFT_COLLECTION,
    DRIFT_DATABASE,
    METADATA_FIELDS,
    PAYLOADS_COLLECTION,
    PAYLOADS_DATABASE,
)

RUN_ID = "run-1"
SOURCE = "tms"
ENDPOINT = "/v1/shipments"
EVENTS_ENDPOINT = "/v1/shipment_events"
TIMESTAMP = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
EXPOSED_AT = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
PRIMARY_KEY = ("run_id", "endpoint", "page", "item_index")


class FakeCollection:
    """Colección de solo lectura con ``find`` por igualdad."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def find(self, filter: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
        for document in self.documents:
            if all(document.get(name) == value for name, value in filter.items()):
                yield dict(document)


class FakeItemStore:
    """Destino en memoria con la misma PK y semántica ``on conflict do nothing``."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.ensure_calls = 0
        self.insert_calls = 0

    def ensure_table(self) -> None:
        self.ensure_calls += 1

    def insert_items(self, rows: Iterable[Mapping[str, Any]]) -> int:
        self.insert_calls += 1
        inserted = 0
        for row in rows:
            key = tuple(row[name] for name in PRIMARY_KEY)
            if any(tuple(old[name] for name in PRIMARY_KEY) == key for old in self.rows):
                continue
            self.rows.append(dict(row))
            inserted += 1
        return inserted

    def counts_by_page(self, run_id: str) -> Mapping[tuple[str, int], int]:
        counts: dict[tuple[str, int], int] = {}
        for row in self.rows:
            if row["run_id"] == run_id:
                key = (row["endpoint"], row["page"])
                counts[key] = counts.get(key, 0) + 1
        return counts


def _landed(
    *,
    endpoint: str = ENDPOINT,
    page: int = 1,
    items: list[dict[str, Any]] | None = None,
    digest: str = "a" * 64,
) -> dict[str, Any]:
    data = items if items is not None else [{"shipment_id": "s-1"}]
    return {
        "run_id": RUN_ID,
        "source_system": SOURCE,
        "endpoint": endpoint,
        "page": page,
        "payload_sha256": digest,
        "ingested_at": TIMESTAMP,
        "payload": {
            "data": data,
            "page": page,
            "page_size": 200,
            "total_pages": 1,
            "next_page": None,
        },
    }


def _drift(
    *,
    endpoint: str = ENDPOINT,
    page: int = 1,
    source_key: str = "s-1",
    field: str = "weight",
    run_id: str = RUN_ID,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "endpoint": endpoint,
        "page": page,
        "source_key": source_key,
        "field": field,
        "rule_id": "DQ-008",
    }


def test_expands_one_row_per_data_item_with_six_metadata() -> None:
    payloads = FakeCollection(
        [
            _landed(items=[{"shipment_id": "s-1"}, {"shipment_id": "s-2"}]),
            _landed(items=[{"shipment_id": "s-3"}], page=2, digest="b" * 64),
        ]
    )
    store = FakeItemStore()
    result = expose_run(payloads, FakeCollection([]), store, RUN_ID, exposed_at=EXPOSED_AT)

    assert (result.pages, result.elements, result.inserted) == (2, 3, 3)
    assert store.ensure_calls == 1
    assert store.insert_calls == 1
    assert [row["item_index"] for row in store.rows] == [0, 1, 0]
    assert [row["page"] for row in store.rows] == [1, 1, 2]
    assert {row["payload_sha256"] for row in store.rows} == {"a" * 64, "b" * 64}
    for row in store.rows:
        assert all(row[name] is not None for name in METADATA_FIELDS)
        assert row["run_id"] == RUN_ID
        assert row["source_system"] == SOURCE
        assert row["endpoint"] == ENDPOINT
        assert row["ingested_at"] == TIMESTAMP
        assert row["dataset_name"] == "tms_shipments"
        assert row["exposed_at"] == EXPOSED_AT
        assert row["has_drift_flag"] is False
    assert json.loads(store.rows[0]["payload"]) == {"shipment_id": "s-1"}
    assert json.loads(store.rows[2]["payload"]) == {"shipment_id": "s-3"}


def test_repeat_run_id_inserts_nothing_and_keeps_hashes_and_exposed_at() -> None:
    payloads = FakeCollection([_landed(items=[{"shipment_id": "s-1"}, {"shipment_id": "s-2"}])])
    store = FakeItemStore()

    first = expose_run(payloads, FakeCollection([]), store, RUN_ID, exposed_at=EXPOSED_AT)
    hashes = [row["payload_sha256"] for row in store.rows]
    payload_values = [row["payload"] for row in store.rows]
    second = expose_run(payloads, FakeCollection([]), store, RUN_ID, exposed_at=LATER)

    assert (first.inserted, second.inserted) == (2, 0)
    assert second.elements == first.elements == 2
    assert len(store.rows) == 2
    assert [row["payload_sha256"] for row in store.rows] == hashes
    assert [row["payload"] for row in store.rows] == payload_values
    assert {row["exposed_at"] for row in store.rows} == {EXPOSED_AT}


def test_drift_flag_comes_from_meta_by_four_part_key() -> None:
    payloads = FakeCollection(
        [
            _landed(items=[{"shipment_id": "s-1"}, {"shipment_id": "s-2"}]),
            _landed(endpoint=EVENTS_ENDPOINT, items=[{"event_id": "e-1"}]),
        ]
    )
    drift = FakeCollection(
        [
            _drift(source_key="s-1", field="weight"),
            _drift(run_id="otro", source_key="s-2", field="por-otro-run"),
            _drift(page=2, source_key="s-2", field="por-otra-pagina"),
            _drift(endpoint=EVENTS_ENDPOINT, source_key="s-2", field="por-otro-endpoint"),
            _drift(endpoint=EVENTS_ENDPOINT, source_key="e-1", field="severity"),
        ]
    )
    store = FakeItemStore()
    expose_run(payloads, drift, store, RUN_ID, exposed_at=EXPOSED_AT)

    by_key = {(row["endpoint"], row["source_key"]): row for row in store.rows}
    marked = by_key[(ENDPOINT, "s-1")]
    assert marked["has_drift_flag"] is True
    assert json.loads(marked["drift_fields"]) == ["weight"]
    assert by_key[(ENDPOINT, "s-2")]["has_drift_flag"] is False
    assert json.loads(by_key[(ENDPOINT, "s-2")]["drift_fields"]) == []
    events = by_key[(EVENTS_ENDPOINT, "e-1")]
    assert events["has_drift_flag"] is True
    assert json.loads(events["drift_fields"]) == ["severity"]


def test_only_three_tms_resources_are_exposed() -> None:
    payloads = FakeCollection(
        [
            _landed(endpoint="/v1/weather", items=[{"city": "x"}]),
            _landed(endpoint=EVENTS_ENDPOINT, items=[{"event_id": "e-1"}]),
        ]
    )
    store = FakeItemStore()
    result = expose_run(payloads, FakeCollection([]), store, RUN_ID, exposed_at=EXPOSED_AT)

    assert (result.pages, result.elements) == (1, 1)
    assert store.rows[0]["endpoint"] == EVENTS_ENDPOINT
    assert store.rows[0]["dataset_name"] == "tms_shipment_events"


def test_verify_matches_counts_and_fails_on_empty_instead_of_zero_equals_zero() -> None:
    payloads = FakeCollection([_landed(items=[{"shipment_id": "s-1"}, {"shipment_id": "s-2"}])])
    store = FakeItemStore()
    expose_run(payloads, FakeCollection([]), store, RUN_ID, exposed_at=EXPOSED_AT)

    verified = verify_run(payloads, store, RUN_ID)
    assert (verified.pages, verified.elements) == (1, 2)

    with pytest.raises(expose.ExposeVerificationError, match="sin elementos"):
        verify_run(FakeCollection([]), FakeItemStore(), RUN_ID)
    with pytest.raises(expose.ExposeVerificationError, match="sin elementos"):
        verify_run(FakeCollection([_landed(items=[])]), store, RUN_ID)


def test_names_default_to_production_and_injection_reaches_ddl() -> None:
    names = ResourceNames()
    assert (names.payloads_database, names.payloads_collection) == (
        PAYLOADS_DATABASE,
        PAYLOADS_COLLECTION,
    )
    assert (names.drift_database, names.drift_collection) == (DRIFT_DATABASE, DRIFT_COLLECTION)
    assert (names.items_schema, names.items_table) == ("raw", "api_items")

    injected = ResourceNames(
        payloads_collection="it_abc_api_payloads",
        drift_collection="it_abc_api_schema_drift",
        items_schema="it_abc_raw",
    )
    ddl = create_items_statement(injected).as_string(None)
    assert '"it_abc_raw"."api_items"' in ddl
    assert "primary key (run_id, endpoint, page, item_index)" in ddl
    assert (
        *METADATA_FIELDS,
        "dataset_name",
        "source_key",
        "item_index",
        "payload",
        "has_drift_flag",
        "drift_fields",
        "exposed_at",
    ) == ITEM_COLUMNS


def test_from_env_uses_defaults_without_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in expose.ENV_NAMES.values():
        monkeypatch.delenv(env_name, raising=False)
    assert ResourceNames.from_env() == ResourceNames()


def test_expose_module_never_emits_update_or_delete() -> None:
    source = Path(expose.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("update", "delete"):
        assert forbidden not in source
    assert "on conflict (run_id, endpoint, page, item_index) do nothing" in source

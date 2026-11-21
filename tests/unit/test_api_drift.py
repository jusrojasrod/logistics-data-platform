"""Pruebas unitarias de la detección de drift y del bloqueo DQ-008.

El detector compara el payload con el contrato declarado; no usa el plan del mock.
Los dobles de MongoDB registran el orden de escritura para comprobar que el payload
se aterriza y el drift se registra antes de marcar la ejecución como bloqueada.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from nexora.common.api_contracts import (
    JSON_BOOLEAN,
    JSON_DATE,
    JSON_INTEGER,
    JSON_NUMBER,
    JSON_STRING,
    JSON_TIMESTAMP,
    SHIPMENTS_CONTRACT,
)
from nexora.ingestion.api.client import ApiPage, PageEnvelope
from nexora.ingestion.api.drift import (
    ApiSchemaDriftError,
    detect_page_drift,
    json_type_of,
    land_and_flag_drift,
)
from nexora.ingestion.api.landing import (
    DRIFT_KEY,
    METADATA_FIELDS,
    UNIQUE_KEY,
    sha256_bytes,
)

RUN = "run-drift"
SOURCE = "tms"
ENDPOINT = "/v1/shipments"
TIMESTAMP = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _valid_item() -> dict[str, Any]:
    """Elemento conforme al contrato de ``shipments``."""
    return {
        "shipment_id": "s-1",
        "customer_id": "c-1",
        "carrier_id": "ca-1",
        "route_id": "r-1",
        "service_type_id": "st-1",
        "pickup_ts": "2026-11-18T08:00:00Z",
        "pickup_date": "2026-11-18",
        "promised_delivery_ts": None,
        "picked_up_at": "2026-11-18T09:00:00Z",
        "delivered_at": None,
        "ordered_qty": 10,
        "delivered_qty": None,
        "ordered_weight_kg": 12.5,
        "delivered_weight_kg": None,
        "definitive_failure_flag": False,
    }


def _page(items: list[dict[str, Any]], *, number: int = 1) -> ApiPage:
    body: dict[str, Any] = {
        "data": items,
        "page": number,
        "page_size": 10,
        "total_pages": 1,
        "next_page": None,
    }
    return ApiPage(
        endpoint=ENDPOINT,
        page=number,
        payload=body,
        envelope=PageEnvelope.model_validate(body),
        content=json.dumps(body).encode(),
    )


class EventCollection:
    """Colección append-only que deja constancia del orden de las escrituras."""

    def __init__(self, events: list[str], label: str, unique_key: tuple[str, ...]) -> None:
        self.documents: list[dict[str, Any]] = []
        self.events = events
        self.label = label
        self.unique_key = unique_key

    def find_one(self, filter: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return next(
            (doc for doc in self.documents if all(doc.get(k) == v for k, v in filter.items())),
            None,
        )

    def insert_one(self, document: Mapping[str, Any]) -> Any:
        self.events.append(self.label)
        key = tuple(document.get(name) for name in self.unique_key)
        if any(tuple(doc.get(name) for name in self.unique_key) == key for doc in self.documents):
            raise DuplicateKeyError("índice único")
        stored = dict(document)
        stored["_id"] = f"{self.label}-{len(self.documents) + 1}"
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])


class FakeRunRegistry:
    """Doble del enganche con ``meta.api_runs``."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[str, str, int]] = []

    def mark_blocked(self, *, run_id: str, dataset_name: str, drift_count: int) -> None:
        self.events.append("blocked")
        self.calls.append((run_id, dataset_name, drift_count))


def _collections() -> tuple[list[str], EventCollection, EventCollection]:
    events: list[str] = []
    return (
        events,
        EventCollection(events, "payload", UNIQUE_KEY),
        EventCollection(events, "drift", DRIFT_KEY),
    )


def test_json_type_of_distinguishes_dates_and_numbers() -> None:
    assert json_type_of("2026-11-18") == JSON_DATE
    assert json_type_of("2026-11-18T08:00:00Z") == JSON_TIMESTAMP
    assert json_type_of(42) == JSON_INTEGER
    assert json_type_of(12.5) == JSON_NUMBER
    assert json_type_of("diez") == JSON_STRING
    assert json_type_of(True) == JSON_BOOLEAN
    assert json_type_of(None) == "null"


def test_valid_page_and_nullable_nulls_have_no_drift() -> None:
    assert detect_page_drift(_page([_valid_item()]), SHIPMENTS_CONTRACT) == ()
    item = _valid_item()
    item["delivered_at"] = None
    item["delivered_qty"] = None
    assert detect_page_drift(_page([item]), SHIPMENTS_CONTRACT) == ()


@pytest.mark.parametrize(
    ("strategy", "field", "change_type", "expected_type", "actual_type"),
    [
        ("added", "drift_injected_field", "added", "", JSON_STRING),
        ("removed", "ordered_weight_kg", "removed", JSON_NUMBER, "missing"),
        ("retyped", "ordered_weight_kg", "retyped", JSON_NUMBER, JSON_STRING),
        ("retyped_ts", "pickup_ts", "retyped", JSON_TIMESTAMP, JSON_INTEGER),
    ],
)
def test_detect_page_drift_reports_each_change_type(
    strategy: str, field: str, change_type: str, expected_type: str, actual_type: str
) -> None:
    item = _valid_item()
    if strategy == "added":
        item[field] = "drift"
    elif strategy == "removed":
        item.pop(field)
    elif strategy == "retyped":
        item[field] = "diez"
    else:
        item[field] = 42
    page = _page([item])
    entries = detect_page_drift(page, SHIPMENTS_CONTRACT)
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.field, entry.change_type) == (field, change_type)
    assert (entry.expected_type, entry.actual_type) == (expected_type, actual_type)
    assert entry.source_key == "s-1"
    assert entry.endpoint == ENDPOINT
    assert entry.page == 1
    assert entry.payload_sha256 == sha256_bytes(page.content)


def test_drift_lands_records_blocks_then_raises() -> None:
    events, payloads, rows = _collections()
    registry = FakeRunRegistry(events)
    item = _valid_item()
    item.pop("ordered_weight_kg")

    with pytest.raises(ApiSchemaDriftError) as exc:
        land_and_flag_drift(
            payloads,
            rows,
            [_page([item])],
            SHIPMENTS_CONTRACT,
            run_id=RUN,
            source_system=SOURCE,
            registry=registry,
            clock=lambda: TIMESTAMP,
        )

    assert exc.value.run_id == RUN
    assert exc.value.dataset == "tms_shipments"
    assert exc.value.endpoint == ENDPOINT
    assert exc.value.drift_count == 1
    assert len(exc.value.entries) == 1

    assert len(payloads.documents) == 1
    landed = payloads.documents[0]
    assert all(landed[name] is not None for name in METADATA_FIELDS)
    assert landed["payload"]["data"][0] == item
    assert landed["ingested_at"] == TIMESTAMP

    assert len(rows.documents) == 1
    row = rows.documents[0]
    assert row["rule_id"] == "DQ-008"
    assert row["severity"] == "blocking"
    assert row["field"] == "ordered_weight_kg"
    assert row["change_type"] == "removed"
    assert row["source_key"] == "s-1"
    assert row["detected_at"] == TIMESTAMP

    assert registry.calls == [(RUN, "tms_shipments", 1)]
    assert events == ["payload", "drift", "blocked"]


def test_drift_raises_even_without_a_registry_hook() -> None:
    _, payloads, rows = _collections()
    item = _valid_item()
    item["ordered_weight_kg"] = "diez"
    with pytest.raises(ApiSchemaDriftError):
        land_and_flag_drift(
            payloads,
            rows,
            [_page([item])],
            SHIPMENTS_CONTRACT,
            run_id=RUN,
            clock=lambda: TIMESTAMP,
        )
    assert len(payloads.documents) == 1
    assert len(rows.documents) == 1


def test_one_drift_row_per_altered_payload() -> None:
    _, payloads, rows = _collections()
    registry = FakeRunRegistry([])
    first = _valid_item()
    first["shipment_id"] = "s-1"
    first["ordered_weight_kg"] = "diez"
    second = _valid_item()
    second["shipment_id"] = "s-2"
    second.pop("pickup_date")
    with pytest.raises(ApiSchemaDriftError) as exc:
        land_and_flag_drift(
            payloads,
            rows,
            [_page([first, second])],
            SHIPMENTS_CONTRACT,
            run_id=RUN,
            registry=registry,
            clock=lambda: TIMESTAMP,
        )
    assert exc.value.drift_count == 2
    assert len(rows.documents) == 2
    assert {row["source_key"] for row in rows.documents} == {"s-1", "s-2"}
    assert registry.calls == [(RUN, "tms_shipments", 2)]


def test_without_drift_does_not_record_or_block() -> None:
    events, payloads, rows = _collections()
    registry = FakeRunRegistry(events)
    result = land_and_flag_drift(
        payloads,
        rows,
        [_page([_valid_item()])],
        SHIPMENTS_CONTRACT,
        run_id=RUN,
        source_system=SOURCE,
        registry=registry,
        clock=lambda: TIMESTAMP,
    )
    assert [item.inserted for item in result.landed] == [True]
    assert result.drift_entries == ()
    assert result.recorded == ()
    assert rows.documents == []
    assert registry.calls == []
    assert events == ["payload"]

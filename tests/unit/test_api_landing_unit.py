"""Pruebas unitarias del aterrizaje con un doble de MongoDB en memoria.

El doble solo ofrece ``find_one`` e ``insert_one`` y simula el índice único de
la colección, de modo que el contrato se comprueba sin Mongo, sin red y sin
operaciones de actualización o borrado.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from nexora.ingestion.api.client import ApiPage, PageEnvelope
from nexora.ingestion.api.landing import (
    DRIFT_RULE_ID,
    DRIFT_SEVERITY,
    UNIQUE_KEY,
    DriftEntry,
    LandingValidationError,
    PayloadRewriteError,
    land_page,
    land_pages,
    record_drift,
    sha256_bytes,
)

RUN_ID = "run-1"
SOURCE = "tms"
ENDPOINT = "/v1/shipments"
TIMESTAMP = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
METADATA = ("run_id", "source_system", "endpoint", "page", "payload_sha256", "ingested_at")


def _page(*, endpoint: str = ENDPOINT, number: int = 1, content: bytes | None = None) -> ApiPage:
    raw = content if content is not None else b'{ "data" : [ {"shipment_id": "s-1"} ] }'
    body = {
        "data": [{"shipment_id": "s-1"}],
        "page": number,
        "page_size": 1,
        "total_pages": 1,
        "next_page": None,
    }
    if content is not None:
        body = json.loads(content)
        body["page"] = number
    return ApiPage(
        endpoint=endpoint,
        page=number,
        payload=body,
        envelope=PageEnvelope.model_validate(body),
        content=raw,
    )


class FakeCollection:
    """Colección append-only: nidifica por ``(run_id, endpoint, page)``."""

    def __init__(self, *, unique_key: tuple[str, ...] = UNIQUE_KEY) -> None:
        self.documents: list[dict[str, Any]] = []
        self.unique_key = unique_key
        self.insert_calls = 0
        self.fail_next_insert = False

    def find_one(self, filter: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return next(
            (doc for doc in self.documents if all(doc.get(k) == v for k, v in filter.items())),
            None,
        )

    def insert_one(self, document: Mapping[str, Any]) -> Any:
        self.insert_calls += 1
        if self.fail_next_insert:
            self.fail_next_insert = False
            raise DuplicateKeyError("índice único")
        key = tuple(document.get(name) for name in self.unique_key)
        if any(tuple(doc.get(name) for name in self.unique_key) == key for doc in self.documents):
            raise DuplicateKeyError("índice único")
        stored = dict(document)
        stored["_id"] = f"id-{len(self.documents) + 1}"
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])


def _drift_entry(*, payload_sha256: str = "a" * 64, field: str = "weight") -> DriftEntry:
    return DriftEntry(
        endpoint=ENDPOINT,
        page=1,
        field=field,
        change_type="retyped",
        expected_type="number",
        actual_type="string",
        source_key="s-1",
        payload_sha256=payload_sha256,
    )


def test_hash_uses_raw_bytes_not_reserialized_json() -> None:
    page = _page()
    assert sha256_bytes(page.content) == hashlib.sha256(page.content).hexdigest()
    reserialized = json.dumps(page.payload, sort_keys=True).encode()
    assert sha256_bytes(page.content) != hashlib.sha256(reserialized).hexdigest()
    with pytest.raises(LandingValidationError, match="bytes"):
        sha256_bytes("no son bytes")  # type: ignore[arg-type]


def test_land_page_writes_six_metadata_fields_and_raw_payload() -> None:
    collection = FakeCollection()
    page = _page()
    result = land_page(collection, page, run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    assert result.inserted is True
    assert collection.insert_calls == 1
    stored = collection.documents[0]
    assert all(stored[name] is not None for name in METADATA)
    assert stored["run_id"] == RUN_ID
    assert stored["source_system"] == SOURCE
    assert stored["endpoint"] == ENDPOINT
    assert stored["page"] == 1
    assert stored["payload_sha256"] == sha256_bytes(page.content)
    assert stored["ingested_at"] == TIMESTAMP
    assert stored["payload"] == page.payload
    assert stored["content_type"] == "application/json"
    assert stored["http_status"] == 200


def test_same_key_same_hash_is_noop_and_preserves_id_and_timestamp() -> None:
    collection = FakeCollection()
    page = _page()
    first = land_page(collection, page, run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    later = datetime(2026, 11, 18, 13, 0, tzinfo=UTC)
    second = land_page(collection, page, run_id=RUN_ID, source_system=SOURCE, ingested_at=later)
    assert first.inserted is True
    assert second.inserted is False
    assert collection.insert_calls == 1
    assert len(collection.documents) == 1
    assert collection.documents[0]["ingested_at"] == TIMESTAMP
    assert collection.documents[0]["_id"] == "id-1"


def test_same_key_other_hash_raises_payload_rewrite_error() -> None:
    collection = FakeCollection()
    changed = _page(
        content=b'{"data": [{"shipment_id": "s-2"}], "page": 1, '
        b'"page_size": 1, "total_pages": 1, "next_page": null}'
    )
    land_page(collection, _page(), run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    with pytest.raises(PayloadRewriteError) as exc:
        land_page(collection, changed, run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    assert exc.value.run_id == RUN_ID
    assert exc.value.endpoint == ENDPOINT
    assert exc.value.page == 1
    assert exc.value.stored_sha256 == sha256_bytes(_page().content)
    assert exc.value.incoming_sha256 == sha256_bytes(changed.content)
    assert collection.insert_calls == 1
    assert len(collection.documents) == 1


def test_other_run_id_with_same_hash_lands_new_document() -> None:
    collection = FakeCollection()
    page = _page()
    land_page(collection, page, run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    result = land_page(
        collection, page, run_id="run-2", source_system=SOURCE, ingested_at=TIMESTAMP
    )
    assert result.inserted is True
    assert len(collection.documents) == 2
    assert {doc["run_id"] for doc in collection.documents} == {RUN_ID, "run-2"}
    assert collection.documents[0]["payload_sha256"] == collection.documents[1]["payload_sha256"]
    assert collection.documents[0]["_id"] != collection.documents[1]["_id"]


def test_duplicate_key_race_with_same_hash_is_noop() -> None:
    collection = FakeCollection()
    page = _page()
    land_page(collection, page, run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    collection.fail_next_insert = True
    result = land_page(collection, page, run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    assert result.inserted is False
    assert len(collection.documents) == 1


def test_duplicate_key_race_with_other_hash_raises() -> None:
    collection = FakeCollection()
    land_page(collection, _page(), run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    changed = _page(
        content=b'{"data": [{"shipment_id": "s-3"}], "page": 1, '
        b'"page_size": 1, "total_pages": 1, "next_page": null}'
    )
    collection.fail_next_insert = True
    with pytest.raises(PayloadRewriteError):
        land_page(collection, changed, run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    assert len(collection.documents) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": ""},
        {"source_system": ""},
        {"endpoint": "   "},
    ],
)
def test_invalid_metadata_is_rejected_before_insert(overrides: dict[str, Any]) -> None:
    collection = FakeCollection()
    page_overrides = {"endpoint": overrides["endpoint"]} if "endpoint" in overrides else {}
    with pytest.raises(LandingValidationError):
        land_page(
            collection,
            _page(**page_overrides),
            run_id=overrides.get("run_id", RUN_ID),
            source_system=overrides.get("source_system", SOURCE),
            ingested_at=TIMESTAMP,
        )
    assert collection.insert_calls == 0


def test_page_number_zero_is_rejected_before_insert() -> None:
    collection = FakeCollection()
    page = _page()
    invalid = ApiPage(
        endpoint=page.endpoint,
        page=0,
        payload=page.payload,
        envelope=page.envelope,
        content=page.content,
    )
    with pytest.raises(LandingValidationError, match="page"):
        land_page(collection, invalid, run_id=RUN_ID, source_system=SOURCE, ingested_at=TIMESTAMP)
    assert collection.insert_calls == 0


def test_land_pages_shares_the_injected_clock() -> None:
    collection = FakeCollection()
    pages = [_page(number=1), _page(number=2)]
    results = land_pages(
        collection, pages, run_id=RUN_ID, source_system=SOURCE, clock=lambda: TIMESTAMP
    )
    assert [result.inserted for result in results] == [True, True]
    assert [doc["page"] for doc in collection.documents] == [1, 2]
    assert {doc["ingested_at"] for doc in collection.documents} == {TIMESTAMP}


def test_drift_row_is_blocking_and_idempotent() -> None:
    collection = FakeCollection(
        unique_key=("run_id", "endpoint", "page", "source_key", "field", "change_type")
    )
    entry = _drift_entry()
    first = record_drift(
        collection, entry, run_id=RUN_ID, source_system=SOURCE, detected_at=TIMESTAMP
    )
    second = record_drift(
        collection, entry, run_id=RUN_ID, source_system=SOURCE, detected_at=TIMESTAMP
    )
    assert first.inserted is True
    assert second.inserted is False
    assert collection.insert_calls == 1
    stored = collection.documents[0]
    assert stored["rule_id"] == DRIFT_RULE_ID == "DQ-008"
    assert stored["severity"] == DRIFT_SEVERITY == "blocking"
    for name in (
        "endpoint",
        "page",
        "field",
        "change_type",
        "expected_type",
        "actual_type",
        "source_key",
        "payload_sha256",
        "detected_at",
    ):
        assert name in stored
    assert all(stored[name] is not None for name in METADATA)
    assert stored["detected_at"] == stored["ingested_at"] == TIMESTAMP


def test_invalid_drift_entry_is_rejected() -> None:
    collection = FakeCollection(
        unique_key=("run_id", "endpoint", "page", "source_key", "field", "change_type")
    )
    with pytest.raises(LandingValidationError, match="change_type"):
        record_drift(
            collection,
            DriftEntry(
                endpoint=ENDPOINT,
                page=1,
                field="weight",
                change_type="unknown",
                expected_type="number",
                actual_type="string",
                source_key="s-1",
                payload_sha256="a" * 64,
            ),
            run_id=RUN_ID,
            source_system=SOURCE,
            detected_at=TIMESTAMP,
        )
    assert collection.insert_calls == 0


def test_landing_module_never_updates_or_deletes() -> None:
    from pathlib import Path

    import nexora.ingestion.api.landing as landing

    source = Path(landing.__file__).read_text(encoding="utf-8")
    for forbidden in ("update_one", "update_many", "delete_one", "delete_many", "replace_one"):
        assert forbidden not in source

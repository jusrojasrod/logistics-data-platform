"""Pruebas de integración del aterrizaje de API contra la MongoDB local.

Cada prueba usa colecciones ``it_<hex>_*`` creadas con ``nexora_app`` dentro de
``raw`` y ``meta``, con el validador y los índices copiados de las colecciones
reales. No se usa root, no se crean usuarios y las colecciones se borran al
terminar incluso si la prueba falla.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from nexora.ingestion.api.client import ApiPage, PageEnvelope
from nexora.ingestion.api.landing import (
    DRIFT_COLLECTION,
    DRIFT_RULE_ID,
    DRIFT_SEVERITY,
    METADATA_FIELDS,
    PAYLOADS_COLLECTION,
    DriftEntry,
    PayloadRewriteError,
    land_page,
    land_pages,
    record_drift,
    sha256_bytes,
)

RUN = "it-run-landing"
SOURCE = "tms"
ENDPOINT = "/v1/shipments"
TIMESTAMP = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _page(
    *, number: int = 1, items: list[dict[str, Any]] | None = None, content: bytes | None = None
) -> ApiPage:
    rows = items if items is not None else [{"shipment_id": f"s-{number}"}]
    body: dict[str, Any] = {
        "data": rows,
        "page": number,
        "page_size": 10,
        "total_pages": 2,
        "next_page": 2 if number == 1 else None,
    }
    raw = content if content is not None else json.dumps(body).encode()
    if content is not None:
        body = json.loads(content)
        body["page"] = number
    return ApiPage(
        endpoint=ENDPOINT,
        page=number,
        payload=body,
        envelope=PageEnvelope.model_validate(body),
        content=raw,
    )


def test_lands_one_document_per_page_with_six_metadata_fields(mongo_api_collections: Any) -> None:
    pages = [_page(number=1), _page(number=2)]
    results = land_pages(
        mongo_api_collections.payloads,
        pages,
        run_id=RUN,
        source_system=SOURCE,
        clock=lambda: TIMESTAMP,
    )
    assert [result.inserted for result in results] == [True, True]
    docs = list(mongo_api_collections.payloads.find({}).sort("page", 1))
    assert len(docs) == 2
    for doc, page in zip(docs, pages, strict=True):
        assert all(doc[field] is not None for field in METADATA_FIELDS)
        assert doc["run_id"] == RUN
        assert doc["source_system"] == SOURCE
        assert doc["endpoint"] == ENDPOINT
        assert doc["page"] == page.page
        assert doc["payload_sha256"] == sha256_bytes(page.content)
        assert doc["ingested_at"] == TIMESTAMP
        assert doc["payload"] == page.payload
        assert doc["content_type"] == "application/json"
        assert doc["http_status"] == 200

    indexes = {index["name"]: index for index in mongo_api_collections.payloads.list_indexes()}
    assert list(indexes["uniq_run_endpoint_page"]["key"].items()) == [
        ("run_id", 1),
        ("endpoint", 1),
        ("page", 1),
    ]
    assert indexes["uniq_run_endpoint_page"].get("unique") is True
    info = next(
        mongo_api_collections.payloads.database.list_collections(
            filter={"name": mongo_api_collections.payloads.name}
        )
    )
    assert info["options"]["validator"]
    assert info["options"]["validationAction"] == "error"


def test_validator_and_unique_index_reject_bad_document_and_duplicate(
    mongo_api_collections: Any,
) -> None:
    collection = mongo_api_collections.payloads
    land_page(collection, _page(), run_id=RUN, source_system=SOURCE, ingested_at=TIMESTAMP)
    with pytest.raises(WriteError) as invalid:
        collection.insert_one({"run_id": RUN, "payload": {}})
    assert invalid.value.code == 121
    first = collection.find_one({"run_id": RUN, "page": 1})
    duplicate = {key: value for key, value in first.items() if key != "_id"}
    duplicate["payload_sha256"] = "b" * 64
    with pytest.raises(DuplicateKeyError) as duplicated:
        collection.insert_one(duplicate)
    assert duplicated.value.code == 11000
    assert collection.count_documents({}) == 1


def test_same_run_id_is_idempotent_and_other_run_id_lands_again(
    mongo_api_collections: Any,
) -> None:
    collection = mongo_api_collections.payloads
    pages = [_page(number=1), _page(number=2)]
    first = land_pages(collection, pages, run_id=RUN, source_system=SOURCE, clock=lambda: TIMESTAMP)
    later = datetime(2026, 11, 18, 13, 0, tzinfo=UTC)
    second = land_pages(collection, pages, run_id=RUN, source_system=SOURCE, clock=lambda: later)
    assert [result.inserted for result in first] == [True, True]
    assert [result.inserted for result in second] == [False, False]
    before = {doc["_id"]: doc["ingested_at"] for doc in collection.find({"run_id": RUN})}
    assert len(before) == 2
    assert set(before.values()) == {TIMESTAMP}

    third = land_pages(
        collection, pages, run_id="it-run-later", source_system=SOURCE, clock=lambda: later
    )
    assert [result.inserted for result in third] == [True, True]
    docs = list(collection.find({}))
    assert len(docs) == 4
    assert len({doc["_id"] for doc in docs}) == 4
    assert len({doc["run_id"] for doc in docs}) == 2
    assert len({doc["payload_sha256"] for doc in docs}) == 2
    after = {doc["_id"]: doc["ingested_at"] for doc in collection.find({"run_id": RUN})}
    assert after == before


def test_payload_rewrite_error_on_same_key_different_content(mongo_api_collections: Any) -> None:
    collection = mongo_api_collections.payloads
    changed = _page(
        content=json.dumps(
            {
                "data": [{"shipment_id": "s-otro"}],
                "page": 1,
                "page_size": 10,
                "total_pages": 2,
                "next_page": 2,
            }
        ).encode()
    )
    land_page(collection, _page(), run_id=RUN, source_system=SOURCE, ingested_at=TIMESTAMP)
    with pytest.raises(PayloadRewriteError) as exc:
        land_page(collection, changed, run_id=RUN, source_system=SOURCE, ingested_at=TIMESTAMP)
    assert exc.value.run_id == RUN
    assert exc.value.stored_sha256 == sha256_bytes(_page().content)
    assert exc.value.incoming_sha256 == sha256_bytes(changed.content)
    assert collection.count_documents({}) == 1
    assert collection.find_one({"run_id": RUN})["payload"] == _page().payload


def test_drift_rows_are_blocking_and_payload_is_preserved(mongo_api_collections: Any) -> None:
    page = _page(items=[{"shipment_id": "s-1", "weight": "diez"}])
    land_page(
        mongo_api_collections.payloads,
        page,
        run_id=RUN,
        source_system=SOURCE,
        ingested_at=TIMESTAMP,
    )
    entry = DriftEntry(
        endpoint=ENDPOINT,
        page=1,
        field="weight",
        change_type="retyped",
        expected_type="number",
        actual_type="string",
        source_key="s-1",
        payload_sha256=sha256_bytes(page.content),
    )
    first = record_drift(
        mongo_api_collections.drift,
        entry,
        run_id=RUN,
        source_system=SOURCE,
        detected_at=TIMESTAMP,
    )
    second = record_drift(
        mongo_api_collections.drift,
        entry,
        run_id=RUN,
        source_system=SOURCE,
        detected_at=TIMESTAMP,
    )
    assert first.inserted is True
    assert second.inserted is False
    rows = list(mongo_api_collections.drift.find({}))
    assert len(rows) == 1
    row = rows[0]
    assert row["rule_id"] == DRIFT_RULE_ID == "DQ-008"
    assert row["severity"] == DRIFT_SEVERITY == "blocking"
    assert row["endpoint"] == ENDPOINT
    assert row["field"] == "weight"
    assert row["change_type"] == "retyped"
    assert row["source_key"] == "s-1"
    assert row["payload_sha256"] == sha256_bytes(page.content)
    assert row["detected_at"] == TIMESTAMP
    assert mongo_api_collections.payloads.count_documents({}) == 1
    assert mongo_api_collections.payloads.find_one({})["payload"] == page.payload


def test_temporary_collections_stay_outside_production_databases(
    mongo_api_collections: Any, mongo_app: Any
) -> None:
    raw_names = mongo_app["raw"].list_collection_names()
    meta_names = mongo_app["meta"].list_collection_names()
    assert PAYLOADS_COLLECTION in raw_names
    assert DRIFT_COLLECTION in meta_names
    assert mongo_api_collections.payloads.name.startswith(mongo_api_collections.prefix)
    assert mongo_api_collections.drift.name.startswith(mongo_api_collections.prefix)
    assert [
        name
        for name in mongo_app.list_database_names()
        if name.startswith(mongo_api_collections.prefix)
    ] == []

"""Pruebas unitarias del orquestador de la vía API y de su CLI.

Sin red, MongoDB ni PostgreSQL: el transporte, las colecciones, el registro y el
repositorio son dobles. La tabla vacía se simula con un conteo cero para comprobar
que ``verify-keys`` falla antes de comparar 0 = 0.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from nexora.common.api_contracts import SHIPMENTS_CONTRACT, SOURCE_SYSTEM
from nexora.common.config import Settings
from nexora.ingestion.api.cli import main
from nexora.ingestion.api.client import ApiHttpError, ApiPage, PageEnvelope
from nexora.ingestion.api.drift import ApiSchemaDriftError
from nexora.ingestion.api.landing import DRIFT_KEY, UNIQUE_KEY
from nexora.ingestion.api.registry import DATASETS, FRESHNESS_SLO_MINUTES
from nexora.ingestion.api.runner import (
    DATASET_ENDPOINTS,
    EmptySourceTableError,
    KeyMismatchError,
    collect_pages,
    dataset_slos,
    extract_dataset,
    freshness_rows,
    run_extraction,
    verify_keys,
)

RUN = "run-runner"
TIMESTAMP = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
ENDPOINT = SHIPMENTS_CONTRACT.endpoint


def _valid_item(number: int = 1) -> dict[str, Any]:
    """Elemento conforme al contrato de ``shipments``."""
    return {
        "shipment_id": f"s-{number}",
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


def _page(items: list[dict[str, Any]], *, number: int = 1, total: int = 1) -> ApiPage:
    body: dict[str, Any] = {
        "data": items,
        "page": number,
        "page_size": 10,
        "total_pages": total,
        "next_page": number + 1 if number < total else None,
    }
    return ApiPage(
        endpoint=ENDPOINT,
        page=number,
        payload=body,
        envelope=PageEnvelope.model_validate(body),
        content=json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )


class MemoryCollection:
    """Colección append-only en memoria, con búsqueda y agregación mínimas."""

    def __init__(self, key: tuple[str, ...]) -> None:
        self.documents: list[dict[str, Any]] = []
        self.key = key

    def find_one(self, filter: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return next(
            (doc for doc in self.documents if all(doc.get(k) == v for k, v in filter.items())),
            None,
        )

    def insert_one(self, document: Mapping[str, Any]) -> Any:
        values = tuple(document.get(name) for name in self.key)
        if any(tuple(doc.get(name) for name in self.key) == values for doc in self.documents):
            raise DuplicateKeyError("índice único")
        stored = dict(document)
        stored["_id"] = len(self.documents) + 1
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def find(self, filter: Mapping[str, Any], projection: Mapping[str, Any]) -> Iterator[Mapping]:
        return iter(
            doc
            for doc in self.documents
            if all(doc.get(name) == value for name, value in filter.items())
        )

    def aggregate(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        endpoint = pipeline[0]["$match"]["endpoint"]
        stamps = [doc["ingested_at"] for doc in self.documents if doc.get("endpoint") == endpoint]
        return [{"latest": max(stamps)}] if stamps else []


class FakeRegistry:
    """Doble de ``RunRegistry`` con el mismo enganche de bloqueo."""

    def __init__(self) -> None:
        self.pages = 0
        self.documents = 0
        self.events: list[str] = []
        self.rows: list[tuple[str, str, str, dict[str, Any]]] = []

    def ensure_tables(self) -> None:
        self.events.append("tables")

    def upsert_datasets(self, *, updated_at: datetime | None = None) -> int:
        self.events.append("datasets")
        return len(DATASETS)

    def record_run(self, run_id: str, dataset_name: str, **fields: Any) -> None:
        self.events.append("ok" if fields.get("status") == "ok" else "failed")
        self.rows.append((run_id, dataset_name, str(fields.get("status")), fields))

    def mark_blocked(self, *, run_id: str, dataset_name: str, drift_count: int) -> None:
        self.events.append("blocked")
        self.rows.append((run_id, dataset_name, "blocked", {"drift_count": drift_count}))


class FakeClient:
    """Cliente con páginas prefijadas, que falla si se le pide otra."""

    def __init__(self, pages: list[ApiPage], *, error: Exception | None = None) -> None:
        self.pages = pages
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def fetch_page(self, endpoint: str, page: int = 1) -> ApiPage:
        self.calls.append((endpoint, page))
        if self.error is not None:
            raise self.error
        return self.pages[page - 1]


class FakeRepository:
    """Repositorio que devuelve el conteo que se le indique."""

    def __init__(self, count: int) -> None:
        self.count_value = count
        self.calls = 0

    def count(self, contract: Any) -> int:
        self.calls += 1
        return self.count_value


class FakeConnection:
    """Conexión mínima: registra sentencias y simula ``meta.datasets`` ausente."""

    def __init__(self, *, has_datasets: bool = False) -> None:
        self.statements: list[tuple[Any, Any]] = []
        self.has_datasets = has_datasets

    def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append((statement, params))
        return SimpleNamespace(fetchall=lambda: [])

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        connection = self

        class Cursor:
            def execute(self, statement: str, params: Any = None) -> None:
                connection.statements.append(statement)

            def fetchone(self) -> tuple[Any, ...] | None:
                return ("meta.datasets",) if connection.has_datasets else (None,)

            def fetchall(self) -> list[tuple[Any, ...]]:
                return []

        yield Cursor()


def _collections() -> tuple[MemoryCollection, MemoryCollection]:
    return MemoryCollection(UNIQUE_KEY), MemoryCollection(DRIFT_KEY)


def test_cli_help_lists_the_three_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    for name in ("extract", "verify-keys", "freshness"):
        assert name in output


def test_extract_dataset_lands_pages_and_records_ok() -> None:
    payloads, drift_rows = _collections()
    registry = FakeRegistry()
    pages = (_page([_valid_item(1)], number=1, total=2), _page([_valid_item(2)], number=2, total=2))

    result = extract_dataset(
        "tms_shipments",
        run_id=RUN,
        pages=pages,
        payloads=payloads,
        drift_rows=drift_rows,
        registry=registry,
        clock=lambda: TIMESTAMP,
    )

    assert (result.pages, result.documents, result.drift_count, result.status) == (2, 2, 0, "ok")
    assert len(payloads.documents) == 2
    assert {doc["page"] for doc in payloads.documents} == {1, 2}
    assert all(doc["source_system"] == SOURCE_SYSTEM for doc in payloads.documents)
    assert drift_rows.documents == []
    assert registry.events == ["tables", "datasets", "ok"]
    assert registry.rows[0][:3] == (RUN, "tms_shipments", "ok")


def test_extract_dataset_with_drift_blocks_after_landing() -> None:
    payloads, drift_rows = _collections()
    registry = FakeRegistry()
    item = _valid_item()
    item["ordered_weight_kg"] = "diez"

    with pytest.raises(ApiSchemaDriftError) as exc:
        extract_dataset(
            "tms_shipments",
            run_id=RUN,
            pages=[_page([item])],
            payloads=payloads,
            drift_rows=drift_rows,
            registry=registry,
            clock=lambda: TIMESTAMP,
        )

    assert exc.value.drift_count == 1
    assert len(payloads.documents) == 1
    assert len(drift_rows.documents) == 1
    assert drift_rows.documents[0]["rule_id"] == "DQ-008"
    assert registry.events == ["tables", "datasets", "blocked"]
    assert registry.rows[0][:3] == (RUN, "tms_shipments", "blocked")


def test_collect_pages_traverses_the_tms_client() -> None:
    pages = [_page([_valid_item(1)], number=1, total=2), _page([_valid_item(2)], number=2, total=2)]
    client = FakeClient(pages)

    collected = collect_pages("tms_shipments", client=client, config=Settings(api_token=None))

    assert [page.page for page in collected] == [1, 2]
    assert client.calls == [(ENDPOINT, 1), (ENDPOINT, 2)]


def test_collect_pages_for_external_source_is_deterministic_and_offline(tmp_path: Any) -> None:
    config = Settings(api_cache_dir=str(tmp_path), api_live=0)

    first = collect_pages("weather_api", config=config, on_date="2026-11-18")
    second = collect_pages("weather_api", config=config, on_date="2026-11-18")

    assert len(first) == 18
    assert [page.content for page in first] == [page.content for page in second]
    assert first[0].endpoint == "/v1/weather"


def test_extract_external_source_lands_one_document_per_city(tmp_path: Any) -> None:
    payloads, drift_rows = _collections()
    registry = FakeRegistry()
    pages = collect_pages(
        "geocoding_api",
        config=Settings(api_cache_dir=str(tmp_path), api_live=0),
        on_date="2026-11-18",
    )

    result = extract_dataset(
        "geocoding_api",
        run_id=RUN,
        pages=pages,
        payloads=payloads,
        drift_rows=drift_rows,
        registry=registry,
        clock=lambda: TIMESTAMP,
    )

    assert (result.pages, result.documents, result.status) == (18, 18, "ok")
    assert result.source_system == "geocoding"
    assert {doc["endpoint"] for doc in payloads.documents} == {"/v1/geocoding"}


def test_run_extraction_records_a_failed_run_on_http_error() -> None:
    payloads, drift_rows = _collections()
    connection = FakeConnection()
    error = ApiHttpError(500, ENDPOINT, 1)

    with pytest.raises(ApiHttpError):
        run_extraction(
            "tms_shipments",
            run_id=RUN,
            connection=connection,
            payloads=payloads,
            drift_rows=drift_rows,
            client=FakeClient([], error=error),
            clock=lambda: TIMESTAMP,
        )

    assert payloads.documents == []
    statements = [entry[0] for entry in connection.statements]
    params = [entry[1] for entry in connection.statements if isinstance(entry[1], tuple)]
    assert any("insert into" in str(statement).lower() for statement in statements)
    assert any(row[5] == "failed" and row[9] == "ApiHttpError" for row in params)


def test_verify_keys_fails_on_empty_source_table_before_comparing() -> None:
    payloads, _ = _collections()
    repository = FakeRepository(0)

    with pytest.raises(EmptySourceTableError) as exc:
        verify_keys(
            "tms_shipments",
            run_id=RUN,
            repository=repository,
            payloads=payloads,
        )

    assert "count(*) = 0" in str(exc.value)
    assert "tms.shipments" in str(exc.value)
    assert repository.calls == 1


def test_verify_keys_compares_counts_only_for_non_empty_tables() -> None:
    payloads, _ = _collections()
    payloads.documents.append(
        {
            "run_id": RUN,
            "endpoint": ENDPOINT,
            "payload": {"data": [_valid_item(1), _valid_item(2)]},
        }
    )

    counts = verify_keys(
        "tms_shipments",
        run_id=RUN,
        repository=FakeRepository(2),
        payloads=payloads,
    )
    assert str(counts) == "tms_shipments (tms.shipments.shipment_id): esperado = aterrizado = 2"

    with pytest.raises(KeyMismatchError):
        verify_keys(
            "tms_shipments",
            run_id=RUN,
            repository=FakeRepository(3),
            payloads=payloads,
        )


def test_cli_verify_keys_reports_empty_table_with_exit_code_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payloads, _ = _collections()

    @contextmanager
    def fake_mongo(config: Any = None, dsn: str | None = None) -> Iterator[Any]:
        yield (payloads, payloads)

    monkeypatch.setattr("nexora.ingestion.api.cli.mongo_collections", fake_mongo)
    monkeypatch.setattr(
        "nexora.ingestion.api.cli.PostgresRepository", lambda *args, **kwargs: FakeRepository(0)
    )

    code = main(["verify-keys", "--dataset", "tms_shipments", "--run-id", RUN])

    assert code == 1
    error = capsys.readouterr().err
    assert "count(*) = 0" in error
    assert "no se comparan claves" in error


def test_freshness_prints_one_line_per_dataset_with_minutes_and_slo() -> None:
    payloads = MemoryCollection(UNIQUE_KEY)
    slo = FRESHNESS_SLO_MINUTES
    for definition in DATASETS:
        payloads.documents.append(
            {
                "endpoint": DATASET_ENDPOINTS[definition.dataset_name],
                "ingested_at": TIMESTAMP - timedelta(minutes=12),
            }
        )
    slos = {definition.dataset_name: slo for definition in DATASETS}

    rows = freshness_rows(
        slos=slos,
        payloads=payloads,
        clock=lambda: TIMESTAMP,
    )

    assert len(rows) == 6
    for row in rows:
        line = str(row)
        assert f"{row.minutes:.1f} min" in line
        assert f"SLO {slo} min" in line
        assert row.minutes == pytest.approx(12.0)


def test_freshness_handles_datasets_without_payloads() -> None:
    rows = freshness_rows(
        slos={"tms_shipments": FRESHNESS_SLO_MINUTES},
        payloads=MemoryCollection(UNIQUE_KEY),
        clock=lambda: TIMESTAMP,
    )

    assert len(rows) == 1
    assert str(rows[0]) == "tms_shipments: sin datos (SLO 120 min)"


def test_dataset_slos_uses_the_canonical_seed_when_table_is_absent() -> None:
    slos = dataset_slos(FakeConnection())

    assert len(slos) == 6
    assert set(slos.values()) == {FRESHNESS_SLO_MINUTES}

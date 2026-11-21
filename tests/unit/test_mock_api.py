"""Pruebas unitarias del mock TMS sin red ni base de datos.

La aplicación ASGI se llama en proceso: no hace falta un cliente HTTP ni
levantar un servidor. El repositorio en memoria sostiene los tres recursos.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

import pytest

from nexora.common.api_contracts import PAGINATION_KEYS, RESOURCE_CONTRACTS, Field, ResourceContract
from nexora.generators.api.app import create_app
from nexora.generators.api.drift import (
    ADDED_FIELD,
    CHANGE_TYPES,
    DriftPlan,
)
from nexora.generators.api.faults import (
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_TOO_MANY_REQUESTS,
    FaultPlan,
)
from nexora.generators.api.repository import InMemoryRepository

TOKEN = "token-de-prueba"
ROWS_PER_RESOURCE = 7
NO_FAULTS = 0


def _sample(field: Field, position: int) -> Any:
    """Valor de prueba de un campo, con su tipo nativo y nulos ocasionales."""
    if field.nullable and position % 5 == 0:
        return None
    if field.json_type == "integer":
        return position
    if field.json_type == "number":
        return position + 0.5
    if field.json_type == "boolean":
        return position % 2 == 0
    if field.json_type == "date":
        return date(2026, 11, 1)
    if field.json_type == "timestamp":
        return datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    return f"{field.name}-{position}"


def _rows(contract: ResourceContract, count: int) -> list[dict[str, object]]:
    """Filas de prueba; la clave natural se repite cada tres posiciones."""
    rows: list[dict[str, object]] = []
    for position in range(count):
        row = {field.name: _sample(field, position) for field in contract.fields}
        if position >= 3:
            row[contract.natural_key] = rows[position - 3][contract.natural_key]
        rows.append(row)
    return rows


class _AsgiClient:
    """Cliente mínimo que llama a la app ASGI en proceso, sin dependencias nuevas."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def get(self, path: str, *, token: str | None = TOKEN) -> tuple[int, dict[str, str], Any]:
        target, _, query = path.partition("?")
        headers: list[tuple[bytes, bytes]] = []
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        return asyncio.run(self._request(target, query, headers))

    async def _request(
        self, path: str, query: str, headers: list[tuple[bytes, bytes]]
    ) -> tuple[int, dict[str, str], Any]:
        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
        }
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await self.app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_headers = {
            name.decode().lower(): value.decode() for name, value in start["headers"]
        }
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return start["status"], response_headers, json.loads(body)


@pytest.fixture
def repository() -> InMemoryRepository:
    repo = InMemoryRepository()
    for contract in RESOURCE_CONTRACTS.values():
        repo.add(contract.resource, _rows(contract, ROWS_PER_RESOURCE))
    return repo


def _client(
    repository: InMemoryRepository,
    *,
    faults: FaultPlan | None = None,
    drift: DriftPlan | None = None,
) -> _AsgiClient:
    """Cliente en proceso con los planes inyectados; sin fallos ni drift por defecto."""
    return _AsgiClient(
        create_app(
            repository,
            TOKEN,
            fault_plan=faults if faults is not None else FaultPlan(max_failures=NO_FAULTS),
            drift_plan=drift if drift is not None else DriftPlan(enabled=False),
        )
    )


@pytest.fixture
def client(repository: InMemoryRepository) -> _AsgiClient:
    return _client(repository)


def test_health_does_not_require_token(repository: InMemoryRepository) -> None:
    status, _, body = _client(repository).get("/health", token=None)
    assert status == 200
    assert body == {"status": "ok"}


def test_resource_requires_token(client: _AsgiClient) -> None:
    status, headers, body = client.get("/v1/shipments", token=None)
    assert status == 401
    assert headers["www-authenticate"] == "Bearer"
    assert body == {"detail": "Not authenticated"}


def test_wrong_token_is_rejected(client: _AsgiClient) -> None:
    assert client.get("/v1/shipments", token="otro-token")[0] == 401


def test_app_requires_a_configured_token(repository: InMemoryRepository) -> None:
    with pytest.raises(ValueError):
        create_app(repository, "")


def test_unknown_resource_is_not_found(client: _AsgiClient) -> None:
    assert client.get("/v1/desconocido")[0] == 404


def test_page_size_bounds(client: _AsgiClient) -> None:
    assert client.get("/v1/shipments?page_size=1001")[0] == 422
    assert client.get("/v1/shipments?page=0")[0] == 422


@pytest.mark.parametrize("resource", sorted(RESOURCE_CONTRACTS))
def test_pagination_envelope(client: _AsgiClient, resource: str) -> None:
    status, _, first = client.get(f"/v1/{resource}?page=1&page_size=3")
    assert status == 200
    assert set(first) == {"data", "page", "page_size", "total_pages", "next_page"}
    assert first["page"] == 1
    assert first["page_size"] == 3
    assert first["total_pages"] == 3
    assert len(first["data"]) == 3
    assert first["next_page"] == 2

    status, _, last = client.get(f"/v1/{resource}?page=3&page_size=3")
    assert status == 200
    assert len(last["data"]) == 1
    assert last["next_page"] is None


@pytest.mark.parametrize("resource", sorted(RESOURCE_CONTRACTS))
def test_full_traversal_counts_natural_keys(
    repository: InMemoryRepository, client: _AsgiClient, resource: str
) -> None:
    contract = RESOURCE_CONTRACTS[resource]
    expected = Counter(str(row[contract.natural_key]) for row in repository.page(contract, 1, 1000))
    collected: Counter[str] = Counter()
    page: int | None = 1
    pages_seen = 0
    while page is not None:
        status, _, envelope = client.get(f"/v1/{resource}?page={page}&page_size=2")
        assert status == 200
        assert envelope["page"] == page
        assert envelope["page_size"] == 2
        collected.update(str(item[contract.natural_key]) for item in envelope["data"])
        page = envelope["next_page"]
        pages_seen += 1
        assert pages_seen <= 10
    assert collected == expected
    assert sum(collected.values()) == ROWS_PER_RESOURCE


def test_json_types_follow_the_contract(client: _AsgiClient) -> None:
    status, _, body = client.get("/v1/shipments?page=1&page_size=1")
    assert status == 200
    item = body["data"][0]
    assert set(item) == set(RESOURCE_CONTRACTS["shipments"].field_names)
    assert isinstance(item["shipment_id"], str)
    assert isinstance(item["ordered_qty"], int)
    assert isinstance(item["ordered_weight_kg"], float)
    assert item["pickup_date"] == "2026-11-01"
    assert item["pickup_ts"] == "2026-11-01T12:00:00Z"
    assert item["definitive_failure_flag"] is True
    assert item["customer_id"] is None


def _digest(endpoint: str, page: int) -> int:
    """Reimplementación independiente de la fórmula documentada para las pruebas."""
    payload = f"{endpoint}{page}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


def test_fault_pages_are_deterministic() -> None:
    endpoint = RESOURCE_CONTRACTS["shipments"].endpoint
    pages = range(1, 801)

    first = FaultPlan().faulting_pages(endpoint, pages)
    second = FaultPlan().faulting_pages(endpoint, pages)

    assert first == second
    assert any(item.status == HTTP_INTERNAL_SERVER_ERROR for item in first.values())
    assert any(item.status == HTTP_TOO_MANY_REQUESTS for item in first.values())


def test_faulting_page_retries_then_serves_content() -> None:
    repository = InMemoryRepository()
    repo_rows = _rows(RESOURCE_CONTRACTS["shipments"], 600)
    repository.add("shipments", repo_rows)
    plan = FaultPlan(max_failures=2)
    endpoint = RESOURCE_CONTRACTS["shipments"].endpoint
    found = plan.faulting_pages(endpoint, range(1, 601))
    five_hundred = next(page for page, item in found.items() if item.status == 500)
    too_many = next(page for page, item in found.items() if item.status == 429)

    for page in (five_hundred, too_many):
        expected = found[page]
        client = _client(repository, faults=plan)
        path = f"/v1/shipments?page={page}&page_size=1"

        status, headers, _ = client.get(path)
        assert status == expected.status
        assert headers["retry-after"] == str(expected.retry_after_s)
        assert 1 <= expected.retry_after_s <= 5

        assert client.get(path)[0] == expected.status

        status, _, body = client.get(path)
        assert status == 200
        assert set(body) == set(PAGINATION_KEYS)
        assert body["page"] == page
        assert len(body["data"]) == 1
        natural = RESOURCE_CONTRACTS["shipments"].natural_key
        assert set(body["data"][0]) == set(RESOURCE_CONTRACTS["shipments"].field_names)
        assert body["data"][0][natural] == str(repo_rows[page - 1][natural])


@pytest.mark.parametrize("resource", sorted(RESOURCE_CONTRACTS))
def test_page_one_always_drifted_with_flag(repository: InMemoryRepository, resource: str) -> None:
    contract = RESOURCE_CONTRACTS[resource]
    baseline = _client(repository)
    drifted = _client(repository, drift=DriftPlan(enabled=True))
    plan = DriftPlan(enabled=True)
    path = f"/v1/{resource}?page=1&page_size={ROWS_PER_RESOURCE}"

    _, _, original = baseline.get(path)
    status, _, altered = drifted.get(path)
    assert status == 200
    assert len(original["data"]) == len(altered["data"]) == ROWS_PER_RESOURCE

    for before, after in zip(original["data"], altered["data"], strict=True):
        assert after[contract.natural_key] == before[contract.natural_key]
        differences = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
        assert len(differences) == 1
        field = differences.pop()
        assert field != contract.natural_key
        change = plan.change_for(contract, 1, str(before[contract.natural_key]))
        assert change.field == field
        assert change.change_type in CHANGE_TYPES


@pytest.mark.parametrize("resource", sorted(RESOURCE_CONTRACTS))
def test_drift_page_set_matches_hash_and_is_repeatable(resource: str) -> None:
    endpoint = RESOURCE_CONTRACTS[resource].endpoint
    pages = range(1, 801)
    plan = DriftPlan(enabled=True)

    with_flag = {page for page in pages if plan.is_drifted(endpoint, page)}
    again = {page for page in pages if DriftPlan(enabled=True).is_drifted(endpoint, page)}

    assert 1 in with_flag
    assert with_flag == again
    expected = {1} | {page for page in pages if page != 1 and _digest(endpoint, page) % 100 < 1}
    assert with_flag == expected

    without_flag = {page for page in pages if DriftPlan(enabled=False).is_drifted(endpoint, page)}
    assert without_flag == set()


def test_added_change_uses_a_field_outside_the_contract(
    repository: InMemoryRepository,
) -> None:
    contract = RESOURCE_CONTRACTS["shipments"]
    assert ADDED_FIELD not in contract.field_names
    drifted = _client(repository, drift=DriftPlan(enabled=True))
    _, _, body = drifted.get(f"/v1/shipments?page=1&page_size={ROWS_PER_RESOURCE}")

    for item in body["data"]:
        if ADDED_FIELD in item:
            assert set(contract.field_names) <= set(item)


def test_drift_flag_is_read_at_factory_time(
    repository: InMemoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"/v1/shipments?page=1&page_size={ROWS_PER_RESOURCE}"

    monkeypatch.setenv("NEXORA_API_DRIFT", "0")
    off = _AsgiClient(create_app(repository, TOKEN, fault_plan=FaultPlan(max_failures=NO_FAULTS)))
    _, _, plain = off.get(path)

    monkeypatch.setenv("NEXORA_API_DRIFT", "1")
    on = _AsgiClient(create_app(repository, TOKEN, fault_plan=FaultPlan(max_failures=NO_FAULTS)))
    _, _, altered = on.get(path)

    assert plain["data"] != altered["data"]

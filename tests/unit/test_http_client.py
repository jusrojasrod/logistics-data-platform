"""Pruebas unitarias del cliente HTTP sin red, sin MongoDB y sin esperas reales.

El transporte, el reloj y la espera se sustituyen por dobles: el reloj avanza
sólo cuando el cliente duerme, así que el backoff y el límite de tasa son
observables sin depender del tiempo de la máquina.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from nexora.common.config import Settings
from nexora.ingestion.api.client import (
    ApiClient,
    ApiHttpError,
    ApiResponseError,
    ApiTransportError,
    HttpResponse,
    backoff_delay,
    iter_pages,
    retry_after_seconds,
)

TOKEN = "token-de-prueba"
BASE_URL = "http://mock.test"
PAGE_SIZE = 3
ENDPOINT = "/v1/shipments"


def _payload(
    *,
    page: int = 1,
    next_page: int | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Sobre de paginación válido para el transporte falso."""
    return {
        "data": items if items is not None else [{"shipment_id": f"s-{page}"}],
        "page": page,
        "page_size": PAGE_SIZE,
        "total_pages": 2 if next_page is not None else 1,
        "next_page": next_page,
    }


def _response(
    status: int,
    payload: dict[str, Any] | None = None,
    *,
    retry_after: int | None = None,
    content: bytes | None = None,
) -> HttpResponse:
    """Construye una respuesta del transporte falso."""
    body = payload if payload is not None else {}
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    raw = content if content is not None else json.dumps(body).encode()
    return HttpResponse(status_code=status, headers=headers, content=raw, payload=body)


class FakeClock:
    """Reloj que avanza únicamente cuando se duerme."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeTransport:
    """Transporte en memoria: devuelve lo que diga una función o lanza una excepción."""

    def __init__(self, handler: Callable[[Mapping[str, Any]], HttpResponse | Exception]) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        result = self._handler(dict(params))
        if isinstance(result, Exception):
            raise result
        return result


def _client(
    transport: FakeTransport,
    clock: FakeClock,
    **overrides: Any,
) -> ApiClient:
    options: dict[str, Any] = {
        "base_url": BASE_URL,
        "token": TOKEN,
        "transport": transport,
        "page_size": PAGE_SIZE,
        "rate_limit": 5,
        "clock": clock.time,
        "sleep": clock.sleep,
    }
    options.update(overrides)
    return ApiClient(**options)


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_delay(attempt) for attempt in range(1, 5)] == [1.0, 2.0, 4.0, 8.0]
    assert backoff_delay(10) == 30.0
    with pytest.raises(ValueError):
        backoff_delay(0)


def test_persistent_500_stops_after_five_attempts_without_landing() -> None:
    clock = FakeClock()
    transport = FakeTransport(lambda params: _response(500, {}))
    client = _client(transport, clock)
    landed: list[Any] = []

    with pytest.raises(ApiHttpError) as raised:
        for page in iter_pages(client, ENDPOINT):
            landed.append(page)

    assert raised.value.status_code == 500
    assert len(transport.calls) == 5
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0]
    assert landed == []


@pytest.mark.parametrize(
    ("header", "expected"),
    [(3, 3.0), (120, 60.0)],
)
def test_retry_after_is_respected_and_capped(header: int, expected: float) -> None:
    clock = FakeClock()
    transport = FakeTransport(lambda params: _response(429, {}, retry_after=header))
    client = _client(transport, clock)

    with pytest.raises(ApiHttpError) as raised:
        client.fetch_page(ENDPOINT)

    assert raised.value.status_code == 429
    assert raised.value.retry_after_s == expected
    assert len(transport.calls) == 5
    assert clock.sleeps == [expected] * 4


def test_retry_after_header_parsing() -> None:
    assert retry_after_seconds({"retry-after": " 7 "}) == 7.0
    assert retry_after_seconds({"Retry-After": "120"}) == 60.0
    assert retry_after_seconds({}) is None
    assert retry_after_seconds({"Retry-After": "no-numero"}) is None
    assert retry_after_seconds({"Retry-After": "-1"}) is None


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_client_errors_are_not_retried(status: int) -> None:
    clock = FakeClock()
    transport = FakeTransport(lambda params: _response(status, {}))
    client = _client(transport, clock)

    with pytest.raises(ApiHttpError) as raised:
        client.fetch_page(ENDPOINT)

    assert raised.value.status_code == status
    assert len(transport.calls) == 1
    assert clock.sleeps == []


def test_token_bucket_limits_requests_to_the_configured_rate() -> None:
    clock = FakeClock()
    transport = FakeTransport(lambda params: _response(200, _payload()))
    client = _client(transport, clock, rate_limit=5)

    for _ in range(8):
        client.fetch_page(ENDPOINT)

    assert len(transport.calls) == 8
    assert clock.sleeps == pytest.approx([0.2, 0.2, 0.2])


def test_pagination_traversal_preserves_raw_bytes() -> None:
    pages = {
        1: _payload(page=1, next_page=2, items=[{"shipment_id": "s-1"}]),
        2: _payload(page=2, items=[{"shipment_id": "s-2"}]),
    }
    clock = FakeClock()
    transport = FakeTransport(
        lambda params: _response(
            200, pages[params["page"]], content=json.dumps(pages[params["page"]]).encode()
        )
    )
    client = _client(transport, clock)

    result = list(iter_pages(client, ENDPOINT))

    assert [page.page for page in result] == [1, 2]
    assert [page.data for page in result] == [pages[1]["data"], pages[2]["data"]]
    assert result[0].content == json.dumps(pages[1]).encode()
    assert result[1].content == json.dumps(pages[2]).encode()
    assert transport.calls[0]["params"] == {"page": 1, "page_size": PAGE_SIZE}
    assert transport.calls[1]["params"] == {"page": 2, "page_size": PAGE_SIZE}
    assert clock.sleeps == []


def test_drift_in_items_is_not_a_validation_error() -> None:
    items: list[dict[str, Any]] = [
        {"shipment_id": "s-1"},
        {"shipment_id": "s-2", "carrier_id": "c-1", "campo_nuevo": "drift"},
        {"shipment_id": "s-3", "carrier_id": 42},
    ]
    clock = FakeClock()
    transport = FakeTransport(lambda params: _response(200, _payload(items=items)))
    client = _client(transport, clock)

    page = client.fetch_page(ENDPOINT)

    assert page.data == items
    assert page.payload["data"] == items


@pytest.mark.parametrize(
    "payload",
    [
        {"page": 1, "page_size": PAGE_SIZE, "total_pages": 1},
        {**_payload(), "page": 0},
        {**_payload(), "extra": 1},
        {**_payload(), "data": "no-es-lista"},
    ],
)
def test_invalid_envelope_raises_response_error(payload: dict[str, Any]) -> None:
    clock = FakeClock()
    transport = FakeTransport(lambda params: _response(200, payload))
    client = _client(transport, clock)

    with pytest.raises(ApiResponseError):
        client.fetch_page(ENDPOINT)

    assert len(transport.calls) == 1


def test_authorization_header_is_sent_and_token_is_not_exposed() -> None:
    clock = FakeClock()
    transport = FakeTransport(lambda params: _response(200, _payload()))
    client = _client(transport, clock)

    client.fetch_page(ENDPOINT)

    assert transport.calls[0]["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert transport.calls[0]["url"] == f"{BASE_URL}{ENDPOINT}"
    assert TOKEN not in repr(client)


def test_transport_failures_are_retried_and_then_raised() -> None:
    clock = FakeClock()
    transport = FakeTransport(lambda params: ApiTransportError("sin conexión"))
    client = _client(transport, clock)

    with pytest.raises(ApiTransportError):
        client.fetch_page(ENDPOINT)

    assert len(transport.calls) == 5
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0]


def test_client_requires_a_token() -> None:
    clock = FakeClock()
    transport = FakeTransport(lambda params: _response(200, _payload()))

    with pytest.raises(ValueError):
        _client(transport, clock, token=None, config=Settings(api_token=None))

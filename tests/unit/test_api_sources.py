"""Pruebas unitarias de las fuentes externas y de su caché en disco.

El modo live usa un transporte falso y el reloj de la caché se controla desde la
prueba: ningún caso abre una conexión de red ni espera tiempo real.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests

from nexora.common.config import Settings
from nexora.ingestion.api.cache import DiskCache, cache_key
from nexora.ingestion.api.sources import (
    ENV_VAR_BY_SOURCE,
    SOURCE_EXCHANGE_RATE,
    SOURCE_GEOCODING,
    SOURCE_WEATHER,
    ExternalSourceError,
    JsonTransport,
    LiveSourceConfigError,
    LiveSourceResponseError,
    RequestsJsonTransport,
    digest_int,
    fetch_exchange_rate,
    fetch_geocoding,
    fetch_weather,
    simulated_exchange_rate,
)

BASE = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
CITY = "Bogotá"
DAY = "2026-11-18"
WEATHER_URL = "http://mock.test/weather"
GEOCODING_URL = "http://mock.test/geocoding"
EXCHANGE_URL = "http://mock.test/exchange-rate"


class FakeClock:
    """Reloj controlado por la prueba."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class FakeJsonTransport:
    """Transporte en memoria que devuelve siempre el mismo objeto."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def get_json(self, url: str, *, params: Mapping[str, Any], timeout: float) -> Any:
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        return self.payload


class ExplodingTransport:
    """Transporte que registra intentos y falla si alguien lo usa."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_json(self, url: str, *, params: Mapping[str, Any], timeout: float) -> Any:
        self.calls.append({"url": url})
        raise requests.ConnectionError("sin red")


class FailingSession:
    """Sesión falsa de requests, sin conexión, para el transporte de producción."""

    def get(self, url: str, *, params: Mapping[str, Any], timeout: float) -> Any:
        raise requests.ConnectionError("sin red")


def _config(
    *,
    live: bool = False,
    weather_url: str | None = None,
    geocoding_url: str | None = None,
    exchange_rate_url: str | None = None,
) -> Settings:
    """Configuración aislada del entorno."""
    return Settings(
        _env_file=None,
        api_live=int(live),
        weather_url=weather_url,
        geocoding_url=geocoding_url,
        exchange_rate_url=exchange_rate_url,
    )


def _live_config() -> Settings:
    """Configuración con las tres URL del modo live resueltas."""
    return _config(
        live=True,
        weather_url=WEATHER_URL,
        geocoding_url=GEOCODING_URL,
        exchange_rate_url=EXCHANGE_URL,
    )


def _cache(directory: Path, clock: FakeClock) -> DiskCache:
    return DiskCache(directory / "cache", clock=clock)


def _entries(directory: Path) -> list[Path]:
    return sorted((directory / "cache").glob("*.json"))


def _call_weather(cache: DiskCache, config: Settings, transport: JsonTransport) -> dict[str, Any]:
    return fetch_weather(CITY, DAY, cache=cache, config=config, transport=transport)


def _call_geocoding(cache: DiskCache, config: Settings, transport: JsonTransport) -> dict[str, Any]:
    return fetch_geocoding(CITY, cache=cache, config=config, transport=transport)


def _call_exchange_rate(
    cache: DiskCache, config: Settings, transport: JsonTransport
) -> dict[str, Any]:
    return fetch_exchange_rate(DAY, cache=cache, config=config, transport=transport)


def test_simulated_sources_are_deterministic_and_cached(tmp_path: Path) -> None:
    clock = FakeClock(BASE)
    cache = _cache(tmp_path, clock)
    config = _config()
    transport = ExplodingTransport()

    weather = fetch_weather(CITY, DAY, cache=cache, config=config, transport=transport)
    geocoding = fetch_geocoding(CITY, cache=cache, config=config, transport=transport)
    rate = fetch_exchange_rate(DAY, cache=cache, config=config, transport=transport)

    other_cache = _cache(tmp_path, clock)
    assert fetch_weather(CITY, DAY, cache=other_cache, config=config) == weather
    assert fetch_geocoding(CITY, cache=other_cache, config=config) == geocoding
    assert fetch_exchange_rate(DAY, cache=other_cache, config=config) == rate

    assert weather["source"] == SOURCE_WEATHER
    assert (weather["city"], weather["date"]) == (CITY, DAY)
    assert weather["condition"] in ("despejado", "nublado", "lluvia", "tormenta")
    assert geocoding["source"] == SOURCE_GEOCODING
    assert geocoding["region"] == "andina"
    assert abs(geocoding["latitude"] - 4.711) < 0.02
    assert rate["source"] == SOURCE_EXCHANGE_RATE
    assert (rate["base"], rate["quote"]) == ("USD", "COP")
    assert 3500 <= rate["rate"] < 4600

    assert len(_entries(tmp_path)) == 3
    assert transport.calls == []


def test_hash_depends_on_source_city_and_date() -> None:
    assert digest_int(SOURCE_WEATHER, CITY, DAY) != digest_int(SOURCE_WEATHER, "Cali", DAY)
    assert digest_int(SOURCE_WEATHER, CITY, DAY) != digest_int(SOURCE_WEATHER, CITY, "2026-11-19")
    assert simulated_exchange_rate(DAY) != simulated_exchange_rate("2026-11-19")


def test_live_uses_url_and_second_access_uses_cache(tmp_path: Path) -> None:
    clock = FakeClock(BASE)
    cache = _cache(tmp_path, clock)
    payload = {"source": SOURCE_WEATHER, "city": CITY, "date": DAY, "temperature_c": 21.5}
    transport = FakeJsonTransport(payload)
    config = _config(live=True, weather_url=WEATHER_URL)

    first = fetch_weather(CITY, DAY, cache=cache, config=config, transport=transport)
    second = fetch_weather(CITY, DAY, cache=cache, config=config, transport=transport)

    assert first == second == payload
    assert len(transport.calls) == 1
    assert transport.calls[0]["url"] == WEATHER_URL
    assert transport.calls[0]["params"] == {"city": CITY, "date": DAY}
    assert len(_entries(tmp_path)) == 1


def test_cache_is_reused_by_a_new_instance_without_network(tmp_path: Path) -> None:
    clock = FakeClock(BASE)
    transport = FakeJsonTransport({"source": SOURCE_WEATHER, "valor": 7})
    config = _config(live=True, weather_url=WEATHER_URL)
    fetch_weather(CITY, DAY, cache=_cache(tmp_path, clock), config=config, transport=transport)

    exploding = ExplodingTransport()
    cached = fetch_weather(
        CITY,
        DAY,
        cache=_cache(tmp_path, clock),
        config=config,
        transport=exploding,
    )
    assert cached == {"source": SOURCE_WEATHER, "valor": 7}
    assert exploding.calls == []


def test_ttl_is_per_source(tmp_path: Path) -> None:
    clock = FakeClock(BASE)
    cache = _cache(tmp_path, clock)
    transport = FakeJsonTransport({"ok": True})
    config = _live_config()

    fetch_weather(CITY, DAY, cache=cache, config=config, transport=transport)
    clock.advance(minutes=59)
    fetch_weather(CITY, DAY, cache=cache, config=config, transport=transport)
    assert len(transport.calls) == 1
    clock.advance(minutes=2)
    fetch_weather(CITY, DAY, cache=cache, config=config, transport=transport)
    assert len(transport.calls) == 2

    fetch_exchange_rate(DAY, cache=cache, config=config, transport=transport)
    clock.advance(hours=23)
    fetch_exchange_rate(DAY, cache=cache, config=config, transport=transport)
    assert len(transport.calls) == 3
    clock.advance(hours=2)
    fetch_exchange_rate(DAY, cache=cache, config=config, transport=transport)
    assert len(transport.calls) == 4

    fetch_geocoding(CITY, cache=cache, config=config, transport=transport)
    clock.advance(days=365)
    fetch_geocoding(CITY, cache=cache, config=config, transport=transport)
    assert len(transport.calls) == 5


def test_without_flag_simulation_never_uses_transport(tmp_path: Path) -> None:
    exploding = ExplodingTransport()
    config = _config(weather_url=WEATHER_URL)
    weather = fetch_weather(
        CITY, DAY, cache=_cache(tmp_path, FakeClock(BASE)), config=config, transport=exploding
    )
    assert weather["source"] == SOURCE_WEATHER
    assert exploding.calls == []


@pytest.mark.parametrize(
    ("env_var", "call"),
    [
        (ENV_VAR_BY_SOURCE[SOURCE_WEATHER], _call_weather),
        (ENV_VAR_BY_SOURCE[SOURCE_GEOCODING], _call_geocoding),
        (ENV_VAR_BY_SOURCE[SOURCE_EXCHANGE_RATE], _call_exchange_rate),
    ],
)
def test_live_without_url_fails_before_network(
    env_var: str,
    call: Callable[[DiskCache, Settings, JsonTransport], dict[str, Any]],
    tmp_path: Path,
) -> None:
    exploding = ExplodingTransport()
    with pytest.raises(LiveSourceConfigError) as exc:
        call(_cache(tmp_path, FakeClock(BASE)), _config(live=True), exploding)
    assert env_var in str(exc.value)
    assert exploding.calls == []
    assert _entries(tmp_path) == []


def test_live_response_must_be_a_json_object(tmp_path: Path) -> None:
    transport = FakeJsonTransport(["no", "es", "un", "objeto"])
    config = _config(live=True, weather_url=WEATHER_URL)
    with pytest.raises(LiveSourceResponseError):
        fetch_weather(
            CITY, DAY, cache=_cache(tmp_path, FakeClock(BASE)), config=config, transport=transport
        )
    assert _entries(tmp_path) == []


def test_requests_transport_wraps_network_failures() -> None:
    transport = RequestsJsonTransport(FailingSession())  # type: ignore[arg-type]
    with pytest.raises(ExternalSourceError):
        transport.get_json(WEATHER_URL, params={"city": CITY}, timeout=1.0)


def test_cache_key_is_stable_scoped_and_safe() -> None:
    params = {"city": CITY, "date": DAY}
    assert cache_key(SOURCE_WEATHER, params) == cache_key(
        SOURCE_WEATHER, {"date": DAY, "city": CITY}
    )
    assert cache_key(SOURCE_WEATHER, params) != cache_key(SOURCE_GEOCODING, params)
    assert "/" not in cache_key(SOURCE_WEATHER, params)


def test_invalid_date_fails_before_writing_cache(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        fetch_weather(CITY, "18-11-2026", cache=_cache(tmp_path, FakeClock(BASE)))
    assert _entries(tmp_path) == []

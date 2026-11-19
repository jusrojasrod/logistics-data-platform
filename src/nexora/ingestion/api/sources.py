"""Fuentes externas simuladas con caché en disco y modo live opcional.

Por defecto el clima, la geocodificación y la tasa de cambio USD/COP se calculan
de forma determinista a partir de la fuente y de la ciudad o la fecha. Con
``NEXORA_API_LIVE=1`` se consulta la URL configurada para la fuente; si la URL
falta, se falla antes de abrir ninguna conexión. El resultado es el payload
crudo: la conversión de moneda y su persistencia quedan fuera de este módulo.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Protocol

import requests

from nexora.common.config import Settings, settings
from nexora.generators.config import CITY_CATALOG
from nexora.ingestion.api.cache import DiskCache, cache_key

SOURCE_WEATHER = "weather"
SOURCE_GEOCODING = "geocoding"
SOURCE_EXCHANGE_RATE = "exchange_rate"

ENV_VAR_BY_SOURCE: dict[str, str] = {
    SOURCE_WEATHER: "NEXORA_WEATHER_URL",
    SOURCE_GEOCODING: "NEXORA_GEOCODING_URL",
    SOURCE_EXCHANGE_RATE: "NEXORA_EXCHANGE_RATE_URL",
}

DATASET_BY_SOURCE: dict[str, str] = {
    SOURCE_WEATHER: "weather_api",
    SOURCE_GEOCODING: "geocoding_api",
    SOURCE_EXCHANGE_RATE: "exchange_rate_api",
}

CONFIG_ATTR_BY_SOURCE: dict[str, str] = {
    SOURCE_WEATHER: "weather_url",
    SOURCE_GEOCODING: "geocoding_url",
    SOURCE_EXCHANGE_RATE: "exchange_rate_url",
}

WEATHER_TTL_S = 60 * 60
GEOCODING_TTL_S: int | None = None
EXCHANGE_RATE_TTL_S = 24 * 60 * 60

_WEATHER_CONDITIONS = ("despejado", "nublado", "lluvia", "tormenta")


class ExternalSourceError(RuntimeError):
    """Base de los errores de las fuentes externas."""


class LiveSourceConfigError(ExternalSourceError):
    """El modo live está activo pero falta la URL de la fuente."""


class LiveSourceResponseError(ExternalSourceError):
    """La respuesta live no es un objeto JSON utilizable."""


class JsonTransport(Protocol):
    """Interfaz mínima del transporte inyectado en el modo live."""

    def get_json(self, url: str, *, params: Mapping[str, Any], timeout: float) -> Any: ...


class RequestsJsonTransport:
    """Transporte de producción basado en ``requests``."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session if session is not None else requests.Session()

    def get_json(self, url: str, *, params: Mapping[str, Any], timeout: float) -> Any:
        try:
            response = self._session.get(url, params=dict(params), timeout=timeout)
            response.raise_for_status()
            content = response.content
        except requests.RequestException as exc:
            raise ExternalSourceError(f"GET {url}: fallo de transporte de fuente externa") from exc
        try:
            return json.loads(content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LiveSourceResponseError(f"GET {url}: respuesta JSON inválida") from exc


def digest_int(source: str, *parts: str) -> int:
    """Entero estable derivado de la fuente y de sus componentes."""
    key = ":".join((source, *parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest(), "big")


def date_string(on_date: str | date) -> str:
    """Normaliza una fecha a ``YYYY-MM-DD`` o falla con un mensaje claro."""
    if isinstance(on_date, date):
        return on_date.isoformat()
    try:
        return date.fromisoformat(on_date).isoformat()
    except ValueError as exc:
        raise ValueError(f"fecha no válida: {on_date!r}; se espera YYYY-MM-DD") from exc


def simulated_weather(city: str, day: str) -> dict[str, Any]:
    """Clima sintético elegido por hash de fuente, ciudad y fecha."""
    number = digest_int(SOURCE_WEATHER, city, day)
    hour = number % 24
    return {
        "source": SOURCE_WEATHER,
        "city": city,
        "date": day,
        "observed_at": f"{day}T{hour:02d}:00:00Z",
        "temperature_c": round(-5 + (number % 4000) / 100, 1),
        "humidity_pct": 30 + (number // 4000) % 71,
        "wind_speed_ms": round((number // 400000) % 120 / 10, 1),
        "condition": _WEATHER_CONDITIONS[(number // 4000000) % len(_WEATHER_CONDITIONS)],
    }


def simulated_geocoding(city: str) -> dict[str, Any]:
    """Coordenadas sintéticas: centro de catálogo con desplazamiento por hash."""
    number = digest_int(SOURCE_GEOCODING, city)
    base = next((item for item in CITY_CATALOG if item.name.casefold() == city.casefold()), None)
    if base is None:
        latitude = 4.0 + number % 800 / 100
        longitude = -79.0 + number // 800 % 600 / 100
        region = "desconocida"
    else:
        latitude = base.latitude + (number % 200 - 100) / 10000
        longitude = base.longitude + (number // 200 % 200 - 100) / 10000
        region = base.region
    return {
        "source": SOURCE_GEOCODING,
        "city": city,
        "region": region,
        "latitude": round(latitude, 6),
        "longitude": round(longitude, 6),
    }


def simulated_exchange_rate(day: str) -> dict[str, Any]:
    """TRM USD/COP sintética elegida por hash de fuente y fecha."""
    number = digest_int(SOURCE_EXCHANGE_RATE, day)
    return {
        "source": SOURCE_EXCHANGE_RATE,
        "date": day,
        "base": "USD",
        "quote": "COP",
        "rate": round(3500 + number % 100000 / 100, 2),
    }


def _live_url(source: str, config: Settings) -> str | None:
    """Resuelve la URL live o falla sin red si la bandera está encendida."""
    if not config.api_live:
        return None
    url = getattr(config, CONFIG_ATTR_BY_SOURCE[source])
    if not url:
        raise LiveSourceConfigError(
            f"{ENV_VAR_BY_SOURCE[source]} no configurado: "
            "NEXORA_API_LIVE=1 requiere la URL de la fuente"
        )
    return url


def _resolve(
    *,
    source: str,
    params: Mapping[str, Any],
    ttl_s: int | None,
    cache: DiskCache,
    config: Settings,
    url: str | None,
    transport: JsonTransport | None,
    simulate: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Sirve desde la caché o produce el payload, simulado o live."""
    key = cache_key(source, params)
    cached = cache.get(key, ttl_s=ttl_s)
    if cached is not None:
        return cached
    if url is None:
        value = simulate()
    else:
        active = transport if transport is not None else RequestsJsonTransport()
        payload = active.get_json(url, params=params, timeout=config.api_timeout_s)
        if not isinstance(payload, dict):
            raise LiveSourceResponseError(
                f"{ENV_VAR_BY_SOURCE[source]}: la respuesta no es un objeto JSON"
            )
        value = dict(payload)
    cache.put(key, value)
    return value


def fetch_weather(
    city: str,
    on_date: str | date,
    *,
    cache: DiskCache | None = None,
    config: Settings | None = None,
    transport: JsonTransport | None = None,
) -> dict[str, Any]:
    """Devuelve el payload de clima de una ciudad y fecha, con caché de una hora."""
    conf = config if config is not None else settings
    day = date_string(on_date)
    url = _live_url(SOURCE_WEATHER, conf)
    return _resolve(
        source=SOURCE_WEATHER,
        params={"city": city, "date": day},
        ttl_s=WEATHER_TTL_S,
        cache=cache if cache is not None else DiskCache(conf.api_cache_dir),
        config=conf,
        url=url,
        transport=transport,
        simulate=lambda: simulated_weather(city, day),
    )


def fetch_geocoding(
    city: str,
    *,
    cache: DiskCache | None = None,
    config: Settings | None = None,
    transport: JsonTransport | None = None,
) -> dict[str, Any]:
    """Devuelve el payload de geocodificación de una ciudad, sin caducidad."""
    conf = config if config is not None else settings
    url = _live_url(SOURCE_GEOCODING, conf)
    return _resolve(
        source=SOURCE_GEOCODING,
        params={"city": city},
        ttl_s=GEOCODING_TTL_S,
        cache=cache if cache is not None else DiskCache(conf.api_cache_dir),
        config=conf,
        url=url,
        transport=transport,
        simulate=lambda: simulated_geocoding(city),
    )


def fetch_exchange_rate(
    on_date: str | date,
    *,
    cache: DiskCache | None = None,
    config: Settings | None = None,
    transport: JsonTransport | None = None,
) -> dict[str, Any]:
    """Devuelve el payload de la TRM de una fecha, con caché de 24 horas."""
    conf = config if config is not None else settings
    day = date_string(on_date)
    url = _live_url(SOURCE_EXCHANGE_RATE, conf)
    return _resolve(
        source=SOURCE_EXCHANGE_RATE,
        params={"date": day},
        ttl_s=EXCHANGE_RATE_TTL_S,
        cache=cache if cache is not None else DiskCache(conf.api_cache_dir),
        config=conf,
        url=url,
        transport=transport,
        simulate=lambda: simulated_exchange_rate(day),
    )

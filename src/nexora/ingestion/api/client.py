"""Cliente HTTP de las fuentes de API con reintentos y límite de tasa.

El transporte, el reloj y la espera se inyectan: las pruebas no abren conexiones
ni duermen. El token viaja en la cabecera ``Authorization`` y nunca se registra
ni aparece en la representación del cliente. El sobre de paginación se valida
con Pydantic; el contenido de los elementos no se valida contra el contrato para
que un cambio de esquema de la fuente no se confunda con un payload inválido.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import requests
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from nexora.common.api_contracts import FIRST_PAGE, MAX_PAGE_SIZE
from nexora.common.config import Settings, settings

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_BASE_S = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_BACKOFF_MAX_S = 30.0
DEFAULT_RETRY_AFTER_MAX_S = 60.0
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_FIRST = 500
RETRYABLE_STATUSES: frozenset[int] = frozenset(
    {HTTP_TOO_MANY_REQUESTS, HTTP_SERVER_ERROR_FIRST, 502, 503, 504}
)


class ApiClientError(Exception):
    """Base de los errores del cliente de API."""


class ApiTransportError(ApiClientError):
    """Fallo de red al intentar la petición."""


class ApiResponseError(ApiClientError):
    """Respuesta correcta cuyo sobre de paginación no cumple el contrato."""


class ApiHttpError(ApiClientError):
    """Respuesta HTTP con un estado que el cliente no puede aprovechar."""

    def __init__(
        self,
        status_code: int,
        endpoint: str,
        page: int,
        *,
        retry_after_s: float | None = None,
    ) -> None:
        self.status_code = status_code
        self.endpoint = endpoint
        self.page = page
        self.retry_after_s = retry_after_s
        super().__init__(f"GET {endpoint}?page={page}: HTTP {status_code}")


@dataclass(frozen=True)
class HttpResponse:
    """Respuesta del transporte: bytes recibidos, JSON interpretado y cabeceras."""

    status_code: int
    headers: Mapping[str, str]
    content: bytes
    payload: Any = None


class Transport(Protocol):
    """Interfaz mínima que debe cumplir el transporte inyectado."""

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class RequestsTransport:
    """Transporte de producción basado en ``requests``."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session if session is not None else requests.Session()

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse:
        try:
            response = self._session.get(
                url, params=dict(params), headers=dict(headers), timeout=timeout
            )
        except requests.RequestException as exc:
            raise ApiTransportError(f"GET {url}: fallo de transporte") from exc
        content = response.content
        payload: Any = None
        if content:
            try:
                payload = json.loads(content)
            except (ValueError, UnicodeDecodeError):
                payload = None
        return HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=content,
            payload=payload,
        )


class PageEnvelope(BaseModel):
    """Sobre de paginación esperado; los elementos quedan como JSON arbitrario."""

    model_config = ConfigDict(extra="forbid")

    data: list[dict[str, Any]]
    page: int = Field(ge=FIRST_PAGE)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    total_pages: int = Field(ge=0)
    next_page: int | None = Field(default=None, ge=FIRST_PAGE)


@dataclass(frozen=True)
class ApiPage:
    """Una página validada, con su contenido crudo intacto."""

    endpoint: str
    page: int
    payload: dict[str, Any]
    envelope: PageEnvelope
    content: bytes

    @property
    def data(self) -> list[dict[str, Any]]:
        """Elementos de la página, sin transformar."""
        return self.payload["data"]


def backoff_delay(
    attempt: int,
    *,
    base_s: float = DEFAULT_BACKOFF_BASE_S,
    factor: float = DEFAULT_BACKOFF_FACTOR,
    cap_s: float = DEFAULT_BACKOFF_MAX_S,
) -> float:
    """Espera exponencial del intento dado, con tope."""
    if attempt < 1:
        raise ValueError("attempt debe ser al menos 1")
    return min(cap_s, base_s * factor ** (attempt - 1))


def retry_after_seconds(
    headers: Mapping[str, str], *, cap_s: float = DEFAULT_RETRY_AFTER_MAX_S
) -> float | None:
    """Segundos de ``Retry-After`` en segundos, limitados por ``cap_s``."""
    raw = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(value, cap_s)


class TokenBucket:
    """Balde de fichas con reloj y espera inyectables."""

    def __init__(
        self,
        rate_per_s: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if rate_per_s <= 0:
            raise ValueError("rate_per_s debe ser positivo")
        self._rate = float(rate_per_s)
        self._capacity = float(rate_per_s)
        self._tokens = self._capacity
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep
        self._last = self._clock()

    def acquire(self) -> None:
        """Consume una ficha, esperando a que se reponga si hace falta."""
        while True:
            now = self._clock()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            self._sleep((1.0 - self._tokens) / self._rate)


class ApiClient:
    """Recorre las páginas de un recurso con reintentos y límite de tasa."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | SecretStr | None = None,
        transport: Transport | None = None,
        page_size: int | None = None,
        rate_limit: float | None = None,
        timeout_s: float | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        backoff_max_s: float = DEFAULT_BACKOFF_MAX_S,
        retry_after_max_s: float = DEFAULT_RETRY_AFTER_MAX_S,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        config: Settings | None = None,
    ) -> None:
        conf = config if config is not None else settings
        secret = token if token is not None else conf.api_token
        resolved_token = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        if not resolved_token:
            raise ValueError("NEXORA_API_TOKEN no configurado: el cliente no puede autenticarse")
        if max_attempts < 1:
            raise ValueError("max_attempts debe ser al menos 1")
        self._base_url = (base_url if base_url is not None else conf.api_base_url).rstrip("/")
        self._page_size = page_size if page_size is not None else conf.api_page_size
        self._timeout_s = timeout_s if timeout_s is not None else conf.api_timeout_s
        self._transport = transport if transport is not None else RequestsTransport()
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        self._backoff_factor = backoff_factor
        self._backoff_max_s = backoff_max_s
        self._retry_after_max_s = retry_after_max_s
        self._headers = {"Authorization": f"Bearer {resolved_token}"}
        self._bucket = TokenBucket(
            rate_limit if rate_limit is not None else conf.api_rate_limit,
            clock=clock,
            sleep=sleep,
        )
        self._sleep = sleep if sleep is not None else time.sleep

    def __repr__(self) -> str:
        """No muestra la cabecera de autenticación."""
        return f"ApiClient(base_url={self._base_url!r}, max_attempts={self._max_attempts})"

    def _delay(self, attempt: int) -> float:
        return backoff_delay(
            attempt,
            base_s=self._backoff_base_s,
            factor=self._backoff_factor,
            cap_s=self._backoff_max_s,
        )

    def fetch_page(self, endpoint: str, page: int = FIRST_PAGE) -> ApiPage:
        """Obtiene y valida una página; agota los reintentos antes de fallar."""
        params = {"page": page, "page_size": self._page_size}
        url = f"{self._base_url}{endpoint}"
        for attempt in range(1, self._max_attempts + 1):
            self._bucket.acquire()
            try:
                response = self._transport.get(
                    url, params=params, headers=self._headers, timeout=self._timeout_s
                )
            except ApiTransportError:
                if attempt == self._max_attempts:
                    raise
                self._sleep(self._delay(attempt))
                continue

            if response.status_code in RETRYABLE_STATUSES:
                retry_after_s = retry_after_seconds(response.headers, cap_s=self._retry_after_max_s)
                error = ApiHttpError(
                    response.status_code, endpoint, page, retry_after_s=retry_after_s
                )
                if attempt == self._max_attempts:
                    raise error
                self._sleep(retry_after_s if retry_after_s is not None else self._delay(attempt))
                continue

            if response.status_code >= 400:
                raise ApiHttpError(response.status_code, endpoint, page)

            try:
                envelope = PageEnvelope.model_validate(response.payload)
            except ValidationError as exc:
                raise ApiResponseError(
                    f"GET {endpoint}?page={page}: sobre de paginación inválido"
                ) from exc
            if envelope.page != page or envelope.page_size != self._page_size:
                raise ApiResponseError(
                    f"GET {endpoint}?page={page}: la respuesta no coincide con la petición"
                )
            return ApiPage(
                endpoint=endpoint,
                page=page,
                payload=response.payload,
                envelope=envelope,
                content=response.content,
            )

        raise ApiClientError(f"GET {endpoint}?page={page}: reintentos agotados")


def iter_pages(client: ApiClient, endpoint: str) -> Iterator[ApiPage]:
    """Recorre el recurso hasta que ``next_page`` es nulo."""
    page: int | None = FIRST_PAGE
    seen: set[int] = set()
    while page is not None:
        if page in seen:
            raise ApiResponseError(f"{endpoint}: next_page repetido en {page}")
        seen.add(page)
        result = client.fetch_page(endpoint, page)
        yield result
        page = result.envelope.next_page

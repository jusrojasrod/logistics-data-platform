"""Plan determinista y transitorio de fallos 429/500 del mock TMS.

La decisión depende sólo de ``sha256(endpoint + página)``: el mismo par
endpoint/página produce siempre el mismo fallo y el mismo ``Retry-After``.
El fallo es transitorio: cada página falla como máximo ``max_failures``
peticiones y después sirve su contenido.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

MAX_FAILURES = 2
HTTP_TOO_MANY_REQUESTS = 429
HTTP_INTERNAL_SERVER_ERROR = 500
_FAULT_BUCKETS = 1000
_FIVE_HUNDRED_BUCKETS = 10
_TOO_MANY_BUCKETS = 20
_RETRY_AFTER_MAX_S = 5


@dataclass(frozen=True)
class FaultDecision:
    """Fallo asignado a una página: estado HTTP y segundos de ``Retry-After``."""

    status: int
    retry_after_s: int


def page_digest(endpoint: str, page: int) -> int:
    """Entero derivado de ``sha256(endpoint + página)`` codificado en UTF-8."""
    payload = f"{endpoint}{page}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


class FaultPlan:
    """Plan de fallos por posición de página con contador transitorio por clave."""

    def __init__(self, max_failures: int = MAX_FAILURES) -> None:
        if max_failures < 0:
            raise ValueError("max_failures no puede ser negativo")
        self._max_failures = max_failures
        self._attempts: dict[tuple[str, int], int] = {}

    def plan_for(self, endpoint: str, page: int) -> FaultDecision | None:
        """Devuelve el fallo asignado a la página sin modificar el estado."""
        digest = page_digest(endpoint, page)
        bucket = digest % _FAULT_BUCKETS
        if bucket < _FIVE_HUNDRED_BUCKETS:
            status = HTTP_INTERNAL_SERVER_ERROR
        elif bucket < _TOO_MANY_BUCKETS:
            status = HTTP_TOO_MANY_REQUESTS
        else:
            return None
        retry_after_s = 1 + (digest // _FAULT_BUCKETS) % _RETRY_AFTER_MAX_S
        return FaultDecision(status=status, retry_after_s=retry_after_s)

    def consume(self, endpoint: str, page: int) -> FaultDecision | None:
        """Aplica el fallo si la página lo tiene y no agotó su cupo de intentos."""
        decision = self.plan_for(endpoint, page)
        if decision is None:
            return None
        key = (endpoint, page)
        attempts = self._attempts.get(key, 0)
        if attempts >= self._max_failures:
            return None
        self._attempts[key] = attempts + 1
        return decision

    def faulting_pages(self, endpoint: str, pages: range) -> dict[int, FaultDecision]:
        """Páginas de un rango con fallo asignado, para pruebas y diagnóstico."""
        found: dict[int, FaultDecision] = {}
        for page in pages:
            decision = self.plan_for(endpoint, page)
            if decision is not None:
                found[page] = decision
        return found

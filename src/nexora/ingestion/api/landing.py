"""Aterrizaje inmutable del JSON crudo de la vía API en MongoDB.

Cada página se guarda como un documento en ``raw.api_payloads`` con los seis
metadatos obligatorios y el contenido sin transformar. La idempotencia es por
ejecución: la clave ``(run_id, endpoint, page)`` repetida con el mismo hash no
escribe nada y repetida con otro contenido falla; otra ejecución del mismo
rango aterriza sus documentos aunque el contenido coincida. El hash se calcula
sobre los bytes crudos de ``ApiPage.content``, no sobre una reserialización.

El drift detectado se registra en ``meta.api_schema_drift`` con la regla DQ-008
y severidad bloqueante. Ni el aterrizaje ni el registro actualizan ni borran lo
ya escrito.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pymongo.errors import DuplicateKeyError

from nexora.ingestion.api.client import ApiPage

PAYLOADS_DATABASE = "raw"
PAYLOADS_COLLECTION = "api_payloads"
DRIFT_DATABASE = "meta"
DRIFT_COLLECTION = "api_schema_drift"
UNIQUE_KEY = ("run_id", "endpoint", "page")
METADATA_FIELDS = (
    "run_id",
    "source_system",
    "endpoint",
    "page",
    "payload_sha256",
    "ingested_at",
)
DRIFT_RULE_ID = "DQ-008"
DRIFT_SEVERITY = "blocking"
DRIFT_KEY = ("run_id", "endpoint", "page", "source_key", "field", "change_type")
CHANGE_TYPES: frozenset[str] = frozenset({"added", "removed", "retyped"})
DEFAULT_CONTENT_TYPE = "application/json"
DEFAULT_HTTP_STATUS = 200


class LandingError(RuntimeError):
    """Fallo al aterrizar un payload o al registrar su drift."""


class LandingValidationError(LandingError):
    """Un metadato obligatorio o una entrada de drift no cumple el contrato."""


class PayloadRewriteError(LandingError):
    """La clave de ejecución llegó con un contenido distinto del aterrizado."""

    def __init__(
        self,
        run_id: str,
        endpoint: str,
        page: int,
        stored_sha256: str,
        incoming_sha256: str,
    ) -> None:
        self.run_id = run_id
        self.endpoint = endpoint
        self.page = page
        self.stored_sha256 = stored_sha256
        self.incoming_sha256 = incoming_sha256
        super().__init__(
            f"{run_id}/{endpoint}/{page}: ya aterrizado con sha256 {stored_sha256}, "
            f"entrante con sha256 {incoming_sha256}"
        )


@dataclass(frozen=True)
class LandingResult:
    """Resultado de aterrizar una página."""

    run_id: str
    endpoint: str
    page: int
    payload_sha256: str
    inserted: bool


@dataclass(frozen=True)
class DriftEntry:
    """Un cambio de esquema detectado en un elemento de una página."""

    endpoint: str
    page: int
    field: str
    change_type: str
    expected_type: str | None
    actual_type: str | None
    source_key: str
    payload_sha256: str


@dataclass(frozen=True)
class DriftResult:
    """Resultado de registrar una entrada de drift."""

    run_id: str
    source_key: str
    inserted: bool


class AppendOnlyCollection(Protocol):
    """Solo lectura e inserción: el aterrizaje no dispone de update ni delete."""

    def find_one(
        self, filter: Mapping[str, Any], *args: Any, **kwargs: Any
    ) -> Mapping[str, Any] | None: ...

    def insert_one(self, document: Mapping[str, Any], *args: Any, **kwargs: Any) -> Any: ...


def sha256_bytes(content: bytes) -> str:
    """SHA-256 hexadecimal de los bytes recibidos."""
    if not isinstance(content, bytes):
        raise LandingValidationError("content debe ser bytes")
    return hashlib.sha256(content).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _metadata(
    run_id: str,
    source_system: str,
    endpoint: str,
    page_number: int,
    digest: str,
    ingested_at: datetime,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "run_id": run_id,
        "source_system": source_system,
        "endpoint": endpoint,
        "page": page_number,
        "payload_sha256": digest,
        "ingested_at": ingested_at,
    }
    for name in METADATA_FIELDS:
        value = values[name]
        if value is None or (isinstance(value, str) and not value.strip()):
            raise LandingValidationError(f"{name} es obligatorio y no puede estar vacío")
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise LandingValidationError("page debe ser un entero positivo")
    if not isinstance(ingested_at, datetime):
        raise LandingValidationError("ingested_at debe ser una fecha")
    return values


def _existing_sha256(
    collection: AppendOnlyCollection, key: Mapping[str, Any]
) -> tuple[str | None, bool]:
    """Devuelve ``(hash guardado, existe)`` para una clave ya aterrizada."""
    found = collection.find_one(dict(key))
    return (found["payload_sha256"] if found is not None else None, found is not None)


def land_page(
    collection: AppendOnlyCollection,
    page: ApiPage,
    *,
    run_id: str,
    source_system: str,
    content_type: str = DEFAULT_CONTENT_TYPE,
    http_status: int = DEFAULT_HTTP_STATUS,
    ingested_at: datetime | None = None,
) -> LandingResult:
    """Aterriza una página si su clave de ejecución aún no existe.

    Repetir la clave con el mismo hash no cambia el documento guardado; repetirla
    con otro hash lanza ``PayloadRewriteError``. Una clave nueva se inserta
    siempre, aunque ese hash ya exista en otro ``run_id``.
    """
    digest = sha256_bytes(page.content)
    metadata = _metadata(
        run_id, source_system, page.endpoint, page.page, digest, ingested_at or _utc_now()
    )
    key = {name: metadata[name] for name in UNIQUE_KEY}
    document = {
        **metadata,
        "payload": page.payload,
        "content_type": content_type,
        "http_status": http_status,
    }
    stored, exists = _existing_sha256(collection, key)
    if exists:
        if stored != digest:
            raise PayloadRewriteError(run_id, page.endpoint, page.page, str(stored), digest)
        return LandingResult(run_id, page.endpoint, page.page, digest, False)
    try:
        collection.insert_one(document)
    except DuplicateKeyError:
        stored, exists = _existing_sha256(collection, key)
        if not exists:
            raise
        if stored != digest:
            raise PayloadRewriteError(
                run_id, page.endpoint, page.page, str(stored), digest
            ) from None
        return LandingResult(run_id, page.endpoint, page.page, digest, False)
    return LandingResult(run_id, page.endpoint, page.page, digest, True)


def land_pages(
    collection: AppendOnlyCollection,
    pages: Iterable[ApiPage],
    *,
    run_id: str,
    source_system: str,
    clock: Callable[[], datetime] | None = None,
    content_type: str = DEFAULT_CONTENT_TYPE,
    http_status: int = DEFAULT_HTTP_STATUS,
) -> tuple[LandingResult, ...]:
    """Aterriza varias páginas de una misma ejecución y devuelve su resultado."""
    timestamp = clock if clock is not None else _utc_now
    return tuple(
        land_page(
            collection,
            page,
            run_id=run_id,
            source_system=source_system,
            content_type=content_type,
            http_status=http_status,
            ingested_at=timestamp(),
        )
        for page in pages
    )


def record_drift(
    collection: AppendOnlyCollection,
    entry: DriftEntry,
    *,
    run_id: str,
    source_system: str,
    detected_at: datetime | None = None,
) -> DriftResult:
    """Registra un cambio de esquema con DQ-008 y severidad bloqueante.

    La colección ``meta.api_schema_drift`` comparte el validador de los seis
    metadatos, así que ``ingested_at`` y ``detected_at`` se escriben con el
    mismo instante. Repetir la misma entrada no añade una fila duplicada.
    """
    detected = detected_at or _utc_now()
    metadata = _metadata(
        run_id,
        source_system,
        entry.endpoint,
        entry.page,
        entry.payload_sha256,
        detected,
    )
    for name in ("field", "source_key"):
        value = getattr(entry, name)
        if not isinstance(value, str) or not value.strip():
            raise LandingValidationError(f"{name} es obligatorio y no puede estar vacío")
    if entry.change_type not in CHANGE_TYPES:
        raise LandingValidationError(f"change_type no válido: {entry.change_type}")
    key = {
        "run_id": run_id,
        "endpoint": entry.endpoint,
        "page": entry.page,
        "source_key": entry.source_key,
        "field": entry.field,
        "change_type": entry.change_type,
    }
    document = {
        **metadata,
        "field": entry.field,
        "change_type": entry.change_type,
        "expected_type": entry.expected_type,
        "actual_type": entry.actual_type,
        "source_key": entry.source_key,
        "rule_id": DRIFT_RULE_ID,
        "severity": DRIFT_SEVERITY,
        "detected_at": detected,
    }
    _, exists = _existing_sha256(collection, key)
    if exists:
        return DriftResult(run_id, entry.source_key, False)
    try:
        collection.insert_one(document)
    except DuplicateKeyError:
        _, exists = _existing_sha256(collection, key)
        if not exists:
            raise
        return DriftResult(run_id, entry.source_key, False)
    return DriftResult(run_id, entry.source_key, True)

"""Plan determinista de schema drift del mock TMS.

Con la bandera encendida, la página 1 de cada recurso lleva drift garantizado
y el resto se decide por ``sha256(endpoint + página) % 100 < 1``. Un segundo
hash elige, por elemento, un campo no clave y un tipo de cambio (``added``,
``removed`` o ``retyped``): exactamente uno por payload. La clave natural
nunca se toca, así que el conteo de claves sigue siendo válido.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from nexora.common.api_contracts import (
    FIRST_PAGE,
    JSON_DATE,
    JSON_INTEGER,
    JSON_STRING,
    JSON_TIMESTAMP,
    Field,
    ResourceContract,
)
from nexora.generators.api.faults import page_digest

CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_RETYPED = "retyped"
CHANGE_TYPES: tuple[str, ...] = (CHANGE_ADDED, CHANGE_REMOVED, CHANGE_RETYPED)
DRIFTED_MODULUS = 100
ADDED_FIELD = "drift_injected_field"
_ADDED_VALUE = "drift"
_MISSING = "missing"


@dataclass(frozen=True)
class DriftChange:
    """Cambio asignado a un payload: campo, tipo y tipos esperado/observado."""

    field: str
    change_type: str
    expected_type: str
    actual_type: str


def field_digest(endpoint: str, page: int, source_key: str) -> int:
    """Segundo hash, distinto del de fallos, que elige campo y tipo de cambio."""
    key = f"drift-field:{endpoint}{page}:{source_key}".encode()
    return int.from_bytes(hashlib.sha256(key).digest(), "big")


def _retype_value(field: Field) -> tuple[object, str]:
    """Valor de otro tipo JSON y su nombre, para simular un campo retipado."""
    if field.json_type in (JSON_STRING, JSON_DATE, JSON_TIMESTAMP):
        return 42, JSON_INTEGER
    return _ADDED_VALUE, JSON_STRING


class DriftPlan:
    """Plan de alteraciones de esquema elegidas por hash, sin estado mutable."""

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def is_drifted(self, endpoint: str, page: int) -> bool:
        """Dice si la página lleva drift, con la bandera encendida."""
        if not self._enabled:
            return False
        if page == FIRST_PAGE:
            return True
        return page_digest(endpoint, page) % DRIFTED_MODULUS < 1

    def change_for(self, contract: ResourceContract, page: int, source_key: str) -> DriftChange:
        """Elige por hash un campo no clave y un tipo de cambio para un elemento."""
        digest = field_digest(contract.endpoint, page, source_key)
        non_key = [field for field in contract.fields if field.name != contract.natural_key]
        field = non_key[digest % len(non_key)]
        change_type = CHANGE_TYPES[(digest // len(non_key)) % len(CHANGE_TYPES)]
        if change_type == CHANGE_ADDED:
            return DriftChange(ADDED_FIELD, CHANGE_ADDED, "", JSON_STRING)
        if change_type == CHANGE_REMOVED:
            return DriftChange(field.name, CHANGE_REMOVED, field.json_type, _MISSING)
        _, actual_type = _retype_value(field)
        return DriftChange(field.name, CHANGE_RETYPED, field.json_type, actual_type)

    def apply_to_page(
        self, envelope: dict[str, Any], contract: ResourceContract, page: int
    ) -> dict[str, Any]:
        """Devuelve el sobre con un cambio por elemento si la página lleva drift."""
        if not self.is_drifted(contract.endpoint, page):
            return envelope
        altered: list[dict[str, Any]] = []
        for item in envelope["data"]:
            row = dict(item)
            source_key = str(row[contract.natural_key])
            change = self.change_for(contract, page, source_key)
            if change.change_type == CHANGE_ADDED:
                row[change.field] = _ADDED_VALUE
            elif change.change_type == CHANGE_REMOVED:
                row.pop(change.field, None)
            else:
                value, _ = _retype_value(contract.field(change.field))
                row[change.field] = value
            altered.append(row)
        return {**envelope, "data": altered}

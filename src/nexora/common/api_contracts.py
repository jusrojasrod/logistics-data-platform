"""Contrato único de los recursos de la API simulada del TMS.

Aquí se declaran, una sola vez, los campos y sus tipos JSON, la clave natural,
el dataset y el prefijo de endpoint de cada recurso. El mock proyecta las filas
de ``tms`` con este contrato y el detector de drift compara contra él, de modo
que un cambio en la definición desalinea a ambos de forma visible.
"""

from __future__ import annotations

from dataclasses import dataclass

# Vocabulario de tipos del payload JSON.
JSON_INTEGER = "integer"
JSON_NUMBER = "number"
JSON_STRING = "string"
JSON_BOOLEAN = "boolean"
JSON_DATE = "date"
JSON_TIMESTAMP = "timestamp"

JSON_TYPES: frozenset[str] = frozenset(
    {JSON_INTEGER, JSON_NUMBER, JSON_STRING, JSON_BOOLEAN, JSON_DATE, JSON_TIMESTAMP}
)

SOURCE_SYSTEM = "tms"

FIRST_PAGE = 1
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000

PAGINATION_KEYS: tuple[str, ...] = ("data", "page", "page_size", "total_pages", "next_page")


@dataclass(frozen=True)
class Field:
    """Un campo del contrato y el tipo JSON con el que se serializa."""

    name: str
    json_type: str
    nullable: bool = False

    def __post_init__(self) -> None:
        if self.json_type not in JSON_TYPES:
            raise ValueError(f"{self.name}: tipo JSON desconocido {self.json_type!r}")


@dataclass(frozen=True)
class ResourceContract:
    """Contrato de uno de los tres recursos paginados del mock."""

    resource: str
    dataset: str
    endpoint: str
    natural_key: str
    fields: tuple[Field, ...]

    def __post_init__(self) -> None:
        if self.natural_key not in self.field_names:
            raise ValueError(f"{self.resource}: clave natural fuera del contrato")
        if len(set(self.field_names)) != len(self.field_names):
            raise ValueError(f"{self.resource}: campos duplicados en el contrato")

    @property
    def field_names(self) -> tuple[str, ...]:
        """Nombres de los campos, en el orden del payload."""
        return tuple(field.name for field in self.fields)

    def field(self, name: str) -> Field:
        """Devuelve la definición de un campo del contrato."""
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(f"{self.resource}: campo {name!r} fuera del contrato")


SHIPMENTS_CONTRACT = ResourceContract(
    resource="shipments",
    dataset="tms_shipments",
    endpoint="/v1/shipments",
    natural_key="shipment_id",
    fields=(
        Field("shipment_id", JSON_STRING),
        Field("customer_id", JSON_STRING, nullable=True),
        Field("carrier_id", JSON_STRING, nullable=True),
        Field("route_id", JSON_STRING),
        Field("service_type_id", JSON_STRING),
        Field("pickup_ts", JSON_TIMESTAMP),
        Field("pickup_date", JSON_DATE),
        Field("promised_delivery_ts", JSON_TIMESTAMP, nullable=True),
        Field("picked_up_at", JSON_TIMESTAMP),
        Field("delivered_at", JSON_TIMESTAMP, nullable=True),
        Field("ordered_qty", JSON_INTEGER),
        Field("delivered_qty", JSON_INTEGER, nullable=True),
        Field("ordered_weight_kg", JSON_NUMBER),
        Field("delivered_weight_kg", JSON_NUMBER, nullable=True),
        Field("definitive_failure_flag", JSON_BOOLEAN),
    ),
)

SHIPMENT_EVENTS_CONTRACT = ResourceContract(
    resource="shipment_events",
    dataset="tms_shipment_events",
    endpoint="/v1/shipment_events",
    natural_key="event_id",
    fields=(
        Field("event_id", JSON_STRING),
        Field("shipment_id", JSON_STRING),
        Field("event_ts", JSON_TIMESTAMP),
        Field("event_type", JSON_STRING, nullable=True),
        Field("location_city", JSON_STRING, nullable=True),
        Field("latitude", JSON_NUMBER),
        Field("longitude", JSON_NUMBER),
        Field("severity", JSON_STRING),
    ),
)

CARRIER_INCIDENTS_CONTRACT = ResourceContract(
    resource="carrier_incidents",
    dataset="tms_carrier_incidents",
    endpoint="/v1/carrier_incidents",
    natural_key="incident_id",
    fields=(
        Field("incident_id", JSON_STRING),
        Field("shipment_id", JSON_STRING),
        Field("carrier_id", JSON_STRING),
        Field("incident_type", JSON_STRING),
        Field("severity", JSON_STRING),
        Field("damage_flag", JSON_BOOLEAN),
        Field("claim_amount", JSON_NUMBER, nullable=True),
        Field("currency_code", JSON_STRING),
        Field("reported_ts", JSON_TIMESTAMP),
        Field("responded_ts", JSON_TIMESTAMP),
        Field("resolved_ts", JSON_TIMESTAMP, nullable=True),
    ),
)

RESOURCE_CONTRACTS: dict[str, ResourceContract] = {
    contract.resource: contract
    for contract in (SHIPMENTS_CONTRACT, SHIPMENT_EVENTS_CONTRACT, CARRIER_INCIDENTS_CONTRACT)
}

DATASET_CONTRACTS: dict[str, ResourceContract] = {
    contract.dataset: contract for contract in RESOURCE_CONTRACTS.values()
}

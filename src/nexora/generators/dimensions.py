"""Stable dimension catalogs and their CSV export.

Each source entity is written to its own CSV file with a fixed column order.
Natural ids depend only on the position of the row inside the catalog, so they
stay stable when attributes or the volume profile change. ``source_system`` is
declared per entity: customers come from the ERP, the rest from the TMS.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from nexora.generators.config import (
    AVERAGE_SPEED_KMH,
    CARRIER_COUNT,
    CARRIER_NAME_PREFIXES,
    CARRIER_NAME_ROOTS,
    CARRIER_RATE_INDEX_MIN,
    CARRIER_RATE_INDEX_STEP,
    CARRIER_SERVICE_LEVELS,
    CITY_CATALOG,
    CUSTOMER_COUNT,
    CUSTOMER_INDUSTRIES,
    CUSTOMER_NAME_PREFIXES,
    CUSTOMER_NAME_ROOTS,
    CUSTOMER_PAYMENT_TERMS_DAYS,
    CUSTOMER_SEGMENTS,
    EARTH_RADIUS_KM,
    LOADING_HOURS,
    OWNED_CARRIER_COUNT,
    PRIMARY_ROAD_MIN_KM,
    ROAD_FACTOR,
    SECONDARY_ROAD_MIN_KM,
    SERVICE_TYPE_CATALOG,
    City,
)

CUSTOMERS_ENTITY: str = "customers"
CARRIERS_ENTITY: str = "carriers"
ROUTES_ENTITY: str = "routes"
SERVICE_TYPES_ENTITY: str = "service_types"

ERP_SOURCE_SYSTEM: str = "erp"
TMS_SOURCE_SYSTEM: str = "tms"

# Source system of each entity. Customers are masters loaded from the ERP; the
# remaining entities come from the simulated TMS.
ENTITY_SOURCE_SYSTEM: dict[str, str] = {
    CUSTOMERS_ENTITY: ERP_SOURCE_SYSTEM,
    CARRIERS_ENTITY: TMS_SOURCE_SYSTEM,
    ROUTES_ENTITY: TMS_SOURCE_SYSTEM,
    SERVICE_TYPES_ENTITY: TMS_SOURCE_SYSTEM,
}

CUSTOMERS_COLUMNS: tuple[str, ...] = (
    "customer_id",
    "customer_name",
    "segment",
    "industry",
    "region",
    "city",
    "payment_term_days",
)

CARRIERS_COLUMNS: tuple[str, ...] = (
    "carrier_id",
    "carrier_name",
    "carrier_type",
    "service_level",
    "rate_index",
)

ROUTES_COLUMNS: tuple[str, ...] = (
    "route_id",
    "origin_city",
    "destination_city",
    "distance_km",
    "estimated_hours",
    "road_type",
)

SERVICE_TYPES_COLUMNS: tuple[str, ...] = (
    "service_type_id",
    "service_type_name",
    "sla_hours",
)


@dataclass(frozen=True)
class DimensionTable:
    """A dimension entity ready to be exported as a single CSV file."""

    name: str
    source_system: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]

    def write_csv(self, directory: str | Path) -> Path:
        """Write the table to ``<directory>/<name>.csv`` and return the path."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{self.name}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(self.columns),
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(self.rows)
        return path


def _check_name_capacity(
    label: str, count: int, prefixes: tuple[str, ...], roots: tuple[str, ...]
) -> None:
    capacity = len(prefixes) * len(roots)
    if count > capacity:
        raise ValueError(f"{label}: {count} rows exceed the {capacity} unique name combinations")


def _name(prefixes: tuple[str, ...], roots: tuple[str, ...], index: int) -> str:
    """Build the name at ``index`` from two position-based pools."""
    prefix = prefixes[index % len(prefixes)]
    root = roots[(index // len(prefixes)) % len(roots)]
    return f"{prefix} {root}"


def build_customers() -> DimensionTable:
    """Return the customer catalog, one row per customer."""
    _check_name_capacity(
        CUSTOMERS_ENTITY, CUSTOMER_COUNT, CUSTOMER_NAME_PREFIXES, CUSTOMER_NAME_ROOTS
    )
    rows: list[dict[str, str]] = []
    for index in range(CUSTOMER_COUNT):
        city = CITY_CATALOG[index % len(CITY_CATALOG)]
        rows.append(
            {
                "customer_id": f"CUS-{index + 1:04d}",
                "customer_name": _name(CUSTOMER_NAME_PREFIXES, CUSTOMER_NAME_ROOTS, index),
                "segment": CUSTOMER_SEGMENTS[index % len(CUSTOMER_SEGMENTS)],
                "industry": CUSTOMER_INDUSTRIES[index % len(CUSTOMER_INDUSTRIES)],
                "region": city.region,
                "city": city.name,
                "payment_term_days": str(
                    CUSTOMER_PAYMENT_TERMS_DAYS[index % len(CUSTOMER_PAYMENT_TERMS_DAYS)]
                ),
            }
        )
    return DimensionTable(
        name=CUSTOMERS_ENTITY,
        source_system=ENTITY_SOURCE_SYSTEM[CUSTOMERS_ENTITY],
        columns=CUSTOMERS_COLUMNS,
        rows=tuple(rows),
    )


def _carrier_type(index: int) -> str:
    return "owned" if index < OWNED_CARRIER_COUNT else "third_party"


def _rate_index(index: int) -> str:
    value = CARRIER_RATE_INDEX_MIN + (index % 10) * CARRIER_RATE_INDEX_STEP
    return f"{value:.2f}"


def build_carriers() -> DimensionTable:
    """Return the carrier catalog, one row per carrier."""
    _check_name_capacity(CARRIERS_ENTITY, CARRIER_COUNT, CARRIER_NAME_PREFIXES, CARRIER_NAME_ROOTS)
    rows: list[dict[str, str]] = []
    for index in range(CARRIER_COUNT):
        rows.append(
            {
                "carrier_id": f"CAR-{index + 1:03d}",
                "carrier_name": _name(CARRIER_NAME_PREFIXES, CARRIER_NAME_ROOTS, index),
                "carrier_type": _carrier_type(index),
                "service_level": CARRIER_SERVICE_LEVELS[index % len(CARRIER_SERVICE_LEVELS)],
                "rate_index": _rate_index(index),
            }
        )
    return DimensionTable(
        name=CARRIERS_ENTITY,
        source_system=ENTITY_SOURCE_SYSTEM[CARRIERS_ENTITY],
        columns=CARRIERS_COLUMNS,
        rows=tuple(rows),
    )


def _haversine_km(origin: City, destination: City) -> float:
    """Return the great-circle distance between two cities, in kilometres."""
    lat1 = math.radians(origin.latitude)
    lat2 = math.radians(destination.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(destination.longitude - origin.longitude)
    half_chord = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * math.asin(math.sqrt(half_chord)) * EARTH_RADIUS_KM


def _road_type(distance_km: float) -> str:
    if distance_km >= PRIMARY_ROAD_MIN_KM:
        return "primary"
    if distance_km >= SECONDARY_ROAD_MIN_KM:
        return "secondary"
    return "tertiary"


def build_routes() -> DimensionTable:
    """Return the route catalog, one row per ordered city pair."""
    rows: list[dict[str, str]] = []
    for origin in CITY_CATALOG:
        for destination in CITY_CATALOG:
            if origin.name == destination.name:
                continue
            distance_km = round(_haversine_km(origin, destination) * ROAD_FACTOR, 1)
            estimated_hours = round(distance_km / AVERAGE_SPEED_KMH + LOADING_HOURS, 1)
            rows.append(
                {
                    "route_id": f"RTE-{len(rows) + 1:04d}",
                    "origin_city": origin.name,
                    "destination_city": destination.name,
                    "distance_km": f"{distance_km:.1f}",
                    "estimated_hours": f"{estimated_hours:.1f}",
                    "road_type": _road_type(distance_km),
                }
            )
    return DimensionTable(
        name=ROUTES_ENTITY,
        source_system=ENTITY_SOURCE_SYSTEM[ROUTES_ENTITY],
        columns=ROUTES_COLUMNS,
        rows=tuple(rows),
    )


def build_service_types() -> DimensionTable:
    """Return the service type catalog, one row per service level."""
    rows = tuple(
        {
            "service_type_id": service_type.service_type_id,
            "service_type_name": service_type.service_type_name,
            "sla_hours": str(service_type.sla_hours),
        }
        for service_type in SERVICE_TYPE_CATALOG
    )
    return DimensionTable(
        name=SERVICE_TYPES_ENTITY,
        source_system=ENTITY_SOURCE_SYSTEM[SERVICE_TYPES_ENTITY],
        columns=SERVICE_TYPES_COLUMNS,
        rows=rows,
    )


def build_dimensions() -> tuple[DimensionTable, ...]:
    """Return every dimension entity in a fixed order."""
    return (
        build_customers(),
        build_carriers(),
        build_routes(),
        build_service_types(),
    )


def write_dimensions(directory: str | Path) -> tuple[Path, ...]:
    """Write one CSV per dimension entity and return the written paths."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    return tuple(table.write_csv(target) for table in build_dimensions())

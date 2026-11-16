"""Fleet catalogs of the owned carriers: vehicles and drivers.

Both catalogs are fixed in size and deterministic by position, like the
dimension catalogs: the attributes of a row depend only on its index, never on
a random generator or the system clock. Only owned carriers have fleet; third
party carriers bring their own and are not modelled.

Driver data is fictitious by construction: names combine two invented pools,
documents use a prefix no authority issues, phones use an unassigned prefix and
emails a domain reserved for documentation.
"""

from __future__ import annotations

from datetime import date, timedelta

from nexora.generators.config import (
    CITY_CATALOG,
    DRIVER_DOCUMENT_PREFIX,
    DRIVER_EMAIL_DOMAIN,
    DRIVER_FAMILY_NAMES,
    DRIVER_GIVEN_NAMES,
    DRIVER_HIRE_SPAN_DAYS,
    DRIVER_HIRE_START,
    DRIVER_LICENSE_TYPES,
    DRIVER_PHONE_PREFIX,
    DRIVERS_PER_OWNED_CARRIER,
    FLEET_DATE_STRIDE_DAYS,
    FLEET_PURCHASE_SPAN_DAYS,
    FLEET_PURCHASE_START,
    VEHICLE_PLATE_PREFIX,
    VEHICLE_TYPE_CATALOG,
    VEHICLE_YIELD_STEPS,
    VEHICLES_PER_OWNED_CARRIER,
)
from nexora.generators.dimensions import (
    TMS_SOURCE_SYSTEM,
    DimensionTable,
    build_carriers,
)

VEHICLES_ENTITY: str = "vehicles"
DRIVERS_ENTITY: str = "drivers"

VEHICLES_COLUMNS: tuple[str, ...] = (
    "vehicle_id",
    "carrier_id",
    "plate",
    "vehicle_type",
    "capacity_kg",
    "capacity_m3",
    "fuel_type",
    "km_per_gallon",
    "purchase_date",
)

DRIVERS_COLUMNS: tuple[str, ...] = (
    "driver_id",
    "carrier_id",
    "driver_name",
    "driver_document",
    "driver_phone",
    "driver_email",
    "license_type",
    "hire_date",
    "base_city",
)


def owned_carrier_ids() -> tuple[str, ...]:
    """Return the ids of the owned carriers, in catalog order."""
    return tuple(
        row["carrier_id"] for row in build_carriers().rows if row["carrier_type"] == "owned"
    )


def _spread_date(start: date, span_days: int, index: int) -> date:
    """Return a date inside ``[start, start + span_days)`` chosen by position."""
    return start + timedelta(days=(index * FLEET_DATE_STRIDE_DAYS) % span_days)


def _driver_name(index: int) -> str:
    given = DRIVER_GIVEN_NAMES[index % len(DRIVER_GIVEN_NAMES)]
    family = DRIVER_FAMILY_NAMES[(index // len(DRIVER_GIVEN_NAMES)) % len(DRIVER_FAMILY_NAMES)]
    return f"{given} {family}"


def build_vehicles() -> DimensionTable:
    """Return the vehicle catalog, one row per vehicle of an owned carrier."""
    carriers = owned_carrier_ids()
    rows: list[dict[str, str]] = []
    for index in range(len(carriers) * VEHICLES_PER_OWNED_CARRIER):
        vehicle_type = VEHICLE_TYPE_CATALOG[index % len(VEHICLE_TYPE_CATALOG)]
        step = VEHICLE_YIELD_STEPS[(index // len(VEHICLE_TYPE_CATALOG)) % len(VEHICLE_YIELD_STEPS)]
        rows.append(
            {
                "vehicle_id": f"VEH-{index + 1:03d}",
                "carrier_id": carriers[index // VEHICLES_PER_OWNED_CARRIER],
                "plate": f"{VEHICLE_PLATE_PREFIX}-{index + 1:04d}",
                "vehicle_type": vehicle_type.vehicle_type,
                "capacity_kg": f"{vehicle_type.capacity_kg:.1f}",
                "capacity_m3": f"{vehicle_type.capacity_m3:.2f}",
                "fuel_type": vehicle_type.fuel_type,
                "km_per_gallon": f"{vehicle_type.km_per_gallon + step:.2f}",
                "purchase_date": _spread_date(
                    FLEET_PURCHASE_START, FLEET_PURCHASE_SPAN_DAYS, index
                ).isoformat(),
            }
        )
    return DimensionTable(
        name=VEHICLES_ENTITY,
        source_system=TMS_SOURCE_SYSTEM,
        columns=VEHICLES_COLUMNS,
        rows=tuple(rows),
    )


def build_drivers() -> DimensionTable:
    """Return the driver catalog, one row per driver of an owned carrier."""
    carriers = owned_carrier_ids()
    count = len(carriers) * DRIVERS_PER_OWNED_CARRIER
    capacity = len(DRIVER_GIVEN_NAMES) * len(DRIVER_FAMILY_NAMES)
    if count > capacity:
        raise ValueError(f"{DRIVERS_ENTITY}: {count} rows exceed the {capacity} unique names")
    rows: list[dict[str, str]] = []
    for index in range(count):
        number = index + 1
        rows.append(
            {
                "driver_id": f"DRV-{number:03d}",
                "carrier_id": carriers[index // DRIVERS_PER_OWNED_CARRIER],
                "driver_name": _driver_name(index),
                "driver_document": f"{DRIVER_DOCUMENT_PREFIX}-{number:07d}",
                "driver_phone": (
                    f"{DRIVER_PHONE_PREFIX} {number // 10_000:03d} {number % 10_000:04d}"
                ),
                "driver_email": f"drv{number:03d}@{DRIVER_EMAIL_DOMAIN}",
                "license_type": DRIVER_LICENSE_TYPES[index % len(DRIVER_LICENSE_TYPES)],
                "hire_date": _spread_date(
                    DRIVER_HIRE_START, DRIVER_HIRE_SPAN_DAYS, index
                ).isoformat(),
                "base_city": CITY_CATALOG[index % len(CITY_CATALOG)].name,
            }
        )
    return DimensionTable(
        name=DRIVERS_ENTITY,
        source_system=TMS_SOURCE_SYSTEM,
        columns=DRIVERS_COLUMNS,
        rows=tuple(rows),
    )

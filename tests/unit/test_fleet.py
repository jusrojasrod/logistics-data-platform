"""Pruebas de los catálogos de flota: vehículos y conductores de los carriers
propios, con datos personales ficticios y deterministas por posición."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from nexora.generators.config import CITY_CATALOG, CUTOFF_DATE
from nexora.generators.dimensions import TMS_SOURCE_SYSTEM, build_carriers
from nexora.generators.fleet import (
    DRIVERS_COLUMNS,
    VEHICLES_COLUMNS,
    build_drivers,
    build_vehicles,
)


def owned_ids() -> set[str]:
    return {row["carrier_id"] for row in build_carriers().rows if row["carrier_type"] == "owned"}


def test_counts_are_300_vehicles_and_400_drivers() -> None:
    assert len(build_vehicles().rows) == 300
    assert len(build_drivers().rows) == 400


def test_ids_are_unique() -> None:
    vehicles = build_vehicles().rows
    drivers = build_drivers().rows
    assert len({row["vehicle_id"] for row in vehicles}) == 300
    assert len({row["plate"] for row in vehicles}) == 300
    assert len({row["driver_id"] for row in drivers}) == 400
    for column in ("driver_name", "driver_document", "driver_phone", "driver_email"):
        assert len({row[column] for row in drivers}) == 400, column
    assert vehicles[0]["vehicle_id"] == "VEH-001"
    assert vehicles[-1]["vehicle_id"] == "VEH-300"
    assert drivers[0]["driver_id"] == "DRV-001"
    assert drivers[-1]["driver_id"] == "DRV-400"


def test_only_owned_carriers_split_evenly() -> None:
    owned = owned_ids()
    assert owned == {f"CAR-{n:03d}" for n in range(1, 11)}
    for table, per_carrier in ((build_vehicles(), 30), (build_drivers(), 40)):
        carriers = [row["carrier_id"] for row in table.rows]
        assert set(carriers) == owned
        assert all(carriers.count(carrier) == per_carrier for carrier in owned)


def test_driver_contact_data_is_fictitious() -> None:
    for row in build_drivers().rows:
        assert re.fullmatch(r"NXD-\d{7}", row["driver_document"]), row
        assert re.fullmatch(r"\+57 000 \d{3} \d{4}", row["driver_phone"]), row
        assert re.fullmatch(r"drv\d{3}@example\.com", row["driver_email"]), row
    first = build_drivers().rows[0]
    assert first["driver_document"] == "NXD-0000001"
    assert first["driver_phone"] == "+57 000 000 0001"
    assert first["driver_email"] == "drv001@example.com"


def test_two_calls_are_equal() -> None:
    assert build_vehicles() == build_vehicles()
    assert build_drivers() == build_drivers()


def test_columns_and_formats() -> None:
    vehicles = build_vehicles()
    drivers = build_drivers()
    assert vehicles.columns == VEHICLES_COLUMNS
    assert drivers.columns == DRIVERS_COLUMNS
    assert vehicles.source_system == drivers.source_system == TMS_SOURCE_SYSTEM
    assert all(tuple(row) == VEHICLES_COLUMNS for row in vehicles.rows)
    assert all(tuple(row) == DRIVERS_COLUMNS for row in drivers.rows)
    cities = {city.name for city in CITY_CATALOG}
    for row in vehicles.rows:
        assert re.fullmatch(r"NXV-\d{4}", row["plate"])
        assert re.fullmatch(r"\d+\.\d", row["capacity_kg"])
        assert re.fullmatch(r"\d+\.\d{2}", row["capacity_m3"])
        assert re.fullmatch(r"\d+\.\d{2}", row["km_per_gallon"])
        assert float(row["km_per_gallon"]) > 0
        assert date.fromisoformat(row["purchase_date"]) < CUTOFF_DATE
    for row in drivers.rows:
        assert row["base_city"] in cities
        assert row["license_type"]
        assert date.fromisoformat(row["hire_date"]) < CUTOFF_DATE


def test_csv_round_trip(tmp_path: Path) -> None:
    path = build_drivers().write_csv(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(DRIVERS_COLUMNS)
    assert len(lines) == 401

"""Pruebas del generador sintético: determinismo, fecha de corte, perfiles,
manifiesto de defectos y contrato de columnas de ``shipments.csv``.

El perfil ``demo`` se genera una sola vez por módulo en una carpeta temporal y
todas las pruebas de contenido leen esa corrida. El perfil ``local`` no se
genera completo: se comprueba que el CLI pide 200 000 envíos base y que los
conteos de defectos derivados de esa base son los esperados.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from nexora.generators import cli
from nexora.generators.config import (
    CRITICAL_NULL_COLUMNS,
    CRITICAL_NULL_RATE,
    CUTOFF_DATE,
    DELIVERED_BEFORE_PICKUP_RATE,
    DUPLICATE_RATE,
    HISTORY_MONTHS,
    window_start,
)
from nexora.generators.defects import (
    CRITICAL_NULL_TYPE,
    DELIVERED_BEFORE_PICKUP_TYPE,
    DUPLICATE_TYPE,
    MANIFEST_FILE_NAME,
    RATE_DECIMALS,
    defect_count,
    split_evenly,
)
from nexora.generators.dimensions import (
    build_carriers,
    build_customers,
    build_routes,
    build_service_types,
)
from nexora.generators.shipments import SHIPMENTS_COLUMNS

SEED = 42
DEMO_SHIPMENTS = 50_000
LOCAL_SHIPMENTS = 200_000

EXPECTED_FILES = {
    "shipments.csv",
    "customers.csv",
    "carriers.csv",
    "routes.csv",
    "service_types.csv",
    MANIFEST_FILE_NAME,
}

# Columnas que el contrato declara siempre informadas en el CSV de origen.
NEVER_NULL_COLUMNS = (
    "shipment_id",
    "route_id",
    "service_type_id",
    "pickup_ts",
    "pickup_date",
    "picked_up_at",
    "ordered_qty",
    "ordered_weight_kg",
    "definitive_failure_flag",
)

# Tasa configurada en ``config.py`` para cada entrada del manifiesto. Los nulos
# críticos reparten su tasa en partes iguales entre las tres columnas.
CONFIGURED_RATE: dict[str, float] = {
    DUPLICATE_TYPE: DUPLICATE_RATE,
    CRITICAL_NULL_TYPE: CRITICAL_NULL_RATE / len(CRITICAL_NULL_COLUMNS),
    DELIVERED_BEFORE_PICKUP_TYPE: DELIVERED_BEFORE_PICKUP_RATE,
}

# Error máximo del redondeo de ``rate`` a RATE_DECIMALS decimales: media unidad
# del último decimal (0,000005). Se suma un margen de coma flotante.
FLOAT_MARGIN = 1e-12
ROUNDING_TOLERANCE = 0.5 * 10**-RATE_DECIMALS + FLOAT_MARGIN


def configured_rate_tolerance(shipments: int) -> float:
    """Desviación máxima admisible entre ``rate`` y la tasa configurada.

    Dos fuentes de error, ambas acotadas:

    * conteo entero: ``count`` es ``round(shipments * tasa)`` y, en los nulos,
      ese total se reparte en tercios que difieren en una fila como mucho
      (167/167/166 sobre 50 000). Cada parte queda a menos de una fila de
      ``shipments * tasa / 3``, es decir, a menos de ``1 / shipments`` en tasa;
    * redondeo del manifiesto a 5 decimales: ``ROUNDING_TOLERANCE``.

    En ``demo`` la cota es 0,00002 + 0,000005 = 0,000025; el caso real más
    alejado es 0,00334 frente a 0,0033333 (0,0000067).
    """
    return 1 / shipments + ROUNDING_TOLERANCE


def run_generator(out: Path, *args: str) -> int:
    return cli.main(["--seed", str(SEED), "--out", str(out), *args])


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def file_digests(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
    }


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("demo")
    assert run_generator(out, "--scale", "demo") == 0
    return out


@pytest.fixture(scope="module")
def manifest(demo_dir: Path) -> dict[str, Any]:
    return json.loads((demo_dir / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def shipments_csv(demo_dir: Path) -> tuple[list[str], list[dict[str, str]]]:
    return read_csv(demo_dir / "shipments.csv")


@pytest.fixture(scope="module")
def shipment_rows(shipments_csv: tuple[list[str], list[dict[str, str]]]) -> list[dict[str, str]]:
    return shipments_csv[1]


@pytest.fixture(scope="module")
def base_rows(shipment_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Una fila por ``shipment_id``: las copias del 2 % son idénticas byte a byte."""
    unique: dict[str, dict[str, str]] = {}
    for row in shipment_rows:
        unique.setdefault(row["shipment_id"], row)
    return list(unique.values())


# Determinismo


def test_same_seed_produces_identical_files(demo_dir: Path, tmp_path: Path) -> None:
    second = tmp_path / "run2"
    assert run_generator(second, "--scale", "demo") == 0
    first_digests = file_digests(demo_dir)
    assert set(first_digests) == EXPECTED_FILES
    assert file_digests(second) == first_digests


def test_different_seed_changes_shipments(demo_dir: Path, tmp_path: Path) -> None:
    other = tmp_path / "seed7"
    assert cli.main(["--scale", "demo", "--seed", "7", "--out", str(other)]) == 0
    digests = file_digests(other)
    assert digests["shipments.csv"] != file_digests(demo_dir)["shipments.csv"]
    # Las dimensiones no dependen de la semilla: IDs estables entre corridas.
    for name in ("customers.csv", "carriers.csv", "routes.csv", "service_types.csv"):
        assert digests[name] == file_digests(demo_dir)[name]


def test_manifest_has_no_variable_metadata(manifest: dict[str, Any]) -> None:
    assert list(manifest) == ["scale", "seed", "months", "cutoff_date", "shipments", "defects"]


# Fecha de corte fija


def test_cutoff_date_is_fixed() -> None:
    assert date(2026, 11, 9) == CUTOFF_DATE
    assert window_start(CUTOFF_DATE, HISTORY_MONTHS) == date(2024, 12, 1)


def test_manifest_declares_fixed_cutoff(manifest: dict[str, Any]) -> None:
    assert manifest["cutoff_date"] == CUTOFF_DATE.isoformat()
    assert manifest["months"] == HISTORY_MONTHS


def test_pickups_stay_inside_the_window(shipment_rows: list[dict[str, str]]) -> None:
    pickup_dates = {date.fromisoformat(row["pickup_date"]) for row in shipment_rows}
    assert min(pickup_dates) >= window_start(CUTOFF_DATE, HISTORY_MONTHS)
    assert max(pickup_dates) <= CUTOFF_DATE


def test_no_delivery_after_cutoff(shipment_rows: list[dict[str, str]]) -> None:
    cutoff_end = datetime(CUTOFF_DATE.year, CUTOFF_DATE.month, CUTOFF_DATE.day, tzinfo=UTC)
    cutoff_end += timedelta(days=1)
    delivered = [parse_ts(row["delivered_at"]) for row in shipment_rows if row["delivered_at"]]
    assert delivered
    assert max(delivered) < cutoff_end


# Conteos por perfil


def test_demo_profile_counts(
    manifest: dict[str, Any],
    shipment_rows: list[dict[str, str]],
    base_rows: list[dict[str, str]],
) -> None:
    duplicates = defect_count(DEMO_SHIPMENTS, DUPLICATE_RATE)
    assert manifest["scale"] == "demo"
    assert manifest["shipments"] == DEMO_SHIPMENTS
    assert len(base_rows) == DEMO_SHIPMENTS
    assert len(shipment_rows) == DEMO_SHIPMENTS + duplicates


def test_scale_profiles_are_declared() -> None:
    assert cli.SCALE_SHIPMENTS == {"demo": DEMO_SHIPMENTS, "local": LOCAL_SHIPMENTS}


def test_local_profile_requests_its_volume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """El CLI pide 200 000 envíos con ``local`` sin generarlos en la prueba.

    Generar ``local`` completo cuesta unos 3 s; aquí se intercepta la llamada,
    se registra el volumen pedido y se generan 300 filas para el resto del flujo.
    """
    requested: list[int] = []
    real_generate = cli.generate_shipments

    def fake_generate(count: int, seed: int, months: int) -> tuple[dict[str, str], ...]:
        requested.append(count)
        return real_generate(300, seed, months)

    monkeypatch.setattr(cli, "generate_shipments", fake_generate)
    assert run_generator(tmp_path / "local", "--scale", "local") == 0
    assert requested == [LOCAL_SHIPMENTS]
    written = json.loads((tmp_path / "local" / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
    assert written["scale"] == "local"


@pytest.mark.parametrize(
    ("shipments", "expected"),
    [
        (DEMO_SHIPMENTS, {"duplicate": 1000, "nulls": (167, 167, 166), "before_pickup": 400}),
        (LOCAL_SHIPMENTS, {"duplicate": 4000, "nulls": (667, 667, 666), "before_pickup": 1600}),
    ],
)
def test_defect_counts_per_profile(shipments: int, expected: dict[str, Any]) -> None:
    assert defect_count(shipments, DUPLICATE_RATE) == expected["duplicate"]
    nulls = split_evenly(defect_count(shipments, CRITICAL_NULL_RATE), len(CRITICAL_NULL_COLUMNS))
    assert nulls == expected["nulls"]
    assert defect_count(shipments, DELIVERED_BEFORE_PICKUP_RATE) == expected["before_pickup"]


def test_dimension_files_are_not_empty(demo_dir: Path) -> None:
    for name in ("customers.csv", "carriers.csv", "routes.csv", "service_types.csv"):
        _, rows = read_csv(demo_dir / name)
        assert rows, name


# Tasas del manifiesto (C4)


def test_manifest_entries_follow_schema(manifest: dict[str, Any]) -> None:
    keys = [(entry["type"], entry["column"]) for entry in manifest["defects"]]
    assert keys == [
        (DUPLICATE_TYPE, "shipment_id"),
        *((CRITICAL_NULL_TYPE, column) for column in CRITICAL_NULL_COLUMNS),
        (DELIVERED_BEFORE_PICKUP_TYPE, "delivered_at"),
    ]
    for entry in manifest["defects"]:
        assert list(entry) == ["type", "column", "count", "rate", "rule_id", "expected_action"]
        assert entry["count"] > 0
        assert entry["rate"] > 0


def test_manifest_rate_matches_count_over_shipments(manifest: dict[str, Any]) -> None:
    """``rate`` es ``count / shipments`` redondeado a 5 decimales.

    La igualdad con ``round`` es exacta; la tolerancia solo cubre el error del
    propio redondeo (media unidad del quinto decimal).
    """
    shipments = manifest["shipments"]
    for entry in manifest["defects"]:
        exact = entry["count"] / shipments
        assert entry["rate"] == round(exact, RATE_DECIMALS), entry
        assert abs(entry["rate"] - exact) <= ROUNDING_TOLERANCE, entry


def test_manifest_rate_matches_configured_rate(manifest: dict[str, Any]) -> None:
    """``rate`` coincide con la tasa de ``config.py`` dentro de una cota justificada.

    No puede ser exacta: los conteos son enteros y los nulos se reparten en
    tercios (167/167/166 sobre 50 000, ``rate`` 0,00334 y 0,00332 frente a
    0,0033333). La cota está en ``configured_rate_tolerance``.
    """
    tolerance = configured_rate_tolerance(manifest["shipments"])
    for entry in manifest["defects"]:
        assert entry["rate"] == pytest.approx(CONFIGURED_RATE[entry["type"]], abs=tolerance), entry


def test_manifest_null_thirds_add_up_to_configured_rate(manifest: dict[str, Any]) -> None:
    null_entries = [e for e in manifest["defects"] if e["type"] == CRITICAL_NULL_TYPE]
    total = sum(entry["count"] for entry in null_entries)
    assert total == defect_count(manifest["shipments"], CRITICAL_NULL_RATE)
    counts = [entry["count"] for entry in null_entries]
    assert max(counts) - min(counts) <= 1


def test_manifest_counts_match_generated_rows(
    manifest: dict[str, Any],
    shipment_rows: list[dict[str, str]],
    base_rows: list[dict[str, str]],
) -> None:
    counts = {(entry["type"], entry["column"]): entry["count"] for entry in manifest["defects"]}

    assert counts[(DUPLICATE_TYPE, "shipment_id")] == len(shipment_rows) - len(base_rows)
    for column in CRITICAL_NULL_COLUMNS:
        observed = sum(1 for row in base_rows if row[column] == "")
        assert counts[(CRITICAL_NULL_TYPE, column)] == observed, column
    before_pickup = sum(
        1
        for row in base_rows
        if row["delivered_at"] and parse_ts(row["delivered_at"]) < parse_ts(row["picked_up_at"])
    )
    assert counts[(DELIVERED_BEFORE_PICKUP_TYPE, "delivered_at")] == before_pickup


# Contrato de columnas


def test_shipments_header_matches_contract(
    shipments_csv: tuple[list[str], list[dict[str, str]]],
) -> None:
    header, _ = shipments_csv
    assert header == list(SHIPMENTS_COLUMNS)
    assert len(header) == 15


def test_never_null_columns_are_filled(shipment_rows: list[dict[str, str]]) -> None:
    for column in NEVER_NULL_COLUMNS:
        assert all(row[column] != "" for row in shipment_rows), column


def test_definitive_failure_flag_is_boolean(shipment_rows: list[dict[str, str]]) -> None:
    assert {row["definitive_failure_flag"] for row in shipment_rows} == {"true", "false"}


def test_pickup_date_is_pickup_ts_date(shipment_rows: list[dict[str, str]]) -> None:
    """``pickup_date = pickup_ts::date`` en UTC y en la hora local de la operación."""
    local = timezone(timedelta(hours=-5))
    for row in shipment_rows:
        pickup_ts = parse_ts(row["pickup_ts"])
        assert pickup_ts.utcoffset() == timedelta(0)
        expected = date.fromisoformat(row["pickup_date"])
        assert pickup_ts.astimezone(UTC).date() == expected, row["shipment_id"]
        assert pickup_ts.astimezone(local).date() == expected, row["shipment_id"]


def test_quantities_and_weights_are_positive(shipment_rows: list[dict[str, str]]) -> None:
    for row in shipment_rows:
        assert int(row["ordered_qty"]) >= 1
        assert float(row["ordered_weight_kg"]) > 0
        if row["delivered_qty"]:
            assert 1 <= int(row["delivered_qty"]) <= int(row["ordered_qty"])
            assert 0 < float(row["delivered_weight_kg"]) <= float(row["ordered_weight_kg"])


def test_one_injected_null_per_row(shipment_rows: list[dict[str, str]]) -> None:
    for row in shipment_rows:
        assert sum(1 for column in CRITICAL_NULL_COLUMNS if row[column] == "") <= 1


def test_ids_belong_to_catalogs(shipment_rows: list[dict[str, str]]) -> None:
    catalogs = {
        "customer_id": {row["customer_id"] for row in build_customers().rows},
        "carrier_id": {row["carrier_id"] for row in build_carriers().rows},
        "route_id": {row["route_id"] for row in build_routes().rows},
        "service_type_id": {row["service_type_id"] for row in build_service_types().rows},
    }
    for column, ids in catalogs.items():
        observed = {row[column] for row in shipment_rows if row[column]}
        assert observed <= ids, column


# Equivalencia entre entrega y campos de entrega


def test_delivery_fields_null_iff_no_delivery(shipment_rows: list[dict[str, str]]) -> None:
    for row in shipment_rows:
        no_delivery = row["delivered_at"] == ""
        assert (row["delivered_qty"] == "") is no_delivery, row["shipment_id"]
        assert (row["delivered_weight_kg"] == "") is no_delivery, row["shipment_id"]


def test_both_delivery_states_are_present(shipment_rows: list[dict[str, str]]) -> None:
    states = {row["delivered_at"] == "" for row in shipment_rows}
    assert states == {True, False}


def test_definitive_failure_has_no_delivery(shipment_rows: list[dict[str, str]]) -> None:
    for row in shipment_rows:
        if row["definitive_failure_flag"] == "true":
            assert row["delivered_at"] == "", row["shipment_id"]

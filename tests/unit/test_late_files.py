"""Pruebas de la regla que reparte las filas entre el archivo principal y el tardío."""

from __future__ import annotations

import hashlib

import pytest

from nexora.generators.config import LATE_DIRECTORY, LATE_FILE_PCT, LATE_FILE_SUFFIX
from nexora.generators.late_files import is_late, late_file_name, late_key
from nexora.ingestion.batch import loaders

TARIFF_KEY = ("tariff_id", "valid_from")


def reference_is_late(key: str) -> bool:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100 < 3


def test_late_constants() -> None:
    assert LATE_FILE_PCT == 3
    assert LATE_DIRECTORY == "late"
    assert LATE_FILE_SUFFIX == "_late"


def test_late_directory_matches_loader() -> None:
    assert LATE_DIRECTORY == loaders.LATE_DIRECTORY


def test_is_late_matches_hash_rule() -> None:
    keys = [f"INV-{i:07d}" for i in range(20_000)]
    assert [is_late(k) for k in keys] == [reference_is_late(k) for k in keys]
    assert any(is_late(k) for k in keys)
    assert not all(is_late(k) for k in keys)


def test_is_late_is_deterministic() -> None:
    keys = [f"CNT-{i:05d}" for i in range(1_000)]
    assert [is_late(k) for k in keys] == [is_late(k) for k in keys]


def test_tariff_key_joins_id_and_valid_from() -> None:
    row = {"tariff_id": "TRF-00001", "valid_from": "2024-12-01", "customer_id": "CUS-0001"}
    key = late_key(row, TARIFF_KEY)
    assert key == "TRF-00001|2024-12-01"
    assert is_late(key) == reference_is_late("TRF-00001|2024-12-01")


def test_tariff_versions_have_distinct_keys() -> None:
    v1 = {"tariff_id": "TRF-00001", "valid_from": "2024-12-01"}
    v2 = {"tariff_id": "TRF-00001", "valid_from": "2025-06-14"}
    assert late_key(v1, TARIFF_KEY) != late_key(v2, TARIFF_KEY)


def test_single_column_key_is_the_value() -> None:
    assert late_key({"invoice_id": "INV-0000001"}, ("invoice_id",)) == "INV-0000001"


def test_late_key_requires_columns() -> None:
    with pytest.raises(ValueError):
        late_key({"invoice_id": "INV-0000001"}, ())


def test_late_key_missing_column_fails() -> None:
    with pytest.raises(KeyError):
        late_key({"tariff_id": "TRF-00001"}, TARIFF_KEY)


@pytest.mark.parametrize(
    "entity",
    [
        "tariffs",
        "contacts",
        "contracts",
        "leads",
        "invoices",
        "invoice_lines",
        "payments",
        "carrier_settlements",
        "shipment_costs",
    ],
)
def test_late_file_name(entity: str) -> None:
    assert late_file_name(entity) == f"late/{entity}_late.csv"

"""Pruebas de los lectores de archivos: descubrimiento por tabla, hash, cabecera y
lectura por bloques.

Sin base de datos: los archivos se crean en ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexora.ingestion.batch.loaders import CUSTOMERS, SHIPMENT_EVENTS
from nexora.ingestion.batch.readers import (
    check_header,
    discover_files,
    file_sha256,
    iter_row_blocks,
)

CUSTOMERS_BYTES = (
    b"customer_id,customer_name,segment,industry,region,city,payment_term_days\n"
    b"CUS-0001,Cliente Uno,pyme,retail,Andina,Bogot\xc3\xa1,30\n"
)


def write_bytes(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_discover_finds_table_files_and_ignores_others(tmp_path: Path) -> None:
    write_bytes(tmp_path / "customers_copy.csv", CUSTOMERS_BYTES)
    write_bytes(tmp_path / "customers.csv", CUSTOMERS_BYTES)
    write_bytes(tmp_path / "carriers.csv", b"carrier_id\nCAR-001\n")
    write_bytes(tmp_path / "customers.txt", CUSTOMERS_BYTES)
    (tmp_path / "customers_dir.csv").mkdir()

    found = discover_files(tmp_path, "customers")

    assert [path.name for path in found] == ["customers.csv", "customers_copy.csv"]
    assert all(path.parent == tmp_path for path in found)


def test_discover_accepts_str_path_and_returns_empty_without_matches(tmp_path: Path) -> None:
    write_bytes(tmp_path / "carriers.csv", b"carrier_id\nCAR-001\n")

    assert discover_files(str(tmp_path), "customers") == []


def test_discover_missing_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_files(tmp_path / "missing", "customers")


def test_renamed_copy_has_same_hash(tmp_path: Path) -> None:
    original = write_bytes(tmp_path / "customers.csv", CUSTOMERS_BYTES)
    copy = write_bytes(tmp_path / "customers_copy.csv", CUSTOMERS_BYTES)

    assert file_sha256(copy) == file_sha256(original)


def test_modified_file_has_different_hash(tmp_path: Path) -> None:
    original = write_bytes(tmp_path / "customers.csv", CUSTOMERS_BYTES)
    late = write_bytes(
        tmp_path / "customers_late.csv", CUSTOMERS_BYTES.replace(b"Bogot\xc3\xa1", b"Tunja")
    )

    assert file_sha256(late) != file_sha256(original)


def test_check_header_accepts_contract_order(tmp_path: Path) -> None:
    check_header(CUSTOMERS, write_bytes(tmp_path / "customers.csv", CUSTOMERS_BYTES))


def test_check_header_rejects_other_order(tmp_path: Path) -> None:
    swapped = CUSTOMERS_BYTES.replace(b"customer_id,customer_name", b"customer_name,customer_id")
    path = write_bytes(tmp_path / "customers.csv", swapped)

    with pytest.raises(ValueError, match="distinta del contrato"):
        check_header(CUSTOMERS, path)


EVENTS_HEADER = ",".join(SHIPMENT_EVENTS.column_names)


def write_events(path: Path, ids: list[str]) -> Path:
    lines = [EVENTS_HEADER]
    lines += [
        f"{event_id},SHP-000001,2026-11-01T10:00:00+00:00,created,Bogotá,4.711000,"
        "-74.072000,info"
        for event_id in ids
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_row_blocks_keep_contiguous_copies_in_one_block(tmp_path: Path) -> None:
    # EVT-2 y EVT-5 tienen copias justo detrás del original y caen en el límite.
    ids = ["EVT-1", "EVT-2", "EVT-2", "EVT-3", "EVT-4", "EVT-5", "EVT-5", "EVT-5", "EVT-6"]
    path = write_events(tmp_path / "shipment_events.csv", ids)

    blocks = list(iter_row_blocks(path, SHIPMENT_EVENTS, 2, ("event_id",)))

    assert [[row["event_id"] for row in block] for block in blocks] == [
        ["EVT-1", "EVT-2", "EVT-2"],
        ["EVT-3", "EVT-4"],
        ["EVT-5", "EVT-5", "EVT-5"],
        ["EVT-6"],
    ]
    for event_id in set(ids):
        assert sum(any(r["event_id"] == event_id for r in block) for block in blocks) == 1


def test_row_blocks_preserve_rows_and_order(tmp_path: Path) -> None:
    ids = [f"EVT-{i:03d}" for i in range(1, 26)] + ["EVT-025"]
    path = write_events(tmp_path / "shipment_events.csv", ids)

    for size in (1, 3, 7, 25, 1000):
        blocks = list(iter_row_blocks(path, SHIPMENT_EVENTS, size, ("event_id",)))
        assert [row["event_id"] for block in blocks for row in block] == ids
        assert all(len(block) <= size + 1 for block in blocks)
    first = next(iter_row_blocks(str(path), SHIPMENT_EVENTS, 10, ("event_id",)))[0]
    assert list(first) == list(SHIPMENT_EVENTS.column_names)
    assert first["location_city"] == "Bogotá"


def test_row_blocks_with_composite_key(tmp_path: Path) -> None:
    path = write_events(tmp_path / "shipment_events.csv", ["EVT-1", "EVT-2", "EVT-3"])

    blocks = list(iter_row_blocks(path, SHIPMENT_EVENTS, 1, ("shipment_id", "severity")))

    assert len(blocks) == 1 and len(blocks[0]) == 3


def test_row_blocks_of_header_only_file_yield_nothing(tmp_path: Path) -> None:
    path = write_events(tmp_path / "shipment_events.csv", [])

    assert list(iter_row_blocks(path, SHIPMENT_EVENTS, 5, ("event_id",))) == []


def test_row_blocks_reject_wrong_header(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "customers.csv", CUSTOMERS_BYTES)

    with pytest.raises(ValueError, match="distinta del contrato"):
        next(iter_row_blocks(path, SHIPMENT_EVENTS, 5, ("event_id",)))


def test_row_blocks_reject_bad_arguments(tmp_path: Path) -> None:
    path = write_events(tmp_path / "shipment_events.csv", ["EVT-1"])

    with pytest.raises(ValueError, match="positivo"):
        next(iter_row_blocks(path, SHIPMENT_EVENTS, 0, ("event_id",)))
    with pytest.raises(ValueError, match="fuera del contrato"):
        next(iter_row_blocks(path, SHIPMENT_EVENTS, 5, ("customer_id",)))
    with pytest.raises(ValueError, match="fuera del contrato"):
        next(iter_row_blocks(path, SHIPMENT_EVENTS, 5, ()))

"""Pruebas de los lectores de archivos: descubrimiento por tabla, hash y cabecera.

Sin base de datos: los archivos se crean en ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexora.ingestion.batch.loaders import CUSTOMERS
from nexora.ingestion.batch.readers import check_header, discover_files, file_sha256

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

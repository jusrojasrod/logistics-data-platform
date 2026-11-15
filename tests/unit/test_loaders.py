"""Pruebas unitarias del cargador batch: hash SHA-256 de archivo y decisión de
omitir un archivo ya cargado.

No usan base de datos: los archivos se crean en ``tmp_path`` y la decisión de
omitir se toma solo con la función pura ``should_skip``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nexora.ingestion.batch.loaders import should_skip
from nexora.ingestion.batch.readers import HASH_CHUNK_BYTES, file_sha256

SAMPLE_BYTES = b"shipment_id,customer_id\nSHP-000001,CUS-0001\n"

# Valores de referencia publicados de SHA-256 (FIPS 180-2).
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def write_bytes(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_hash_matches_hashlib_on_same_bytes(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "shipments.csv", SAMPLE_BYTES)

    assert file_sha256(path) == hashlib.sha256(SAMPLE_BYTES).hexdigest()


@pytest.mark.parametrize(
    ("content", "expected"),
    [(b"", EMPTY_SHA256), (b"abc", ABC_SHA256)],
    ids=["empty", "abc"],
)
def test_hash_matches_known_value(tmp_path: Path, content: bytes, expected: str) -> None:
    path = write_bytes(tmp_path / "file.csv", content)

    assert file_sha256(path) == expected


def test_hash_accepts_str_path(tmp_path: Path) -> None:
    path = write_bytes(tmp_path / "file.csv", b"abc")

    assert file_sha256(str(path)) == ABC_SHA256


def test_hash_spanning_several_chunks_matches_hashlib(tmp_path: Path) -> None:
    content = bytes(range(256)) * ((2 * HASH_CHUNK_BYTES) // 256 + 7)
    path = write_bytes(tmp_path / "large.csv", content)

    assert len(content) > 2 * HASH_CHUNK_BYTES
    assert file_sha256(path) == hashlib.sha256(content).hexdigest()


def test_hash_is_lowercase_hex_of_64_chars(tmp_path: Path) -> None:
    digest = file_sha256(write_bytes(tmp_path / "file.csv", SAMPLE_BYTES))

    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)


def test_same_content_different_name_gives_same_hash(tmp_path: Path) -> None:
    first = write_bytes(tmp_path / "shipments.csv", SAMPLE_BYTES)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    second = write_bytes(other_dir / "shipments_copy.csv", SAMPLE_BYTES)

    assert file_sha256(first) == file_sha256(second)


def test_one_different_byte_changes_hash(tmp_path: Path) -> None:
    changed = bytearray(SAMPLE_BYTES)
    changed[-2] ^= 0x01
    original = write_bytes(tmp_path / "original.csv", SAMPLE_BYTES)
    modified = write_bytes(tmp_path / "modified.csv", bytes(changed))

    assert len(changed) == len(SAMPLE_BYTES)
    assert file_sha256(original) != file_sha256(modified)


def test_should_skip_with_empty_collection_is_false() -> None:
    assert should_skip(ABC_SHA256, set()) is False
    assert should_skip(ABC_SHA256, ()) is False


def test_should_skip_when_hash_is_loaded_is_true() -> None:
    assert should_skip(ABC_SHA256, {ABC_SHA256}) is True
    assert should_skip(ABC_SHA256, [EMPTY_SHA256, ABC_SHA256]) is True


def test_should_skip_with_only_other_hashes_is_false() -> None:
    assert should_skip(ABC_SHA256, {EMPTY_SHA256}) is False
    assert should_skip(ABC_SHA256, [EMPTY_SHA256, "0" * 64]) is False


def test_directory_with_loaded_and_new_file_skips_only_loaded(tmp_path: Path) -> None:
    loaded_file = write_bytes(tmp_path / "customers.csv", b"customer_id\nCUS-0001\n")
    new_file = write_bytes(tmp_path / "carriers.csv", b"carrier_id\nCAR-001\n")
    loaded = {file_sha256(loaded_file)}

    decisions = {
        path.name: should_skip(file_sha256(path), loaded) for path in sorted(tmp_path.glob("*.csv"))
    }

    assert decisions == {"carriers.csv": False, "customers.csv": True}
    assert file_sha256(new_file) not in loaded


def test_renamed_copy_of_loaded_file_is_skipped(tmp_path: Path) -> None:
    loaded_file = write_bytes(tmp_path / "shipments.csv", SAMPLE_BYTES)
    renamed_copy = write_bytes(tmp_path / "shipments_resent.csv", SAMPLE_BYTES)
    loaded = {file_sha256(loaded_file)}

    assert should_skip(file_sha256(renamed_copy), loaded) is True

"""File helpers of the batch ingestion: content hash, CSV header and discovery.

A source file is identified by the SHA-256 of its bytes, not by its name, so a
renamed copy of a file has the same hash as the original. The header of a CSV
file must match the column order of its contract. :func:`discover_files` lists
the files of one table in a directory (``<table>*.csv``), sorted by name.

Nothing here touches the database.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Protocol

HASH_CHUNK_BYTES: int = 1024 * 1024


class HasColumnNames(Protocol):
    """A table contract that exposes its business column names in order."""

    @property
    def column_names(self) -> tuple[str, ...]: ...


def file_sha256(path: str | Path) -> str:
    """Return the SHA-256 hex digest of the file contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(path: str | Path) -> tuple[str, ...]:
    """Return the header row of a CSV file."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return tuple(next(csv.reader(handle), ()))


def check_header(table: HasColumnNames, path: str | Path) -> None:
    """Fail if the CSV header does not match the contract column order."""
    header = read_header(path)
    if header != table.column_names:
        raise ValueError(
            f"{Path(path).name}: cabecera {list(header)} distinta del contrato "
            f"{list(table.column_names)}"
        )


def discover_files(directory: str | Path, table: str) -> list[Path]:
    """Return the ``<table>*.csv`` files of ``directory``, sorted by name.

    Only regular files directly inside ``directory`` are returned. A missing
    directory raises :class:`FileNotFoundError`.
    """
    base = Path(directory)
    if not base.is_dir():
        raise FileNotFoundError(f"no existe la carpeta {base}")
    return sorted(
        (path for path in base.glob(f"{table}*.csv") if path.is_file()),
        key=lambda path: path.name,
    )

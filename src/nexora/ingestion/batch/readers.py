"""File helpers of the batch ingestion: content hash, CSV header and discovery.

A source file is identified by the SHA-256 of its bytes, not by its name, so a
renamed copy of a file has the same hash as the original. The header of a CSV
file must match the column order of its contract. :func:`discover_files` lists
the files of one table in a directory (``<table>*.csv``), sorted by name.
:func:`iter_row_blocks` reads a CSV file in blocks of bounded size that never
split consecutive rows sharing the same key, so the copies of a row written
right after it always travel in the same block.

Nothing here touches the database.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator, Sequence
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


def _check_columns(table: HasColumnNames, path: str | Path, header: tuple[str, ...]) -> None:
    if header != table.column_names:
        raise ValueError(
            f"{Path(path).name}: cabecera {list(header)} distinta del contrato "
            f"{list(table.column_names)}"
        )


def check_header(table: HasColumnNames, path: str | Path) -> None:
    """Fail if the CSV header does not match the contract column order."""
    _check_columns(table, path, read_header(path))


def iter_row_blocks(
    path: str | Path,
    contract: HasColumnNames,
    block_rows: int,
    key: Sequence[str],
) -> Iterator[list[dict[str, str]]]:
    """Yield the rows of a CSV file as lists of dicts, in file order.

    The header must match the contract column order. A block is closed once it
    holds at least ``block_rows`` rows and the next row has a different value
    of ``key`` than the last one, so a block is larger than ``block_rows`` only
    by the rows of the group that crosses the limit. An empty file yields
    nothing.
    """
    if block_rows < 1:
        raise ValueError(f"block_rows debe ser positivo: {block_rows}")
    missing = [column for column in key if column not in contract.column_names]
    if not key or missing:
        raise ValueError(f"clave {list(key)} fuera del contrato {list(contract.column_names)}")
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader, ()))
        _check_columns(contract, path, header)
        block: list[dict[str, str]] = []
        last_key: tuple[str, ...] | None = None
        for values in reader:
            row = dict(zip(header, values, strict=True))
            row_key = tuple(row[column] for column in key)
            if len(block) >= block_rows and row_key != last_key:
                yield block
                block = []
            block.append(row)
            last_key = row_key
        if block:
            yield block


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

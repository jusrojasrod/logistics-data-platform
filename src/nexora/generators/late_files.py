"""Split rule between the main ERP and CRM files and their late files.

A row belongs to the late file of its entity when the hash of its natural key
falls in the late share. The rule depends only on the key, so it is
deterministic, it does not consume any random draw and every row lives in a
single file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from nexora.generators.config import LATE_DIRECTORY, LATE_FILE_PCT, LATE_FILE_SUFFIX

# Separator between the columns of a composite natural key.
KEY_SEPARATOR: str = "|"


def is_late(key: str) -> bool:
    """Return whether the row with natural key ``key`` goes to the late file.

    The first 8 hex digits of the SHA-256 of ``key`` give an integer ``x``; the
    row is late when ``x % 100 < LATE_FILE_PCT``.
    """
    x = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    return x % 100 < LATE_FILE_PCT


def late_key(row: Mapping[str, str], columns: Sequence[str]) -> str:
    """Return the natural key of ``row``: its ``columns`` joined by ``|``."""
    if not columns:
        raise ValueError("a natural key needs at least one column")
    return KEY_SEPARATOR.join(row[column] for column in columns)


def late_file_name(entity: str) -> str:
    """Return the path of the late file of ``entity``, relative to the dataset."""
    return f"{LATE_DIRECTORY}/{entity}{LATE_FILE_SUFFIX}.csv"

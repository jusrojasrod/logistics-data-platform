"""Controlled defect injection and the deterministic defect manifest.

Defects are applied on top of the clean base rows of ``shipments.py``:

* ``delivered_before_pickup``: a delivered shipment whose ``delivered_at`` is
  moved before ``picked_up_at`` (DQ-002), the only impossible-date type;
* ``critical_null``: one null per affected row, split in equal thirds over
  ``promised_delivery_ts`` (DQ-011), ``customer_id`` (DQ-005) and
  ``carrier_id`` (DQ-004);
* ``duplicate``: byte-identical copies of clean base rows (DQ-001), placed right
  after their original and counted on top of the base.

Every defect lands on a different base row, so the counts of the manifest are
exact and each affected row carries a single defect. The manifest holds no
generation date, output path, version or run id: two runs with the same
arguments produce byte-identical files.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

from nexora.generators.config import (
    CRITICAL_NULL_COLUMNS,
    CRITICAL_NULL_RATE,
    CUTOFF_DATE,
    DELIVERED_BEFORE_PICKUP_RATE,
    DUPLICATE_RATE,
)

MANIFEST_FILE_NAME: str = "defect_manifest.json"

DUPLICATE_TYPE: str = "duplicate"
CRITICAL_NULL_TYPE: str = "critical_null"
DELIVERED_BEFORE_PICKUP_TYPE: str = "delivered_before_pickup"

QUARANTINE_ACTION: str = "quarantine"
ALERT_ACTION: str = "alert"

# Rule of the canonical catalog that detects each defect.
DUPLICATE_RULE_ID: str = "DQ-001"
DELIVERED_BEFORE_PICKUP_RULE_ID: str = "DQ-002"
CRITICAL_NULL_RULE_IDS: dict[str, str] = {
    "promised_delivery_ts": "DQ-011",
    "customer_id": "DQ-005",
    "carrier_id": "DQ-004",
}

# How far before the real pickup an injected delivery lands, in minutes.
DELIVERED_BEFORE_PICKUP_MINUTES: tuple[int, int] = (30, 2880)

# Decimal places of the ``rate`` field of the manifest.
RATE_DECIMALS: int = 5


@dataclass(frozen=True)
class DefectEntry:
    """One manifest entry: a defect type on a single column."""

    type: str
    column: str
    count: int
    rate: float
    rule_id: str
    expected_action: str

    def to_dict(self) -> dict[str, Any]:
        """Return the entry with the field order of the manifest schema."""
        return {
            "type": self.type,
            "column": self.column,
            "count": self.count,
            "rate": self.rate,
            "rule_id": self.rule_id,
            "expected_action": self.expected_action,
        }


@dataclass(frozen=True)
class InjectionResult:
    """Rows with the defects applied and the entries that describe them."""

    shipments: int
    rows: tuple[dict[str, str], ...]
    defects: tuple[DefectEntry, ...]


def defect_count(base_rows: int, rate: float) -> int:
    """Return the number of rows a rate affects on a base of ``base_rows``."""
    if base_rows < 0:
        raise ValueError(f"base_rows must not be negative: {base_rows}")
    return round(base_rows * rate)


def split_evenly(total: int, parts: int) -> tuple[int, ...]:
    """Split ``total`` into ``parts`` counts that differ by one at most.

    The remainder goes to the first parts, so 500 in thirds is 167, 167, 166.
    """
    if parts < 1:
        raise ValueError(f"parts must be positive: {parts}")
    quotient, remainder = divmod(total, parts)
    return tuple(quotient + (1 if index < remainder else 0) for index in range(parts))


def defect_rate(count: int, base_rows: int) -> float:
    """Return ``count / base_rows`` rounded as the manifest declares it."""
    if base_rows < 1:
        raise ValueError(f"base_rows must be positive: {base_rows}")
    return round(count / base_rows, RATE_DECIMALS)


def _format_ts(value: datetime) -> str:
    """Return an ISO 8601 timestamp with an explicit UTC offset."""
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _take(rng: Random, pool: list[int], count: int, label: str) -> list[int]:
    """Draw ``count`` indices from ``pool`` and remove them from it."""
    if count > len(pool):
        raise ValueError(f"{label}: {count} rows requested, only {len(pool)} available")
    chosen = sorted(rng.sample(pool, count))
    taken = set(chosen)
    pool[:] = [index for index in pool if index not in taken]
    return chosen


def _deliver_before_pickup(rng: Random, row: dict[str, str]) -> None:
    picked_up_at = datetime.fromisoformat(row["picked_up_at"])
    minutes = rng.randint(*DELIVERED_BEFORE_PICKUP_MINUTES)
    row["delivered_at"] = _format_ts(picked_up_at - timedelta(minutes=minutes))


def inject_defects(rows: Sequence[dict[str, str]], seed: int = 42) -> InjectionResult:
    """Apply the configured defects to ``rows`` and describe them.

    ``rows`` are the clean base rows; they are copied, never modified in place.
    The draw depends only on ``seed`` and the content of ``rows``.
    """
    base_rows = len(rows)
    if base_rows < 1:
        raise ValueError("rows must not be empty")

    rng = Random(f"defects-{seed}")
    output = [dict(row) for row in rows]

    delivered_pool = [index for index, row in enumerate(output) if row["delivered_at"]]
    before_pickup_count = defect_count(base_rows, DELIVERED_BEFORE_PICKUP_RATE)
    before_pickup = _take(rng, delivered_pool, before_pickup_count, DELIVERED_BEFORE_PICKUP_TYPE)
    for index in before_pickup:
        _deliver_before_pickup(rng, output[index])

    affected = set(before_pickup)
    clean_pool = [index for index in range(base_rows) if index not in affected]

    null_counts = split_evenly(
        defect_count(base_rows, CRITICAL_NULL_RATE), len(CRITICAL_NULL_COLUMNS)
    )
    null_entries: list[DefectEntry] = []
    for column, count in zip(CRITICAL_NULL_COLUMNS, null_counts, strict=True):
        for index in _take(rng, clean_pool, count, f"{CRITICAL_NULL_TYPE}:{column}"):
            output[index][column] = ""
        null_entries.append(
            DefectEntry(
                type=CRITICAL_NULL_TYPE,
                column=column,
                count=count,
                rate=defect_rate(count, base_rows),
                rule_id=CRITICAL_NULL_RULE_IDS[column],
                expected_action=ALERT_ACTION,
            )
        )

    duplicate_count = defect_count(base_rows, DUPLICATE_RATE)
    duplicated = set(_take(rng, clean_pool, duplicate_count, DUPLICATE_TYPE))
    with_duplicates: list[dict[str, str]] = []
    for index, row in enumerate(output):
        with_duplicates.append(row)
        if index in duplicated:
            with_duplicates.append(dict(row))

    defects = (
        DefectEntry(
            type=DUPLICATE_TYPE,
            column="shipment_id",
            count=duplicate_count,
            rate=defect_rate(duplicate_count, base_rows),
            rule_id=DUPLICATE_RULE_ID,
            expected_action=QUARANTINE_ACTION,
        ),
        *null_entries,
        DefectEntry(
            type=DELIVERED_BEFORE_PICKUP_TYPE,
            column="delivered_at",
            count=before_pickup_count,
            rate=defect_rate(before_pickup_count, base_rows),
            rule_id=DELIVERED_BEFORE_PICKUP_RULE_ID,
            expected_action=QUARANTINE_ACTION,
        ),
    )
    return InjectionResult(shipments=base_rows, rows=tuple(with_duplicates), defects=defects)


def build_manifest(
    scale: str,
    seed: int,
    months: int,
    result: InjectionResult,
    cutoff: date = CUTOFF_DATE,
) -> dict[str, Any]:
    """Return the manifest with the field order of the schema in the contract."""
    return {
        "scale": scale,
        "seed": seed,
        "months": months,
        "cutoff_date": cutoff.isoformat(),
        "shipments": result.shipments,
        "defects": [entry.to_dict() for entry in result.defects],
    }


def write_manifest(directory: str | Path, manifest: dict[str, Any]) -> Path:
    """Write ``defect_manifest.json`` into ``directory`` and return its path."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / MANIFEST_FILE_NAME
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path

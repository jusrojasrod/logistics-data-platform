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
exact and each affected row carries a single defect. The draw only needs to know
which base rows are delivered: ``plan_shipment_defects`` turns that mask into a
``ShipmentDefectPlan`` and ``apply_shipment_defects`` applies it to one row at a
time, so a stream of rows gets the same defects as ``inject_defects`` on the
whole list.

The operational tables get their own defect types, injected while the dataset
is written: ``impossible_date`` (an event a century after the real one),
``no_response`` (a tender without answer) and ``unclosed`` (an old incident
without resolution), plus duplicates and critical nulls of events and fuel
purchases. Every manifest entry names its table. The manifest holds no
generation date, output path, version or run id: two runs with the same
arguments produce byte-identical files.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
IMPOSSIBLE_DATE_TYPE: str = "impossible_date"
NO_RESPONSE_TYPE: str = "no_response"
UNCLOSED_TYPE: str = "unclosed"

SHIPMENTS_TABLE: str = "shipments"

QUARANTINE_ACTION: str = "quarantine"
ALERT_ACTION: str = "alert"

# Rule of the canonical catalog that detects each defect.
DUPLICATE_RULE_ID: str = "DQ-001"
DELIVERED_BEFORE_PICKUP_RULE_ID: str = "DQ-002"
NO_RESPONSE_RULE_ID: str = "DQ-014"
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
    """One manifest entry: a defect type on a single column of a table.

    ``rule_id`` and ``expected_action`` are None while the rule catalog has no
    rule for the defect.
    """

    table: str
    type: str
    column: str
    count: int
    rate: float
    rule_id: str | None
    expected_action: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return the entry with the field order of the manifest schema."""
        return {
            "table": self.table,
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


@dataclass(frozen=True)
class ShipmentDefectPlan:
    """The defects drawn for a population of base shipments, by row position.

    Positions are zero-based over the base rows, in generation order. Each
    position appears in one defect at most.
    """

    shipments: int
    before_pickup_minutes: Mapping[int, int]
    null_columns: Mapping[int, str]
    duplicates: frozenset[int]
    defects: tuple[DefectEntry, ...]


def plan_shipment_defects(delivered: Sequence[int], seed: int = 42) -> ShipmentDefectPlan:
    """Draw the shipment defects from the delivered mask of the base rows.

    ``delivered[i]`` is truthy when the base row at position ``i`` has a
    delivery timestamp. The draw depends only on ``seed`` and that mask.
    """
    base_rows = len(delivered)
    if base_rows < 1:
        raise ValueError("delivered must not be empty")

    rng = Random(f"defects-{seed}")

    delivered_pool = [index for index in range(base_rows) if delivered[index]]
    before_pickup_count = defect_count(base_rows, DELIVERED_BEFORE_PICKUP_RATE)
    before_pickup = _take(rng, delivered_pool, before_pickup_count, DELIVERED_BEFORE_PICKUP_TYPE)
    before_pickup_minutes = {
        index: rng.randint(*DELIVERED_BEFORE_PICKUP_MINUTES) for index in before_pickup
    }

    affected = set(before_pickup)
    clean_pool = [index for index in range(base_rows) if index not in affected]

    null_counts = split_evenly(
        defect_count(base_rows, CRITICAL_NULL_RATE), len(CRITICAL_NULL_COLUMNS)
    )
    null_columns: dict[int, str] = {}
    null_entries: list[DefectEntry] = []
    for column, count in zip(CRITICAL_NULL_COLUMNS, null_counts, strict=True):
        for index in _take(rng, clean_pool, count, f"{CRITICAL_NULL_TYPE}:{column}"):
            null_columns[index] = column
        null_entries.append(
            DefectEntry(
                table=SHIPMENTS_TABLE,
                type=CRITICAL_NULL_TYPE,
                column=column,
                count=count,
                rate=defect_rate(count, base_rows),
                rule_id=CRITICAL_NULL_RULE_IDS[column],
                expected_action=ALERT_ACTION,
            )
        )

    duplicate_count = defect_count(base_rows, DUPLICATE_RATE)
    duplicates = frozenset(_take(rng, clean_pool, duplicate_count, DUPLICATE_TYPE))

    defects = (
        DefectEntry(
            table=SHIPMENTS_TABLE,
            type=DUPLICATE_TYPE,
            column="shipment_id",
            count=duplicate_count,
            rate=defect_rate(duplicate_count, base_rows),
            rule_id=DUPLICATE_RULE_ID,
            expected_action=QUARANTINE_ACTION,
        ),
        *null_entries,
        DefectEntry(
            table=SHIPMENTS_TABLE,
            type=DELIVERED_BEFORE_PICKUP_TYPE,
            column="delivered_at",
            count=before_pickup_count,
            rate=defect_rate(before_pickup_count, base_rows),
            rule_id=DELIVERED_BEFORE_PICKUP_RULE_ID,
            expected_action=QUARANTINE_ACTION,
        ),
    )
    return ShipmentDefectPlan(
        shipments=base_rows,
        before_pickup_minutes=before_pickup_minutes,
        null_columns=null_columns,
        duplicates=duplicates,
        defects=defects,
    )


def apply_shipment_defects(
    index: int, row: dict[str, str], plan: ShipmentDefectPlan
) -> tuple[dict[str, str], ...]:
    """Return the output rows of the base row at position ``index``.

    ``row`` is copied, never modified in place. A duplicated row comes back
    twice, the copy right after the original.
    """
    output = dict(row)
    minutes = plan.before_pickup_minutes.get(index)
    if minutes is not None:
        picked_up_at = datetime.fromisoformat(output["picked_up_at"])
        output["delivered_at"] = _format_ts(picked_up_at - timedelta(minutes=minutes))
    column = plan.null_columns.get(index)
    if column is not None:
        output[column] = ""
    if index in plan.duplicates:
        return output, dict(output)
    return (output,)


def inject_defects(rows: Sequence[dict[str, str]], seed: int = 42) -> InjectionResult:
    """Apply the configured defects to ``rows`` and describe them.

    ``rows`` are the clean base rows; they are copied, never modified in place.
    The draw depends only on ``seed`` and the content of ``rows``.
    """
    if not rows:
        raise ValueError("rows must not be empty")

    plan = plan_shipment_defects(bytearray(1 if row["delivered_at"] else 0 for row in rows), seed)
    output = tuple(
        out_row
        for index, row in enumerate(rows)
        for out_row in apply_shipment_defects(index, row, plan)
    )
    return InjectionResult(shipments=plan.shipments, rows=output, defects=plan.defects)


def build_manifest(
    scale: str,
    seed: int,
    months: int,
    shipments: int,
    tables: Mapping[str, int],
    defects: Sequence[DefectEntry],
    cutoff: date = CUTOFF_DATE,
) -> dict[str, Any]:
    """Return the manifest with the field order of the schema in the contract.

    ``tables`` maps every table to its base rows, without injected copies, in
    the order given.
    """
    return {
        "scale": scale,
        "seed": seed,
        "months": months,
        "cutoff_date": cutoff.isoformat(),
        "shipments": shipments,
        "tables": dict(tables),
        "defects": [entry.to_dict() for entry in defects],
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

"""Streaming writer of the whole synthetic dataset.

``write_dataset`` produces every CSV of the source systems plus the defect
manifest without keeping lists of rows in memory. It walks the base shipments
twice with the same seed, so both walks see the same rows:

1. the first walk keeps only compact state: one byte per shipment telling
   whether it was delivered, which draws the shipment defects, and the trip
   keys of the owned fleet, which consolidate the trips;
2. trips, legs and fuel purchases are written from the trip plan;
3. the second walk writes ``shipments.csv`` with the shipment defects, the
   copies right after their original, and derives the stops, events, tenders,
   incidents and shipment legs from the clean base row.

The defects of the operational tables are drawn row by row, in writing order,
from a random generator of their own. Each affected row carries a single
defect, and the manifest declares the counts actually injected, with ``rate``
over the base rows of the table. Incidents are the exception: only those
reported at least ``UNCLOSED_MIN_AGE_DAYS`` days before the end of the cutoff
date draw the ``unclosed`` defect, and its ``rate`` is over them.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta
from operator import itemgetter
from pathlib import Path
from random import Random
from typing import IO

from nexora.generators.config import (
    EVENT_CRITICAL_NULL_COLUMNS,
    EVENT_CRITICAL_NULL_RATE,
    EVENT_DUPLICATE_RATE,
    EVENT_IMPOSSIBLE_DATE_RATE,
    FUEL_CRITICAL_NULL_COLUMNS,
    FUEL_CRITICAL_NULL_RATE,
    IMPOSSIBLE_DATE_SHIFT_DAYS,
    INCIDENT_UNCLOSED_RATE,
    TENDER_NO_RESPONSE_RATE,
    UNCLOSED_MIN_AGE_DAYS,
)
from nexora.generators.defects import (
    ALERT_ACTION,
    CRITICAL_NULL_TYPE,
    DUPLICATE_RULE_ID,
    DUPLICATE_TYPE,
    IMPOSSIBLE_DATE_TYPE,
    NO_RESPONSE_RULE_ID,
    NO_RESPONSE_TYPE,
    QUARANTINE_ACTION,
    UNCLOSED_TYPE,
    DefectEntry,
    apply_shipment_defects,
    build_manifest,
    defect_rate,
    plan_shipment_defects,
    write_manifest,
)
from nexora.generators.dimensions import (
    CARRIERS_ENTITY,
    ROUTES_ENTITY,
    SERVICE_TYPES_ENTITY,
    build_dimensions,
)
from nexora.generators.fleet import (
    DRIVERS_ENTITY,
    VEHICLES_ENTITY,
    build_drivers,
    build_vehicles,
)
from nexora.generators.operations import (
    CARRIER_INCIDENTS_COLUMNS,
    CARRIER_INCIDENTS_ENTITY,
    CARRIER_TENDERS_COLUMNS,
    CARRIER_TENDERS_ENTITY,
    FUEL_TRANSACTIONS_COLUMNS,
    FUEL_TRANSACTIONS_ENTITY,
    SHIPMENT_EVENTS_COLUMNS,
    SHIPMENT_EVENTS_ENTITY,
    SHIPMENT_LEGS_COLUMNS,
    SHIPMENT_LEGS_ENTITY,
    SHIPMENT_STOPS_COLUMNS,
    SHIPMENT_STOPS_ENTITY,
    TRIP_LEGS_COLUMNS,
    TRIP_LEGS_ENTITY,
    TRIPS_COLUMNS,
    TRIPS_ENTITY,
    EntityIds,
    TripPlanBuilder,
    build_trips,
    format_ts,
    shipment_entities,
)
from nexora.generators.shipments import (
    CUTOFF_END,
    SHIPMENTS_COLUMNS,
    SHIPMENTS_ENTITY,
    iter_shipments,
)

# Tables of the operational TMS, in publication and extraction order.
TMS_TABLE_ORDER: tuple[str, ...] = (
    CARRIERS_ENTITY,
    ROUTES_ENTITY,
    SERVICE_TYPES_ENTITY,
    SHIPMENTS_ENTITY,
    VEHICLES_ENTITY,
    DRIVERS_ENTITY,
    TRIPS_ENTITY,
    TRIP_LEGS_ENTITY,
    SHIPMENT_LEGS_ENTITY,
    SHIPMENT_STOPS_ENTITY,
    SHIPMENT_EVENTS_ENTITY,
    FUEL_TRANSACTIONS_ENTITY,
    CARRIER_TENDERS_ENTITY,
    CARRIER_INCIDENTS_ENTITY,
)

# Incidents reported before this instant are old enough to be expected closed.
UNCLOSED_REPORTED_BEFORE: datetime = CUTOFF_END - timedelta(days=UNCLOSED_MIN_AGE_DAYS)

# Write buffer of each CSV file, in bytes.
WRITE_BUFFER_BYTES: int = 1 << 20

NO_RESPONSE_COLUMNS: tuple[str, ...] = ("responded_ts", "response_status", "accepted_rate_amount")


@dataclass(frozen=True)
class DatasetSummary:
    """What ``write_dataset`` wrote.

    ``files`` holds the rows written per CSV file, injected copies included;
    ``tables`` the base rows of every TMS table, without copies.
    """

    directory: Path
    shipments: int
    files: Mapping[str, int]
    tables: Mapping[str, int]
    defects: tuple[DefectEntry, ...]
    manifest: Path


class _CsvSink:
    """One CSV file written row by row through a buffered writer."""

    def __init__(self, handle: IO[str], columns: tuple[str, ...]) -> None:
        self._writer = csv.writer(handle, lineterminator="\n")
        self._values = itemgetter(*columns)
        self._writer.writerow(columns)
        self.rows = 0

    def write(self, row: Mapping[str, str]) -> None:
        self._writer.writerow(self._values(row))
        self.rows += 1

    def write_all(self, rows: Iterable[Mapping[str, str]]) -> None:
        for row in rows:
            self.write(row)


def _open_sink(stack: ExitStack, directory: Path, name: str, columns: tuple[str, ...]) -> _CsvSink:
    path = directory / f"{name}.csv"
    handle = stack.enter_context(
        path.open("w", encoding="utf-8", newline="", buffering=WRITE_BUFFER_BYTES)
    )
    return _CsvSink(handle, columns)


def _rate(count: int, base_rows: int) -> float:
    return defect_rate(count, base_rows) if base_rows else 0.0


class EntityDefects:
    """Draws the defects of the operational tables, one row at a time.

    One uniform draw per base row, in writing order, falls into exclusive
    ranges, so a row gets one defect at most. Critical nulls alternate between
    the two columns of their table.
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng = Random(f"entity-defects-{seed}")
        self.events = 0
        self.fuel = 0
        self.tenders = 0
        self.incidents = 0
        self.eligible_incidents = 0
        self.event_duplicates = 0
        self.event_nulls = dict.fromkeys(EVENT_CRITICAL_NULL_COLUMNS, 0)
        self.event_impossible_dates = 0
        self.fuel_nulls = dict.fromkeys(FUEL_CRITICAL_NULL_COLUMNS, 0)
        self.tender_no_responses = 0
        self.unclosed_incidents = 0

    def event(self, row: dict[str, str]) -> tuple[dict[str, str], ...]:
        """Return the output rows of a base event: itself, a defective copy or two."""
        self.events += 1
        draw = self._rng.random()
        if draw < EVENT_DUPLICATE_RATE:
            self.event_duplicates += 1
            return row, dict(row)
        draw -= EVENT_DUPLICATE_RATE
        if draw < EVENT_CRITICAL_NULL_RATE:
            column = EVENT_CRITICAL_NULL_COLUMNS[
                sum(self.event_nulls.values()) % len(EVENT_CRITICAL_NULL_COLUMNS)
            ]
            self.event_nulls[column] += 1
            return ({**row, column: ""},)
        draw -= EVENT_CRITICAL_NULL_RATE
        if draw < EVENT_IMPOSSIBLE_DATE_RATE:
            self.event_impossible_dates += 1
            shifted = datetime.fromisoformat(row["event_ts"]) + timedelta(
                days=IMPOSSIBLE_DATE_SHIFT_DAYS
            )
            return ({**row, "event_ts": format_ts(shifted)},)
        return (row,)

    def fuel_transaction(self, row: dict[str, str]) -> dict[str, str]:
        """Return a base fuel purchase, with a critical null if one is drawn."""
        self.fuel += 1
        if self._rng.random() < FUEL_CRITICAL_NULL_RATE:
            column = FUEL_CRITICAL_NULL_COLUMNS[
                sum(self.fuel_nulls.values()) % len(FUEL_CRITICAL_NULL_COLUMNS)
            ]
            self.fuel_nulls[column] += 1
            return {**row, column: ""}
        return row

    def tender(self, row: dict[str, str]) -> dict[str, str]:
        """Return a base tender, without its answer if ``no_response`` is drawn."""
        self.tenders += 1
        if self._rng.random() < TENDER_NO_RESPONSE_RATE:
            self.tender_no_responses += 1
            return {**row, **dict.fromkeys(NO_RESPONSE_COLUMNS, "")}
        return row

    def incident(self, row: dict[str, str]) -> dict[str, str]:
        """Return a base incident; an old one may lose its resolution."""
        self.incidents += 1
        if datetime.fromisoformat(row["reported_ts"]) >= UNCLOSED_REPORTED_BEFORE:
            return row
        self.eligible_incidents += 1
        if self._rng.random() < INCIDENT_UNCLOSED_RATE:
            self.unclosed_incidents += 1
            return {**row, "resolved_ts": ""}
        return row

    def entries(self) -> tuple[DefectEntry, ...]:
        """Return the manifest entries of the injected defects, in a fixed order."""
        null_entries = tuple(
            DefectEntry(
                table=table,
                type=CRITICAL_NULL_TYPE,
                column=column,
                count=count,
                rate=_rate(count, base),
                rule_id=None,
                expected_action=None,
            )
            for table, nulls, base in (
                (SHIPMENT_EVENTS_ENTITY, self.event_nulls, self.events),
                (FUEL_TRANSACTIONS_ENTITY, self.fuel_nulls, self.fuel),
            )
            for column, count in nulls.items()
        )
        event_nulls = null_entries[: len(EVENT_CRITICAL_NULL_COLUMNS)]
        fuel_nulls = null_entries[len(EVENT_CRITICAL_NULL_COLUMNS) :]
        return (
            DefectEntry(
                table=SHIPMENT_EVENTS_ENTITY,
                type=DUPLICATE_TYPE,
                column="event_id",
                count=self.event_duplicates,
                rate=_rate(self.event_duplicates, self.events),
                rule_id=DUPLICATE_RULE_ID,
                expected_action=QUARANTINE_ACTION,
            ),
            *event_nulls,
            DefectEntry(
                table=SHIPMENT_EVENTS_ENTITY,
                type=IMPOSSIBLE_DATE_TYPE,
                column="event_ts",
                count=self.event_impossible_dates,
                rate=_rate(self.event_impossible_dates, self.events),
                rule_id=None,
                expected_action=None,
            ),
            *fuel_nulls,
            DefectEntry(
                table=CARRIER_TENDERS_ENTITY,
                type=NO_RESPONSE_TYPE,
                column="response_status",
                count=self.tender_no_responses,
                rate=_rate(self.tender_no_responses, self.tenders),
                rule_id=NO_RESPONSE_RULE_ID,
                expected_action=ALERT_ACTION,
            ),
            DefectEntry(
                table=CARRIER_INCIDENTS_ENTITY,
                type=UNCLOSED_TYPE,
                column="resolved_ts",
                count=self.unclosed_incidents,
                rate=_rate(self.unclosed_incidents, self.eligible_incidents),
                rule_id=None,
                expected_action=None,
            ),
        )


def write_dataset(
    out: str | Path, scale: str, shipments: int, seed: int, months: int
) -> DatasetSummary:
    """Write every CSV of the dataset and its manifest into ``out``.

    Memory holds one byte per base shipment, the shipment defect plan and the
    integer trip plan; it does not grow with the number of child rows.
    """
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, int] = {}
    tables: dict[str, int] = {}

    for table in (*build_dimensions(), build_vehicles(), build_drivers()):
        table.write_csv(directory)
        files[f"{table.name}.csv"] = len(table.rows)
        tables[table.name] = len(table.rows)

    # First walk: delivered mask and trip keys.
    delivered = bytearray()
    builder = TripPlanBuilder()
    for row in iter_shipments(shipments, seed, months):
        delivered.append(1 if row["delivered_at"] else 0)
        builder.add(row)
    shipment_plan = plan_shipment_defects(delivered, seed)
    trip_plan = builder.build(seed)
    del delivered, builder

    defects = EntityDefects(seed)

    with ExitStack() as stack:
        trips = _open_sink(stack, directory, TRIPS_ENTITY, TRIPS_COLUMNS)
        trip_legs = _open_sink(stack, directory, TRIP_LEGS_ENTITY, TRIP_LEGS_COLUMNS)
        fuel = _open_sink(stack, directory, FUEL_TRANSACTIONS_ENTITY, FUEL_TRANSACTIONS_COLUMNS)
        for record in build_trips(trip_plan):
            trips.write(record.trip)
            trip_legs.write_all(record.legs)
            for fuel_row in record.fuel:
                fuel.write(defects.fuel_transaction(fuel_row))
    trip_counts = {
        TRIPS_ENTITY: trips.rows,
        TRIP_LEGS_ENTITY: trip_legs.rows,
        FUEL_TRANSACTIONS_ENTITY: fuel.rows,
    }

    # Second walk: shipments with their defects and the children of each clean row.
    rng = Random(f"entities-{seed}")
    ids = EntityIds()
    with ExitStack() as stack:
        shipment_sink = _open_sink(stack, directory, SHIPMENTS_ENTITY, SHIPMENTS_COLUMNS)
        stops = _open_sink(stack, directory, SHIPMENT_STOPS_ENTITY, SHIPMENT_STOPS_COLUMNS)
        events = _open_sink(stack, directory, SHIPMENT_EVENTS_ENTITY, SHIPMENT_EVENTS_COLUMNS)
        tenders = _open_sink(stack, directory, CARRIER_TENDERS_ENTITY, CARRIER_TENDERS_COLUMNS)
        incidents = _open_sink(
            stack, directory, CARRIER_INCIDENTS_ENTITY, CARRIER_INCIDENTS_COLUMNS
        )
        shipment_legs = _open_sink(stack, directory, SHIPMENT_LEGS_ENTITY, SHIPMENT_LEGS_COLUMNS)
        for index, row in enumerate(iter_shipments(shipments, seed, months)):
            shipment_sink.write_all(apply_shipment_defects(index, row, shipment_plan))
            entities = shipment_entities(row, trip_plan, rng, ids)
            stops.write_all(entities.stops)
            for event in entities.events:
                events.write_all(defects.event(event))
            for tender in entities.tenders:
                tenders.write(defects.tender(tender))
            for incident in entities.incidents:
                incidents.write(defects.incident(incident))
            shipment_legs.write_all(entities.legs)
    child_files = {
        SHIPMENTS_ENTITY: shipment_sink.rows,
        SHIPMENT_STOPS_ENTITY: stops.rows,
        SHIPMENT_EVENTS_ENTITY: events.rows,
        CARRIER_TENDERS_ENTITY: tenders.rows,
        CARRIER_INCIDENTS_ENTITY: incidents.rows,
        SHIPMENT_LEGS_ENTITY: shipment_legs.rows,
    }
    for name, rows in (*trip_counts.items(), *child_files.items()):
        files[f"{name}.csv"] = rows

    tables.update(trip_counts)
    tables.update(
        {
            SHIPMENTS_ENTITY: shipment_plan.shipments,
            SHIPMENT_STOPS_ENTITY: stops.rows,
            SHIPMENT_EVENTS_ENTITY: defects.events,
            CARRIER_TENDERS_ENTITY: tenders.rows,
            CARRIER_INCIDENTS_ENTITY: incidents.rows,
            SHIPMENT_LEGS_ENTITY: shipment_legs.rows,
        }
    )
    ordered_tables = {name: tables[name] for name in TMS_TABLE_ORDER}

    entries = (*shipment_plan.defects, *defects.entries())
    manifest = write_manifest(
        directory,
        build_manifest(scale, seed, months, shipment_plan.shipments, ordered_tables, entries),
    )
    return DatasetSummary(
        directory=directory,
        shipments=shipment_plan.shipments,
        files=dict(sorted(files.items())),
        tables=ordered_tables,
        defects=entries,
        manifest=manifest,
    )

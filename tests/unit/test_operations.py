"""Pruebas de las reglas de entidades operativas: viajes, tramos, combustible,
paradas, eventos, tenders, incidencias y su relación con los envíos base."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from random import Random

import pytest

from nexora.generators.config import CUTOFF_DATE, FUEL_STOP_KM
from nexora.generators.dimensions import build_carriers, build_routes
from nexora.generators.operations import (
    CARRIER_INCIDENTS_COLUMNS,
    CARRIER_TENDERS_COLUMNS,
    FUEL_TRANSACTIONS_COLUMNS,
    SHIPMENT_EVENTS_COLUMNS,
    SHIPMENT_LEGS_COLUMNS,
    SHIPMENT_STOPS_COLUMNS,
    TRIP_LEGS_COLUMNS,
    TRIPS_COLUMNS,
    EntityIds,
    IncidentDraw,
    TripPlan,
    TripPlanBuilder,
    build_trips,
    incident_row,
    shipment_entities,
    trip_key,
    trip_schedule,
)
from nexora.generators.shipments import CUTOFF_END, iter_shipments

# Marcas observadas de cada tabla; planned_ts de las paradas es un plan y no cuenta.
OBSERVED_COLUMNS: dict[str, tuple[str, ...]] = {
    "trips": ("start_ts", "end_ts"),
    "trip_legs": ("departure_ts", "arrival_ts"),
    "fuel_transactions": ("txn_ts",),
    "shipment_stops": ("actual_ts",),
    "shipment_events": ("event_ts",),
    "carrier_tenders": ("offered_ts", "responded_ts"),
    "carrier_incidents": ("reported_ts", "responded_ts", "resolved_ts"),
}

COLUMNS: dict[str, tuple[str, ...]] = {
    "trips": TRIPS_COLUMNS,
    "trip_legs": TRIP_LEGS_COLUMNS,
    "fuel_transactions": FUEL_TRANSACTIONS_COLUMNS,
    "shipment_legs": SHIPMENT_LEGS_COLUMNS,
    "shipment_stops": SHIPMENT_STOPS_COLUMNS,
    "shipment_events": SHIPMENT_EVENTS_COLUMNS,
    "carrier_tenders": CARRIER_TENDERS_COLUMNS,
    "carrier_incidents": CARRIER_INCIDENTS_COLUMNS,
}


@dataclass
class Dataset:
    plan: TripPlan
    shipments: list[dict[str, str]]
    tables: dict[str, list[dict[str, str]]] = field(default_factory=dict)


def run(rows: list[dict[str, str]], seed: int = 42) -> Dataset:
    """Ejecuta las dos pasadas sobre envíos base y reúne las filas en listas."""
    builder = TripPlanBuilder()
    for row in rows:
        builder.add(row)
    plan = builder.build(seed)
    tables: dict[str, list[dict[str, str]]] = {name: [] for name in COLUMNS}
    for record in build_trips(plan):
        tables["trips"].append(record.trip)
        tables["trip_legs"].extend(record.legs)
        tables["fuel_transactions"].extend(record.fuel)
    rng = Random(f"entities-{seed}")
    ids = EntityIds()
    for row in rows:
        entities = shipment_entities(row, plan, rng, ids)
        tables["shipment_stops"].extend(entities.stops)
        tables["shipment_events"].extend(entities.events)
        tables["carrier_tenders"].extend(entities.tenders)
        tables["carrier_incidents"].extend(entities.incidents)
        tables["shipment_legs"].extend(entities.legs)
    return Dataset(plan=plan, shipments=rows, tables=tables)


@lru_cache(maxsize=4)
def generated(count: int, months: int) -> Dataset:
    return run(list(iter_shipments(count, 42, months)))


@lru_cache(maxsize=1)
def route_ids() -> dict[tuple[str, str], tuple[str, float]]:
    return {
        (row["origin_city"], row["destination_city"]): (row["route_id"], float(row["distance_km"]))
        for row in build_routes().rows
    }


def ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def make_row(
    number: int,
    origin: str,
    destination: str,
    day: date,
    carrier_id: str = "CAR-001",
    delivered: bool = True,
) -> dict[str, str]:
    """Envío base sintético con recogida a las 14:00 UTC del día indicado."""
    pickup = datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC)
    picked_up = pickup + timedelta(minutes=10)
    promised = pickup + timedelta(hours=72)
    delivered_at = pickup + timedelta(hours=30) if delivered else None
    if delivered_at is not None and delivered_at > CUTOFF_END:
        delivered_at = None
    return {
        "shipment_id": f"SHP-{number:06d}",
        "customer_id": "CUS-0001",
        "carrier_id": carrier_id,
        "route_id": route_ids()[(origin, destination)][0],
        "service_type_id": "SVC-STD",
        "pickup_ts": ts(pickup),
        "pickup_date": day.isoformat(),
        "promised_delivery_ts": ts(promised),
        "picked_up_at": ts(picked_up),
        "delivered_at": "" if delivered_at is None else ts(delivered_at),
        "ordered_qty": "10",
        "delivered_qty": "10" if delivered_at else "",
        "ordered_weight_kg": "120.500",
        "delivered_weight_kg": "120.500" if delivered_at else "",
        "definitive_failure_flag": "false",
    }


def test_no_trip_key_for_third_party_carriers() -> None:
    day = date(2025, 3, 3)
    carriers = build_carriers().rows
    for carrier in carriers:
        row = make_row(1, "Bogotá", "Cali", day, carrier["carrier_id"])
        key = trip_key(row)
        if carrier["carrier_type"] == "owned":
            assert isinstance(key, int)
        else:
            assert key is None
    third_party = make_row(1, "Bogotá", "Cali", day, "CAR-020")
    data = run([third_party])
    assert data.plan.trip_count == 0
    assert data.tables["trips"] == []
    assert data.tables["shipment_legs"] == []
    assert len(data.tables["carrier_tenders"]) >= 1

    owned = {row["carrier_id"] for row in carriers if row["carrier_type"] == "owned"}
    data = generated(2000, 24)
    carrier_of = {row["shipment_id"]: row["carrier_id"] for row in data.shipments}
    assert data.tables["shipment_legs"]
    assert {carrier_of[leg["shipment_id"]] for leg in data.tables["shipment_legs"]} <= owned
    assert {trip["carrier_id"] for trip in data.tables["trips"]} <= owned


DESTINATIONS: tuple[str, ...] = ("Cali", "Tunja", "Medellín", "Villavicencio", "Barranquilla")


def consolidated(day: date, origin: str, destinations: tuple[str, ...]) -> Dataset:
    rows = [
        make_row(number, origin, destination, day)
        for number, destination in enumerate(destinations, start=1)
    ]
    return run(rows)


def test_legs_sorted_by_distance_and_empty_return() -> None:
    data = consolidated(date(2025, 3, 3), "Bogotá", DESTINATIONS)
    delivery = [trip for trip in data.tables["trips"] if trip["trip_type"] == "delivery"]
    assert len(delivery) == 1
    trip_id = delivery[0]["trip_id"]
    legs = [leg for leg in data.tables["trip_legs"] if leg["trip_id"] == trip_id]
    assert [int(leg["leg_sequence"]) for leg in legs] == list(range(1, len(DESTINATIONS) + 2))
    loaded, back = legs[:-1], legs[-1]
    distances = [route_ids()[("Bogotá", leg["destination_city"])][1] for leg in loaded]
    assert distances == sorted(distances)
    assert {leg["destination_city"] for leg in loaded} == set(DESTINATIONS)
    assert all(leg["loaded_flag"] == "true" for leg in loaded)
    assert back["destination_city"] == "Bogotá"
    assert back["origin_city"] == loaded[-1]["destination_city"]
    assert back["loaded_flag"] == "false"
    for previous, current in zip(legs, legs[1:], strict=False):
        assert current["origin_city"] == previous["destination_city"]
        assert parse(current["departure_ts"]) > parse(previous["arrival_ts"])
    assert float(delivery[0]["empty_km"]) == pytest.approx(float(back["distance_km"]))
    assert float(delivery[0]["distance_km"]) == pytest.approx(
        sum(float(leg["distance_km"]) for leg in legs)
    )


def test_shipment_legs_are_legs_one_to_k_up_to_destination() -> None:
    data = consolidated(date(2025, 3, 3), "Bogotá", DESTINATIONS)
    trip_legs = {
        (leg["trip_id"], int(leg["leg_sequence"])): leg for leg in data.tables["trip_legs"]
    }
    route_of = {row["shipment_id"]: row["route_id"] for row in data.shipments}
    route_destination = {route_id: pair[1] for pair, (route_id, _) in route_ids().items()}
    for shipment in data.shipments:
        legs = [
            leg
            for leg in data.tables["shipment_legs"]
            if leg["shipment_id"] == shipment["shipment_id"]
        ]
        sequences = [int(leg["leg_sequence"]) for leg in legs]
        assert sequences == list(range(1, len(legs) + 1))
        last = trip_legs[(legs[-1]["trip_id"], sequences[-1])]
        assert last["destination_city"] == route_destination[route_of[shipment["shipment_id"]]]
        for leg in legs:
            trip_leg = trip_legs[(leg["trip_id"], int(leg["leg_sequence"]))]
            assert trip_leg["loaded_flag"] == "true"
            assert leg["loaded_km"] == trip_leg["distance_km"]
            assert leg["weight_kg"] == shipment["ordered_weight_kg"]
            assert float(leg["volume_m3"]) > 0
    counts = sorted(
        sum(1 for leg in data.tables["shipment_legs"] if leg["shipment_id"] == row["shipment_id"])
        for row in data.shipments
    )
    assert counts == list(range(1, len(DESTINATIONS) + 1))


def test_repositioning_trip_has_no_shipment_legs() -> None:
    data = generated(2000, 24)
    repositioning = [trip for trip in data.tables["trips"] if trip["trip_type"] == "repositioning"]
    assert repositioning
    ids = {trip["trip_id"] for trip in repositioning}
    assert ids.isdisjoint({leg["trip_id"] for leg in data.tables["shipment_legs"]})
    for trip in repositioning:
        legs = [leg for leg in data.tables["trip_legs"] if leg["trip_id"] == trip["trip_id"]]
        assert len(legs) == 1
        assert legs[0]["loaded_flag"] == "false"
        assert legs[0]["origin_city"] == trip["origin_city"]
        assert trip["empty_km"] == trip["distance_km"]
    numbers = sorted(int(trip["trip_id"][4:]) for trip in data.tables["trips"])
    assert numbers == list(range(1, data.plan.trip_count + 1))


def test_no_clean_observed_timestamp_after_cutoff() -> None:
    data = generated(3000, 1)
    for table, columns in OBSERVED_COLUMNS.items():
        assert data.tables[table], table
        for row in data.tables[table]:
            for column in columns:
                if row[column]:
                    assert parse(row[column]) <= CUTOFF_END, (table, column, row)
    # planned_ts es un plan: puede pasar del corte, como promised_delivery_ts.
    assert any(parse(stop["planned_ts"]) > CUTOFF_END for stop in data.tables["shipment_stops"])


FAR_DESTINATIONS: tuple[str, ...] = ("Santa Marta", "Cúcuta", "Cartagena", "Neiva", "Medellín")


def test_leg_departing_after_cutoff_is_omitted_with_shipment_legs_and_fuel() -> None:
    data = consolidated(CUTOFF_DATE, "Pasto", FAR_DESTINATIONS)
    key = trip_key(data.shipments[0])
    assert key is not None
    schedule = trip_schedule(data.plan, key)
    planned = schedule.legs
    kept = [leg for leg in planned if leg.departure <= CUTOFF_END]
    assert 0 < len(kept) < len(planned)

    trip_legs = [leg for leg in data.tables["trip_legs"] if leg["trip_id"] == schedule.trip_id]
    assert [int(leg["leg_sequence"]) for leg in trip_legs] == [leg.sequence for leg in kept]
    kept_sequences = {leg.sequence for leg in kept}
    shipment_legs = [
        leg for leg in data.tables["shipment_legs"] if leg["trip_id"] == schedule.trip_id
    ]
    assert {int(leg["leg_sequence"]) for leg in shipment_legs} <= kept_sequences
    full_legs = sum(
        len([leg for leg in planned if leg.loaded][: position + 1])
        for position in range(len(FAR_DESTINATIONS))
    )
    assert len(shipment_legs) < full_legs

    trip = next(trip for trip in data.tables["trips"] if trip["trip_id"] == schedule.trip_id)
    assert float(trip["distance_km"]) == pytest.approx(sum(leg.distance_km for leg in kept))
    fuel = [row for row in data.tables["fuel_transactions"] if row["trip_id"] == schedule.trip_id]
    planned_km = sum(leg.distance_km for leg in planned)
    planned_purchases = max(1, -(-planned_km // FUEL_STOP_KM))
    assert 0 < len(fuel) < planned_purchases
    for row in fuel:
        txn = parse(row["txn_ts"])
        assert parse(trip["start_ts"]) <= txn <= CUTOFF_END
        assert any(leg.departure <= txn <= leg.arrival for leg in kept), row


def incident(reported: datetime, responded: datetime, resolved: datetime) -> IncidentDraw:
    return IncidentDraw(
        incident_type="damage",
        severity="high",
        claim_amount=None,
        reported=reported,
        responded=responded,
        resolved=resolved,
    )


def test_incident_reported_or_responded_after_cutoff_is_omitted() -> None:
    row = make_row(1, "Bogotá", "Cali", CUTOFF_DATE)
    late = CUTOFF_END + timedelta(minutes=5)
    early = CUTOFF_END - timedelta(hours=2)
    assert incident_row("INC-0000001", row, incident(late, late, late)) is None
    assert incident_row("INC-0000001", row, incident(early, late, late)) is None
    kept = incident_row("INC-0000001", row, incident(early, early, early))
    assert kept is not None
    assert kept["carrier_id"] == row["carrier_id"]
    assert kept["damage_flag"] == "true"
    assert kept["claim_amount"] == ""

    data = generated(3000, 1)
    with_incident = {row["shipment_id"] for row in data.tables["carrier_incidents"]}
    damaged = {
        row["shipment_id"]
        for row in data.tables["carrier_incidents"]
        if row["incident_type"] == "damage"
    }
    damage_events = {
        event["shipment_id"]
        for event in data.tables["shipment_events"]
        if event["event_type"] == "damage"
    }
    assert damage_events == damaged
    assert damaged <= with_incident
    ids = [row["incident_id"] for row in data.tables["carrier_incidents"]]
    assert ids == [f"INC-{number:07d}" for number in range(1, len(ids) + 1)]


def test_nullable_timestamps_after_cutoff_are_null() -> None:
    row = make_row(1, "Bogotá", "Cali", CUTOFF_DATE)
    early = CUTOFF_END - timedelta(hours=2)
    kept = incident_row("INC-0000001", row, incident(early, early, CUTOFF_END + timedelta(days=1)))
    assert kept is not None
    assert kept["resolved_ts"] == ""

    data = consolidated(CUTOFF_DATE, "Pasto", FAR_DESTINATIONS)
    assert all(trip["end_ts"] == "" for trip in data.tables["trips"])
    for shipment in data.shipments:
        assert shipment["delivered_at"] == ""
    delivery_stops = [
        stop for stop in data.tables["shipment_stops"] if stop["stop_type"] == "delivery"
    ]
    assert delivery_stops
    assert all(stop["actual_ts"] == "" for stop in delivery_stops)

    data = generated(3000, 1)
    arrivals_after = 0
    for key, repositioning in data.plan.trips():
        schedule = trip_schedule(data.plan, key, repositioning)
        rows = {
            int(leg["leg_sequence"]): leg
            for leg in data.tables["trip_legs"]
            if leg["trip_id"] == schedule.trip_id
        }
        for leg in schedule.legs:
            if leg.departure > CUTOFF_END:
                assert leg.sequence not in rows
            elif leg.arrival > CUTOFF_END:
                arrivals_after += 1
                assert rows[leg.sequence]["arrival_ts"] == ""
            else:
                assert rows[leg.sequence]["arrival_ts"] == ts(leg.arrival)
    assert arrivals_after > 0
    assert any(trip["end_ts"] == "" for trip in data.tables["trips"])
    assert any(stop["actual_ts"] == "" for stop in data.tables["shipment_stops"])


def test_accepted_tender_uses_shipment_carrier_and_responds_before_pickup() -> None:
    data = generated(2000, 24)
    shipments = {row["shipment_id"]: row for row in data.shipments}
    by_shipment: dict[str, list[dict[str, str]]] = {}
    for tender in data.tables["carrier_tenders"]:
        by_shipment.setdefault(tender["shipment_id"], []).append(tender)
    assert set(by_shipment) == set(shipments)
    for shipment_id, tenders in by_shipment.items():
        shipment = shipments[shipment_id]
        pickup = parse(shipment["pickup_ts"])
        accepted = [tender for tender in tenders if tender["response_status"] == "accepted"]
        assert len(accepted) == 1
        assert accepted[0]["carrier_id"] == shipment["carrier_id"]
        assert accepted[0]["accepted_rate_amount"]
        assert tenders[-1] is accepted[0]
        for tender in tenders:
            offered = parse(tender["offered_ts"])
            responded = parse(tender["responded_ts"])
            assert pickup - timedelta(hours=48) <= offered <= pickup - timedelta(hours=2)
            assert offered < responded <= pickup
            if tender is not accepted[0]:
                assert tender["carrier_id"] != shipment["carrier_id"]
                assert tender["response_status"] in {"rejected", "expired"}
                assert tender["accepted_rate_amount"] == ""
                assert responded <= parse(accepted[0]["offered_ts"])
    ratio = len(data.tables["carrier_tenders"]) / len(shipments)
    assert 1.4 <= ratio <= 1.6


def test_stops_between_two_and_four() -> None:
    data = generated(2000, 24)
    by_shipment: dict[str, list[dict[str, str]]] = {}
    for stop in data.tables["shipment_stops"]:
        by_shipment.setdefault(stop["shipment_id"], []).append(stop)
    assert len(by_shipment) == len(data.shipments)
    for stops in by_shipment.values():
        assert 2 <= len(stops) <= 4
        assert [int(stop["stop_sequence"]) for stop in stops] == list(range(1, len(stops) + 1))
        assert stops[0]["stop_type"] == "pickup"
        assert stops[-1]["stop_type"] == "delivery"
        assert all(stop["stop_type"] == "transit_hub" for stop in stops[1:-1])
        planned = [parse(stop["planned_ts"]) for stop in stops]
        assert planned == sorted(planned)
    mean = len(data.tables["shipment_stops"]) / len(data.shipments)
    assert 2.85 <= mean <= 3.15


def test_columns_and_determinism() -> None:
    first = generated(2000, 24)
    for table, columns in COLUMNS.items():
        assert first.tables[table], table
        assert all(tuple(row) == columns for row in first.tables[table]), table
    second = run(list(iter_shipments(2000, 42, 24)))
    assert second.tables == first.tables
    events = first.tables["shipment_events"]
    assert [event["event_id"] for event in events] == [
        f"EVT-{number:09d}" for number in range(1, len(events) + 1)
    ]
    assert 9.5 <= len(events) / len(first.shipments) <= 10.5

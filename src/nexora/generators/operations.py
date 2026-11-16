"""Operational entities derived from the base shipments: trips and their children.

The rules of this module turn one clean base shipment, or one consolidated
trip, into the rows of the operational tables of the TMS. Nothing here keeps a
list of rows across shipments or trips: callers stream the base shipments and
write the rows as they come out.

Trips, legs and fuel purchases exist only for the owned fleet:

* the shipments of one owned carrier that leave the same origin city on the
  same pickup date travel together; the trip key packs those three values into
  one integer and the plan keeps, per key, a bit mask of the destination cities;
* a trip visits its destinations ordered by distance from the origin (ties by
  catalog position) and returns empty to the origin; a shipment rides the legs
  from the first one up to the leg that reaches its destination;
* after a loaded trip, a small share of trips adds an empty repositioning trip
  of a single leg, which carries no shipments;
* vehicle, driver, departure minute and dwell times come from a random
  generator seeded by the trip number, so the schedule of a trip is the same
  whether it is rebuilt for its own rows or for the legs of a shipment;
* fuel purchases are spread over the planned distance, one every
  ``FUEL_STOP_KM`` kilometres, and the odometer of each vehicle accumulates in
  trip order.

Stops, events, tenders and incidents exist for every base shipment and come
from a single sequential random generator, in shipment order.

Observed timestamps never pass ``CUTOFF_END``. A row whose not-null observed
timestamp would fall after it is not generated: an omitted leg takes its
shipment legs and its fuel purchases with it, and an omitted incident takes its
damage event. Nullable timestamps after the cutoff stay empty, because the
operation is still in progress. Planned timestamps of stops are a plan, like
``promised_delivery_ts``, and are kept as drawn.

Simplifications: a vehicle may run two trips at the same time, capacity is not
checked and the trip schedule is not reconciled with ``delivered_at``.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from random import Random

from nexora.generators.config import (
    CITY_CATALOG,
    CURRENCY_CODE,
    EVENTS_PER_SHIPMENT,
    FUEL_STOP_KM,
    INCIDENTS_PER_SHIPMENT,
    REPOSITIONING_TRIP_RATE,
    STOPS_PER_SHIPMENT,
    TENDERS_PER_SHIPMENT,
)
from nexora.generators.dimensions import build_carriers, build_routes
from nexora.generators.fleet import build_drivers, build_vehicles, owned_carrier_ids
from nexora.generators.shipments import CUTOFF_END, LOCAL_TIMEZONE

TRIPS_ENTITY: str = "trips"
TRIP_LEGS_ENTITY: str = "trip_legs"
SHIPMENT_LEGS_ENTITY: str = "shipment_legs"
SHIPMENT_STOPS_ENTITY: str = "shipment_stops"
SHIPMENT_EVENTS_ENTITY: str = "shipment_events"
FUEL_TRANSACTIONS_ENTITY: str = "fuel_transactions"
CARRIER_TENDERS_ENTITY: str = "carrier_tenders"
CARRIER_INCIDENTS_ENTITY: str = "carrier_incidents"

TRIPS_COLUMNS: tuple[str, ...] = (
    "trip_id",
    "carrier_id",
    "vehicle_id",
    "driver_id",
    "trip_type",
    "origin_city",
    "start_ts",
    "end_ts",
    "distance_km",
    "empty_km",
    "stopped_minutes",
    "fuel_gallons",
)

TRIP_LEGS_COLUMNS: tuple[str, ...] = (
    "trip_id",
    "leg_sequence",
    "origin_city",
    "destination_city",
    "distance_km",
    "departure_ts",
    "arrival_ts",
    "loaded_flag",
)

SHIPMENT_LEGS_COLUMNS: tuple[str, ...] = (
    "shipment_id",
    "trip_id",
    "leg_sequence",
    "loaded_km",
    "weight_kg",
    "volume_m3",
)

SHIPMENT_STOPS_COLUMNS: tuple[str, ...] = (
    "shipment_id",
    "stop_sequence",
    "stop_type",
    "city",
    "planned_ts",
    "actual_ts",
)

SHIPMENT_EVENTS_COLUMNS: tuple[str, ...] = (
    "event_id",
    "shipment_id",
    "event_ts",
    "event_type",
    "location_city",
    "latitude",
    "longitude",
    "severity",
)

FUEL_TRANSACTIONS_COLUMNS: tuple[str, ...] = (
    "fuel_txn_id",
    "trip_id",
    "vehicle_id",
    "driver_id",
    "txn_ts",
    "station_city",
    "gallons",
    "price_per_gallon_amount",
    "total_amount",
    "currency_code",
    "odometer_km",
)

CARRIER_TENDERS_COLUMNS: tuple[str, ...] = (
    "tender_id",
    "shipment_id",
    "carrier_id",
    "offered_ts",
    "responded_ts",
    "response_status",
    "offered_rate_amount",
    "accepted_rate_amount",
    "currency_code",
)

CARRIER_INCIDENTS_COLUMNS: tuple[str, ...] = (
    "incident_id",
    "shipment_id",
    "carrier_id",
    "incident_type",
    "severity",
    "damage_flag",
    "claim_amount",
    "currency_code",
    "reported_ts",
    "responded_ts",
    "resolved_ts",
)

DELIVERY_TRIP: str = "delivery"
REPOSITIONING_TRIP: str = "repositioning"

PICKUP_STOP: str = "pickup"
HUB_STOP: str = "transit_hub"
DELIVERY_STOP: str = "delivery"

ACCEPTED_STATUS: str = "accepted"
PRIOR_OFFER_STATUSES: tuple[str, ...] = ("rejected", "expired")

# Trips leave at 05:00 local time plus a random minute, and dwell at every
# destination before the next leg.
TRIP_START_LOCAL_HOUR: int = 5
TRIP_START_MINUTES: tuple[int, int] = (0, 59)
DWELL_MINUTES: tuple[int, int] = (30, 90)

# Odometer of each vehicle at the start of the history, spread by position.
ODOMETER_START_KM: float = 25_000.0
ODOMETER_STRIDE_KM: int = 7_919
ODOMETER_SPAN_KM: int = 180_000

# Fuel price per gallon by fuel type, in the operation currency.
FUEL_PRICE_PER_GALLON: dict[str, tuple[float, float]] = {
    "diesel": (10_200.0, 11_400.0),
    "natural_gas": (5_800.0, 6_600.0),
}

# A shipment is created between two and four days before its planned pickup,
# always before its first tender offer.
CREATED_LEAD_MINUTES: tuple[int, int] = (49 * 60, 96 * 60)

# Spread of the drawn number of events around EVENTS_PER_SHIPMENT. The total
# covers the fixed events of the timeline; the rest is filled with in-transit
# pings spread over the segments between hubs.
EVENTS_SPREAD: int = 3

# The out-for-delivery event happens shortly before the end of the timeline.
OUT_FOR_DELIVERY_MINUTES: tuple[int, int] = (30, 240)

# A shipment closed without delivery fails within this share of its promise.
FAILURE_TRANSIT_RANGE: tuple[float, float] = (0.62, 0.98)

# Without an outcome, the timeline of a shipment ends one day after the cutoff,
# so its last events fall after the cutoff and are not generated.
IN_TRANSIT_HORIZON: timedelta = timedelta(days=1)

# Every tender is offered between two and 48 hours before the planned pickup and
# answered before it.
TENDER_LEAD_MINUTES: tuple[int, int] = (120, 2880)
TENDER_RATE_PER_KM: tuple[float, float] = (3_800.0, 6_200.0)
TENDER_RATE_PER_KG: tuple[float, float] = (90.0, 180.0)
ACCEPTED_RATE_SHARE: tuple[float, float] = (0.94, 1.0)

# Incident types with their weight, the severities each one can take and
# whether it opens a claim.
INCIDENT_TYPES: tuple[tuple[str, float], ...] = (
    ("delay", 0.45),
    ("damage", 0.25),
    ("documentation", 0.15),
    ("vehicle_breakdown", 0.10),
    ("loss", 0.05),
)
INCIDENT_SEVERITIES: dict[str, tuple[str, ...]] = {
    "delay": ("low", "low", "medium"),
    "damage": ("medium", "high"),
    "documentation": ("low", "medium"),
    "vehicle_breakdown": ("medium", "high"),
    "loss": ("high",),
}
CLAIM_INCIDENT_TYPES: frozenset[str] = frozenset({"damage", "loss"})
CLAIM_RATE: float = 0.6
CLAIM_AMOUNT_PER_KG: tuple[float, float] = (8_000.0, 30_000.0)
INCIDENT_POSITION_RANGE: tuple[float, float] = (0.05, 0.95)
INCIDENT_RESPONSE_MINUTES: tuple[int, int] = (10, 1440)
INCIDENT_RESOLUTION_MINUTES: tuple[int, int] = (1 * 1440, 14 * 1440)

# Cargo density used to derive the volume of a shipment on a leg, in kg per m3.
CARGO_DENSITY_KG_M3: tuple[float, float] = (150.0, 450.0)

INFO_SEVERITY: str = "info"
WARNING_SEVERITY: str = "warning"
CRITICAL_SEVERITY: str = "critical"


def observed(value: datetime) -> bool:
    """Return whether an observed timestamp falls inside the generated history."""
    return value <= CUTOFF_END


def format_ts(value: datetime) -> str:
    """Return an ISO 8601 timestamp with an explicit UTC offset."""
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _optional_ts(value: datetime | None) -> str:
    """Return the timestamp, or an empty field if it is unknown or after the cutoff."""
    if value is None or not observed(value):
        return ""
    return format_ts(value)


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class _Network:
    """Catalog lookups shared by every rule, built once per process."""

    city_names: tuple[str, ...]
    route_cities: Mapping[str, tuple[int, int]]
    distance_km: tuple[tuple[float, ...], ...]
    hours: tuple[tuple[float, ...], ...]
    carrier_ids: tuple[str, ...]
    owned_index: Mapping[str, int]
    owned_ids: tuple[str, ...]
    vehicles: tuple[tuple[tuple[str, float, str], ...], ...]
    drivers: tuple[tuple[str, ...], ...]
    vehicle_position: Mapping[str, int]


@lru_cache(maxsize=1)
def _network() -> _Network:
    city_names = tuple(city.name for city in CITY_CATALOG)
    city_index = {name: index for index, name in enumerate(city_names)}
    size = len(city_names)
    distance = [[0.0] * size for _ in range(size)]
    hours = [[0.0] * size for _ in range(size)]
    route_cities: dict[str, tuple[int, int]] = {}
    for row in build_routes().rows:
        origin = city_index[row["origin_city"]]
        destination = city_index[row["destination_city"]]
        route_cities[row["route_id"]] = (origin, destination)
        distance[origin][destination] = float(row["distance_km"])
        hours[origin][destination] = float(row["estimated_hours"])
    owned_ids = owned_carrier_ids()
    owned_index = {carrier_id: index for index, carrier_id in enumerate(owned_ids)}
    vehicles: list[list[tuple[str, float, str]]] = [[] for _ in owned_ids]
    vehicle_position: dict[str, int] = {}
    for position, row in enumerate(build_vehicles().rows):
        vehicles[owned_index[row["carrier_id"]]].append(
            (row["vehicle_id"], float(row["km_per_gallon"]), row["fuel_type"])
        )
        vehicle_position[row["vehicle_id"]] = position
    drivers: list[list[str]] = [[] for _ in owned_ids]
    for row in build_drivers().rows:
        drivers[owned_index[row["carrier_id"]]].append(row["driver_id"])
    return _Network(
        city_names=city_names,
        route_cities=route_cities,
        distance_km=tuple(tuple(line) for line in distance),
        hours=tuple(tuple(line) for line in hours),
        carrier_ids=tuple(row["carrier_id"] for row in build_carriers().rows),
        owned_index=owned_index,
        owned_ids=owned_ids,
        vehicles=tuple(tuple(items) for items in vehicles),
        drivers=tuple(tuple(items) for items in drivers),
        vehicle_position=vehicle_position,
    )


def trip_key(row: Mapping[str, str]) -> int | None:
    """Return the integer trip key of a base shipment, or None for third parties.

    The key packs ``(pickup_date, carrier_id, origin_city)``, so sorting keys
    sorts trips by date, then owned carrier and then origin city position.
    """
    network = _network()
    carrier = network.owned_index.get(row["carrier_id"])
    if carrier is None:
        return None
    origin, _ = network.route_cities[row["route_id"]]
    day = date.fromisoformat(row["pickup_date"]).toordinal()
    return (day * len(network.owned_ids) + carrier) * len(network.city_names) + origin


def decode_trip_key(key: int) -> tuple[date, str, str]:
    """Return the pickup date, owned carrier id and origin city of a trip key."""
    network = _network()
    rest, origin = divmod(key, len(network.city_names))
    day, carrier = divmod(rest, len(network.owned_ids))
    return date.fromordinal(day), network.owned_ids[carrier], network.city_names[origin]


@dataclass(frozen=True)
class TripPlan:
    """The consolidated trips of a population, as integers only.

    ``masks`` holds the destination cities of each loaded trip as a bit mask,
    ``numbers`` the trip number of each key and ``repositioning`` the keys whose
    loaded trip is followed by a repositioning trip, numbered right after it.
    """

    seed: int
    masks: Mapping[int, int]
    numbers: Mapping[int, int]
    repositioning: frozenset[int]
    trip_count: int

    def trips(self) -> Iterator[tuple[int, bool]]:
        """Yield ``(key, is_repositioning)`` for every trip, in trip number order."""
        for key in sorted(self.numbers):
            yield key, False
            if key in self.repositioning:
                yield key, True


class TripPlanBuilder:
    """Accumulates the trip keys of a stream of base shipments."""

    def __init__(self) -> None:
        self._masks: dict[int, int] = {}

    def add(self, row: Mapping[str, str]) -> int | None:
        """Register a base shipment and return its trip key, if it has one."""
        key = trip_key(row)
        if key is None:
            return None
        _, destination = _network().route_cities[row["route_id"]]
        self._masks[key] = self._masks.get(key, 0) | (1 << destination)
        return key

    def build(self, seed: int = 42) -> TripPlan:
        """Number the trips in key order and draw the repositioning trips."""
        rng = Random(f"repositioning-{seed}")
        numbers: dict[int, int] = {}
        repositioning: set[int] = set()
        number = 0
        for key in sorted(self._masks):
            number += 1
            numbers[key] = number
            if rng.random() < REPOSITIONING_TRIP_RATE:
                repositioning.add(key)
                number += 1
        return TripPlan(
            seed=seed,
            masks=dict(self._masks),
            numbers=numbers,
            repositioning=frozenset(repositioning),
            trip_count=number,
        )


@dataclass(frozen=True)
class PlannedLeg:
    """A leg of a trip as planned, before the cutoff rules apply."""

    sequence: int
    origin: int
    destination: int
    distance_km: float
    departure: datetime
    arrival: datetime
    loaded: bool

    @property
    def generated(self) -> bool:
        """A leg exists in the source only if its departure is observed."""
        return observed(self.departure)


@dataclass(frozen=True)
class TripSchedule:
    """The full plan of one trip: fleet assignment, legs and dwell times."""

    trip_id: str
    number: int
    trip_type: str
    carrier_id: str
    vehicle_id: str
    driver_id: str
    km_per_gallon: float
    fuel_type: str
    origin: int
    start: datetime
    legs: tuple[PlannedLeg, ...]
    dwell_minutes: tuple[int, ...]

    @property
    def generated_legs(self) -> tuple[PlannedLeg, ...]:
        return tuple(leg for leg in self.legs if leg.generated)


def _trip_id(number: int) -> str:
    return f"TRP-{number:07d}"


def _destinations(mask: int, origin: int) -> list[int]:
    """Return the destination cities of a mask, nearest to the origin first."""
    network = _network()
    cities = [index for index in range(len(network.city_names)) if mask >> index & 1]
    return sorted(cities, key=lambda index: (network.distance_km[origin][index], index))


def _schedule(plan: TripPlan, key: int, repositioning: bool) -> tuple[TripSchedule, Random]:
    network = _network()
    if key not in plan.numbers:
        raise KeyError(f"trip key not in plan: {key}")
    number = plan.numbers[key] + (1 if repositioning else 0)
    rng = Random(f"trip-{plan.seed}-{number}")
    day, carrier_id, _ = decode_trip_key(key)
    origin = key % len(network.city_names)
    carrier = network.owned_index[carrier_id]
    vehicle_id, km_per_gallon, fuel_type = rng.choice(network.vehicles[carrier])
    driver_id = rng.choice(network.drivers[carrier])
    start = datetime.combine(
        day,
        time(TRIP_START_LOCAL_HOUR, rng.randint(*TRIP_START_MINUTES)),
        tzinfo=LOCAL_TIMEZONE,
    ).astimezone(UTC)

    if repositioning:
        candidates = [index for index in range(len(network.city_names)) if index != origin]
        path = [origin, rng.choice(candidates)]
        loaded = [False]
    else:
        path = [origin, *_destinations(plan.masks[key], origin), origin]
        loaded = [True] * (len(path) - 2) + [False]

    legs: list[PlannedLeg] = []
    dwell: list[int] = []
    departure = start
    for position, (leg_origin, leg_destination) in enumerate(zip(path, path[1:], strict=False)):
        arrival = departure + timedelta(hours=network.hours[leg_origin][leg_destination])
        legs.append(
            PlannedLeg(
                sequence=position + 1,
                origin=leg_origin,
                destination=leg_destination,
                distance_km=network.distance_km[leg_origin][leg_destination],
                departure=departure,
                arrival=arrival,
                loaded=loaded[position],
            )
        )
        if position < len(path) - 2:
            minutes = rng.randint(*DWELL_MINUTES)
            dwell.append(minutes)
            departure = arrival + timedelta(minutes=minutes)

    schedule = TripSchedule(
        trip_id=_trip_id(number),
        number=number,
        trip_type=REPOSITIONING_TRIP if repositioning else DELIVERY_TRIP,
        carrier_id=carrier_id,
        vehicle_id=vehicle_id,
        driver_id=driver_id,
        km_per_gallon=km_per_gallon,
        fuel_type=fuel_type,
        origin=origin,
        start=start,
        legs=tuple(legs),
        dwell_minutes=tuple(dwell),
    )
    return schedule, rng


def trip_schedule(plan: TripPlan, key: int, repositioning: bool = False) -> TripSchedule:
    """Return the schedule of the loaded trip of ``key`` or of its repositioning trip."""
    return _schedule(plan, key, repositioning)[0]


def _shipment_trip(
    row: Mapping[str, str], plan: TripPlan
) -> tuple[str, tuple[PlannedLeg, ...]] | None:
    """Return the trip id and the planned legs of a base shipment, if it has a trip."""
    key = trip_key(row)
    if key is None:
        return None
    _, destination = _network().route_cities[row["route_id"]]
    schedule = trip_schedule(plan, key)
    for position, leg in enumerate(schedule.legs):
        if leg.loaded and leg.destination == destination:
            return schedule.trip_id, schedule.legs[: position + 1]
    raise ValueError(f"{row['shipment_id']}: destination not in its trip")


def trip_legs_for(row: Mapping[str, str], plan: TripPlan) -> tuple[PlannedLeg, ...]:
    """Return the planned legs a base shipment rides: 1 to k, k reaching its destination.

    Third party shipments ride no leg of the owned fleet.
    """
    trip = _shipment_trip(row, plan)
    return () if trip is None else trip[1]


@dataclass(frozen=True)
class TripRecord:
    """The rows of one trip: the trip, its generated legs and its fuel purchases."""

    trip: dict[str, str]
    legs: tuple[dict[str, str], ...]
    fuel: tuple[dict[str, str], ...]


def _odometer_start(vehicle_id: str) -> float:
    position = _network().vehicle_position[vehicle_id]
    return ODOMETER_START_KM + (position * ODOMETER_STRIDE_KM) % ODOMETER_SPAN_KM


def _leg_row(schedule: TripSchedule, leg: PlannedLeg) -> dict[str, str]:
    names = _network().city_names
    return {
        "trip_id": schedule.trip_id,
        "leg_sequence": str(leg.sequence),
        "origin_city": names[leg.origin],
        "destination_city": names[leg.destination],
        "distance_km": f"{leg.distance_km:.1f}",
        "departure_ts": format_ts(leg.departure),
        "arrival_ts": _optional_ts(leg.arrival),
        "loaded_flag": "true" if leg.loaded else "false",
    }


def _fuel_rows(
    schedule: TripSchedule, rng: Random, odometer_km: float, first_number: int
) -> list[dict[str, str]]:
    """Return the observed fuel purchases of a trip, spread over its planned distance."""
    names = _network().city_names
    planned_km = sum(leg.distance_km for leg in schedule.legs)
    purchases = max(1, math.ceil(planned_km / FUEL_STOP_KM))
    segment_km = planned_km / purchases
    price_range = FUEL_PRICE_PER_GALLON[schedule.fuel_type]
    rows: list[dict[str, str]] = []
    leg_index = 0
    leg_start_km = 0.0
    for purchase in range(purchases):
        position_km = purchase * segment_km
        while (
            leg_index < len(schedule.legs) - 1
            and position_km >= leg_start_km + schedule.legs[leg_index].distance_km
        ):
            leg_start_km += schedule.legs[leg_index].distance_km
            leg_index += 1
        leg = schedule.legs[leg_index]
        fraction = (position_km - leg_start_km) / leg.distance_km if leg.distance_km else 0.0
        txn_ts = leg.departure + (leg.arrival - leg.departure) * fraction
        if not observed(txn_ts):
            break
        gallons = round(segment_km / schedule.km_per_gallon, 2)
        price = round(rng.uniform(*price_range), 2)
        rows.append(
            {
                "fuel_txn_id": f"FUE-{first_number + len(rows):08d}",
                "trip_id": schedule.trip_id,
                "vehicle_id": schedule.vehicle_id,
                "driver_id": schedule.driver_id,
                "txn_ts": format_ts(txn_ts),
                "station_city": names[leg.origin if fraction < 0.5 else leg.destination],
                "gallons": f"{gallons:.2f}",
                "price_per_gallon_amount": f"{price:.2f}",
                "total_amount": f"{round(gallons * price, 2):.2f}",
                "currency_code": CURRENCY_CODE,
                "odometer_km": f"{odometer_km + position_km:.1f}",
            }
        )
    return rows


def trip_record(
    schedule: TripSchedule, rng: Random, odometer_km: float, first_fuel_number: int
) -> TripRecord | None:
    """Return the rows of a trip, or None if it starts after the cutoff."""
    if not observed(schedule.start):
        return None
    generated = schedule.generated_legs
    complete = len(generated) == len(schedule.legs)
    distance_km = round(sum(leg.distance_km for leg in generated), 1)
    empty_km = round(sum(leg.distance_km for leg in generated if not leg.loaded), 1)
    stopped = sum(
        minutes
        for position, minutes in enumerate(schedule.dwell_minutes)
        if schedule.legs[position + 1].generated
    )
    last_arrival = schedule.legs[-1].arrival
    trip = {
        "trip_id": schedule.trip_id,
        "carrier_id": schedule.carrier_id,
        "vehicle_id": schedule.vehicle_id,
        "driver_id": schedule.driver_id,
        "trip_type": schedule.trip_type,
        "origin_city": _network().city_names[schedule.origin],
        "start_ts": format_ts(schedule.start),
        "end_ts": _optional_ts(last_arrival) if complete else "",
        "distance_km": f"{distance_km:.1f}",
        "empty_km": f"{empty_km:.1f}",
        "stopped_minutes": str(stopped),
        "fuel_gallons": f"{round(distance_km / schedule.km_per_gallon, 2):.2f}",
    }
    return TripRecord(
        trip=trip,
        legs=tuple(_leg_row(schedule, leg) for leg in generated),
        fuel=tuple(_fuel_rows(schedule, rng, odometer_km, first_fuel_number)),
    )


def build_trips(plan: TripPlan) -> Iterator[TripRecord]:
    """Yield the rows of every trip of the plan, in trip number order.

    Fuel purchases are numbered in that order and the odometer of each vehicle
    advances by the generated distance of its trips.
    """
    odometer: dict[str, float] = {}
    next_fuel = 1
    for key, repositioning in plan.trips():
        schedule, rng = _schedule(plan, key, repositioning)
        start_km = odometer.get(schedule.vehicle_id)
        if start_km is None:
            start_km = _odometer_start(schedule.vehicle_id)
        record = trip_record(schedule, rng, start_km, next_fuel)
        if record is None:
            continue
        next_fuel += len(record.fuel)
        odometer[schedule.vehicle_id] = start_km + float(record.trip["distance_km"])
        yield record


@dataclass
class EntityIds:
    """Running counters of the natural ids of the shipment children."""

    event: int = 0
    tender: int = 0
    incident: int = 0

    def next_event(self) -> str:
        self.event += 1
        return f"EVT-{self.event:09d}"

    def next_tender(self) -> str:
        self.tender += 1
        return f"TND-{self.tender:08d}"

    def next_incident(self) -> str:
        self.incident += 1
        return f"INC-{self.incident:07d}"


@dataclass(frozen=True)
class IncidentDraw:
    """An incident as drawn, before the cutoff rules apply."""

    incident_type: str
    severity: str
    claim_amount: float | None
    reported: datetime
    responded: datetime
    resolved: datetime

    @property
    def damage(self) -> bool:
        return self.incident_type == "damage"

    @property
    def generated(self) -> bool:
        """An incident exists only if it was reported and answered before the cutoff."""
        return observed(self.reported) and observed(self.responded)


def incident_row(
    incident_id: str, row: Mapping[str, str], draw: IncidentDraw
) -> dict[str, str] | None:
    """Return the incident row of a base shipment, or None if it is not generated."""
    if not draw.generated:
        return None
    return {
        "incident_id": incident_id,
        "shipment_id": row["shipment_id"],
        "carrier_id": row["carrier_id"],
        "incident_type": draw.incident_type,
        "severity": draw.severity,
        "damage_flag": "true" if draw.damage else "false",
        "claim_amount": "" if draw.claim_amount is None else f"{draw.claim_amount:.2f}",
        "currency_code": CURRENCY_CODE,
        "reported_ts": format_ts(draw.reported),
        "responded_ts": format_ts(draw.responded),
        "resolved_ts": _optional_ts(draw.resolved),
    }


def _draw_incident(rng: Random, weight_kg: float, start: datetime, end: datetime) -> IncidentDraw:
    names = tuple(name for name, _ in INCIDENT_TYPES)
    weights = tuple(weight for _, weight in INCIDENT_TYPES)
    incident_type = rng.choices(names, weights=weights)[0]
    severity = rng.choice(INCIDENT_SEVERITIES[incident_type])
    claim_amount = None
    if incident_type in CLAIM_INCIDENT_TYPES and rng.random() < CLAIM_RATE:
        claim_amount = round(weight_kg * rng.uniform(*CLAIM_AMOUNT_PER_KG), 2)
    reported = start + (end - start) * rng.uniform(*INCIDENT_POSITION_RANGE)
    reported = reported.replace(microsecond=0)
    responded = reported + timedelta(minutes=rng.randint(*INCIDENT_RESPONSE_MINUTES))
    resolved = reported + timedelta(minutes=rng.randint(*INCIDENT_RESOLUTION_MINUTES))
    return IncidentDraw(
        incident_type=incident_type,
        severity=severity,
        claim_amount=claim_amount,
        reported=reported,
        responded=responded,
        resolved=resolved,
    )


@dataclass(frozen=True)
class ShipmentEntities:
    """The child rows of one base shipment."""

    stops: tuple[dict[str, str], ...]
    events: tuple[dict[str, str], ...]
    tenders: tuple[dict[str, str], ...]
    incidents: tuple[dict[str, str], ...]
    legs: tuple[dict[str, str], ...] = field(default=())


def _event(
    event_type: str, city: int, ts: datetime, severity: str = INFO_SEVERITY
) -> tuple[datetime, str, int, str]:
    return ts.replace(microsecond=0), event_type, city, severity


def shipment_entities(
    row: Mapping[str, str], plan: TripPlan, rng: Random, ids: EntityIds
) -> ShipmentEntities:
    """Return the stops, events, tenders, incidents and legs of a clean base shipment.

    ``rng`` is shared by the whole stream and consumed in a fixed order, so the
    same shipments in the same order give the same rows. ``ids`` numbers the
    events, tenders and incidents across the stream.
    """
    network = _network()
    origin, destination = network.route_cities[row["route_id"]]
    pickup_ts = _parse_ts(row["pickup_ts"])
    picked_up_at = _parse_ts(row["picked_up_at"])
    promised = _parse_ts(row["promised_delivery_ts"])
    delivered_at = _parse_ts(row["delivered_at"]) if row["delivered_at"] else None
    failed = row["definitive_failure_flag"] == "true"
    weight_kg = float(row["ordered_weight_kg"])

    # Transit hubs between pickup and delivery.
    max_hubs = 2 * (STOPS_PER_SHIPMENT - 2)
    hub_count = rng.randint(0, max_hubs)
    candidates = [
        index for index in range(len(network.city_names)) if index not in (origin, destination)
    ]
    hubs = rng.sample(candidates, hub_count)

    # End of the timeline: delivery, failure or a horizon past the cutoff.
    closed = delivered_at is not None or failed
    if delivered_at is not None:
        end = delivered_at
    elif failed:
        end = picked_up_at + (promised - pickup_ts) * rng.uniform(*FAILURE_TRANSIT_RANGE)
        end = max(picked_up_at, min(end, CUTOFF_END))
    else:
        end = max(promised, CUTOFF_END + IN_TRANSIT_HORIZON)
    created = pickup_ts - timedelta(minutes=rng.randint(*CREATED_LEAD_MINUTES))

    incident = None
    if rng.random() < INCIDENTS_PER_SHIPMENT:
        incident = _draw_incident(rng, weight_kg, picked_up_at, end)

    # Event timeline.
    total = rng.randint(EVENTS_PER_SHIPMENT - EVENTS_SPREAD, EVENTS_PER_SHIPMENT + EVENTS_SPREAD)
    fixed = 2 + 2 * hub_count + (2 if closed else 0)
    fillers = [0] * (hub_count + 1)
    for _ in range(max(0, total - fixed)):
        fillers[rng.randint(0, hub_count)] += 1
    nodes = [origin, *hubs]
    middle: list[tuple[str, int]] = []
    for segment, count in enumerate(fillers):
        if segment > 0:
            middle.append(("arrived_at_hub", nodes[segment]))
            middle.append(("departed_hub", nodes[segment]))
        middle.extend(("in_transit", nodes[segment]) for _ in range(count))
    middle_end = end
    if closed:
        span_minutes = (end - picked_up_at).total_seconds() / 60
        lead = min(rng.randint(*OUT_FOR_DELIVERY_MINUTES), span_minutes / 2)
        middle_end = end - timedelta(minutes=lead)
    fractions = sorted(rng.random() for _ in middle)
    timeline = [_event("created", origin, created), _event("picked_up", origin, picked_up_at)]
    hub_arrivals: list[datetime] = []
    for (event_type, city), fraction in zip(middle, fractions, strict=True):
        ts = picked_up_at + (middle_end - picked_up_at) * fraction
        if event_type == "arrived_at_hub":
            hub_arrivals.append(ts.replace(microsecond=0))
        timeline.append(_event(event_type, city, ts))
    if closed:
        timeline.append(_event("out_for_delivery", destination, middle_end))
        if delivered_at is not None:
            timeline.append(_event("delivered", destination, end))
        else:
            timeline.append(_event("delivery_failed", destination, end, WARNING_SEVERITY))

    incidents: tuple[dict[str, str], ...] = ()
    incident_out = None
    if incident is not None and incident.generated:
        incident_out = incident_row(ids.next_incident(), row, incident)
    if incident is not None and incident_out is not None:
        incidents = (incident_out,)
        if incident.damage:
            span = end - picked_up_at
            position = (incident.reported - picked_up_at) / span if span else 0.0
            severity = CRITICAL_SEVERITY if incident.severity == "high" else WARNING_SEVERITY
            city = origin if position < 0.5 else destination
            timeline.append(_event("damage", city, incident.reported, severity))
    timeline.sort(key=lambda item: item[0])

    events = []
    for ts, event_type, city, severity in timeline:
        if not observed(ts):
            continue
        location = CITY_CATALOG[city]
        events.append(
            {
                "event_id": ids.next_event(),
                "shipment_id": row["shipment_id"],
                "event_ts": format_ts(ts),
                "event_type": event_type,
                "location_city": location.name,
                "latitude": f"{location.latitude:.6f}",
                "longitude": f"{location.longitude:.6f}",
                "severity": severity,
            }
        )

    # Stops: pickup, hubs and delivery. Planned times follow the promise.
    stops = [
        {
            "shipment_id": row["shipment_id"],
            "stop_sequence": "1",
            "stop_type": PICKUP_STOP,
            "city": network.city_names[origin],
            "planned_ts": format_ts(pickup_ts),
            "actual_ts": _optional_ts(picked_up_at),
        }
    ]
    for position, hub in enumerate(hubs):
        planned = pickup_ts + (promised - pickup_ts) * (position + 1) / (hub_count + 1)
        stops.append(
            {
                "shipment_id": row["shipment_id"],
                "stop_sequence": str(position + 2),
                "stop_type": HUB_STOP,
                "city": network.city_names[hub],
                "planned_ts": format_ts(planned),
                "actual_ts": _optional_ts(hub_arrivals[position]),
            }
        )
    stops.append(
        {
            "shipment_id": row["shipment_id"],
            "stop_sequence": str(hub_count + 2),
            "stop_type": DELIVERY_STOP,
            "city": network.city_names[destination],
            "planned_ts": format_ts(promised),
            "actual_ts": _optional_ts(delivered_at),
        }
    )

    # Tenders: an optional previous offer to another carrier, then the accepted one.
    distance_km = network.distance_km[origin][destination]
    prior = rng.random() < TENDERS_PER_SHIPMENT - 1
    if prior:
        prior_lead, accepted_lead = sorted(
            rng.sample(range(TENDER_LEAD_MINUTES[0], TENDER_LEAD_MINUTES[1] + 1), 2),
            reverse=True,
        )
    else:
        prior_lead, accepted_lead = 0, rng.randint(*TENDER_LEAD_MINUTES)
    tenders = []
    if prior:
        others = [carrier for carrier in network.carrier_ids if carrier != row["carrier_id"]]
        offered = pickup_ts - timedelta(minutes=prior_lead)
        responded = offered + timedelta(minutes=rng.randint(1, prior_lead - accepted_lead))
        rate = distance_km * rng.uniform(*TENDER_RATE_PER_KM) + weight_kg * rng.uniform(
            *TENDER_RATE_PER_KG
        )
        status = rng.choice(PRIOR_OFFER_STATUSES)
        carrier = rng.choice(others)
        if observed(offered):
            tenders.append(
                {
                    "tender_id": ids.next_tender(),
                    "shipment_id": row["shipment_id"],
                    "carrier_id": carrier,
                    "offered_ts": format_ts(offered),
                    "responded_ts": format_ts(responded),
                    "response_status": status,
                    "offered_rate_amount": f"{rate:.2f}",
                    "accepted_rate_amount": "",
                    "currency_code": CURRENCY_CODE,
                }
            )
    offered = pickup_ts - timedelta(minutes=accepted_lead)
    responded = offered + timedelta(minutes=rng.randint(1, accepted_lead))
    rate = distance_km * rng.uniform(*TENDER_RATE_PER_KM) + weight_kg * rng.uniform(
        *TENDER_RATE_PER_KG
    )
    accepted_rate = rate * rng.uniform(*ACCEPTED_RATE_SHARE)
    if observed(offered):
        tenders.append(
            {
                "tender_id": ids.next_tender(),
                "shipment_id": row["shipment_id"],
                "carrier_id": row["carrier_id"],
                "offered_ts": format_ts(offered),
                "responded_ts": format_ts(responded),
                "response_status": ACCEPTED_STATUS,
                "offered_rate_amount": f"{rate:.2f}",
                "accepted_rate_amount": f"{accepted_rate:.2f}",
                "currency_code": CURRENCY_CODE,
            }
        )

    # Shipment legs: the generated legs of its trip up to its destination.
    legs = []
    trip = _shipment_trip(row, plan)
    if trip is not None:
        density = rng.uniform(*CARGO_DENSITY_KG_M3)
        trip_id, planned_legs = trip
        for leg in planned_legs:
            if not leg.generated:
                continue
            legs.append(
                {
                    "shipment_id": row["shipment_id"],
                    "trip_id": trip_id,
                    "leg_sequence": str(leg.sequence),
                    "loaded_km": f"{leg.distance_km:.1f}",
                    "weight_kg": row["ordered_weight_kg"],
                    "volume_m3": f"{weight_kg / density:.3f}",
                }
            )

    return ShipmentEntities(
        stops=tuple(stops),
        events=tuple(events),
        tenders=tuple(tenders),
        incidents=incidents,
        legs=tuple(legs),
    )

"""Synthetic shipment generation for the source layer.

The module emits the fifteen business columns of the ``raw.shipments`` contract,
in the declared order, from the dimension catalogs of ``dimensions.py``. Output
is deterministic: every draw comes from a seeded ``Random`` instance, the
temporal anchor is the fixed ``CUTOFF_DATE`` and no clock or absolute path is
read, so two runs with the same arguments are byte-identical.

Shapes of the generated population:

* pickups spread over ``months`` whole months ending at ``CUTOFF_DATE``, weighted
  by the month seasonality and the weekday;
* customer demand is concentrated on the head of the catalog, owned carriers
  move more volume than third parties and primary roads carry more shipments;
* the promised timestamp uses the larger window of the service SLA and the road
  transit plus handling, and a share of deliveries misses that window;
* a shipment whose transit ends after the cutoff is still in transit: it has no
  delivery timestamp and is not a definitive failure;
* ``delivered_qty`` and ``delivered_weight_kg`` are set only when there is a
  delivery, so both are null if and only if ``delivered_at`` is null.

Injected defects (duplicates, critical nulls and deliveries before pickup) are
applied later by ``defects.py``; this module only produces clean base rows.
"""

from __future__ import annotations

import csv
from bisect import bisect
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from itertools import accumulate
from pathlib import Path
from random import Random
from typing import Generic, TypeVar

from nexora.generators.config import (
    CUTOFF_DATE,
    HISTORY_MONTHS,
    RAINY_MONTHS,
    SERVICE_TYPE_CATALOG,
    seasonal_weight,
    window_start,
)
from nexora.generators.dimensions import build_carriers, build_customers, build_routes

SHIPMENTS_ENTITY: str = "shipments"

# Business columns of the ``raw.shipments`` contract, in the order of the DDL.
# The five execution metadata columns are added by the loader, not here.
SHIPMENTS_COLUMNS: tuple[str, ...] = (
    "shipment_id",
    "customer_id",
    "carrier_id",
    "route_id",
    "service_type_id",
    "pickup_ts",
    "pickup_date",
    "promised_delivery_ts",
    "picked_up_at",
    "delivered_at",
    "ordered_qty",
    "delivered_qty",
    "ordered_weight_kg",
    "delivered_weight_kg",
    "definitive_failure_flag",
)

# Local business time of the operation. Pickups between 06:00 and 17:59 local
# stay on the same calendar day in UTC, so ``pickup_date`` matches
# ``pickup_ts::date`` in UTC sessions (the database default) and in UTC-5
# sessions. Other session time zones can shift the date.
LOCAL_TIMEZONE = timezone(timedelta(hours=-5))
PICKUP_LOCAL_HOUR_RANGE: tuple[int, int] = (6, 17)

# Relative demand weight per weekday, Monday first. Weekends drop.
WEEKDAY_WEIGHTS: tuple[float, ...] = (1.05, 1.08, 1.05, 1.02, 0.98, 0.55, 0.25)

# Demand concentration: the customer at rank zero is CUSTOMER_WEIGHT_FLOOR times
# more likely than a mid-tail customer, and the catalog keeps a long tail.
CUSTOMER_WEIGHT_FLOOR: float = 5.0
OWNED_CARRIER_WEIGHT: float = 3.0
THIRD_PARTY_CARRIER_WEIGHT: float = 1.0
ROAD_TYPE_WEIGHTS: dict[str, float] = {"primary": 2.0, "secondary": 1.0, "tertiary": 0.5}

# Pickup delay against the planned timestamp, in minutes.
PICKUP_ON_TIME_MINUTES: tuple[int, int] = (0, 20)
PICKUP_LATE_RATE: float = 0.12
PICKUP_LATE_MINUTES: tuple[int, int] = (25, 240)

# The promise uses the larger window of the contractual SLA and the road transit,
# so no shipment is promised a delivery the operation cannot physically make.
PROMISE_BUFFER_HOURS: float = 2.0

# Transit multiplier over the promised window: on-time deliveries finish inside
# the window, late ones miss it.
ON_TIME_TRANSIT_RANGE: tuple[float, float] = (0.62, 0.98)
LATE_TRANSIT_RANGE: tuple[float, float] = (1.02, 1.40)
LATE_DELIVERY_BASE_RATE: float = 0.15
MIN_LATE_RATE: float = 0.05
MAX_LATE_RATE: float = 0.35
RAINY_LATE_PENALTY: float = 0.05

# Share of shipments that end closed without delivery, and the share of
# deliveries that arrive incomplete.
DELIVERY_BASE_RATE: float = 0.94
MIN_DELIVERY_RATE: float = 0.75
MAX_DELIVERY_RATE: float = 0.98
RAINY_DELIVERY_PENALTY: float = 0.02
PARTIAL_DELIVERY_RATE: float = 0.07
PARTIAL_QTY_RANGE: tuple[float, float] = (0.40, 0.95)

# Last instant covered by the generated history.
CUTOFF_END: datetime = datetime.combine(CUTOFF_DATE, time(23, 59, 59), tzinfo=UTC)

Item = TypeVar("Item")


@dataclass(frozen=True)
class WeightedCatalog(Generic[Item]):
    """A population with fixed relative weights and a single draw per call."""

    population: tuple[Item, ...]
    cumulative: tuple[float, ...]
    total: float

    @classmethod
    def from_weights(
        cls, population: Sequence[Item], weights: Sequence[float]
    ) -> WeightedCatalog[Item]:
        """Build the catalog, rejecting empty populations and non-positive weights."""
        items = tuple(population)
        if not items:
            raise ValueError("population must not be empty")
        if len(items) != len(weights):
            raise ValueError("population and weights must have the same length")
        cumulative = tuple(accumulate(weights))
        if cumulative[-1] <= 0:
            raise ValueError("weights must add up to a positive value")
        return cls(population=items, cumulative=cumulative, total=cumulative[-1])

    def pick(self, rng: Random) -> Item:
        """Return one item, drawn proportionally to its weight."""
        return self.population[bisect(self.cumulative, rng.random() * self.total)]


@dataclass(frozen=True)
class ServiceParameters:
    """Generation parameters attached to a service type of the catalog."""

    weight: float
    delivery_adjustment: float
    late_adjustment: float
    qty_range: tuple[int, int]
    unit_weight_range: tuple[float, float]


@dataclass(frozen=True)
class ServiceProfile:
    """A service type with its SLA and its generation parameters."""

    service_type_id: str
    sla_hours: int
    delivery_adjustment: float
    late_adjustment: float
    qty_range: tuple[int, int]
    unit_weight_range: tuple[float, float]


@dataclass(frozen=True)
class RouteProfile:
    """A route with the transit time the road allows."""

    route_id: str
    estimated_hours: float


SERVICE_PARAMETERS: dict[str, ServiceParameters] = {
    "SVC-STD": ServiceParameters(
        weight=0.34,
        delivery_adjustment=0.0,
        late_adjustment=0.0,
        qty_range=(5, 120),
        unit_weight_range=(2.0, 9.0),
    ),
    "SVC-EXP": ServiceParameters(
        weight=0.24,
        delivery_adjustment=0.02,
        late_adjustment=-0.03,
        qty_range=(1, 30),
        unit_weight_range=(1.0, 6.0),
    ),
    "SVC-ECO": ServiceParameters(
        weight=0.14,
        delivery_adjustment=-0.05,
        late_adjustment=0.04,
        qty_range=(10, 200),
        unit_weight_range=(1.0, 4.0),
    ),
    "SVC-REF": ServiceParameters(
        weight=0.11,
        delivery_adjustment=-0.02,
        late_adjustment=0.01,
        qty_range=(4, 80),
        unit_weight_range=(2.0, 12.0),
    ),
    "SVC-FTL": ServiceParameters(
        weight=0.10,
        delivery_adjustment=0.01,
        late_adjustment=-0.01,
        qty_range=(120, 600),
        unit_weight_range=(8.0, 25.0),
    ),
    "SVC-LTL": ServiceParameters(
        weight=0.07,
        delivery_adjustment=-0.03,
        late_adjustment=0.02,
        qty_range=(20, 300),
        unit_weight_range=(3.0, 12.0),
    ),
}


@dataclass(frozen=True)
class ShipmentTable:
    """The generated shipments, ready to be exported as a single CSV file."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]

    def write_csv(self, directory: str | Path) -> Path:
        """Write the table to ``<directory>/<name>.csv`` and return the path."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{self.name}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(self.columns),
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(self.rows)
        return path


@dataclass(frozen=True)
class _Catalogs:
    """The catalogs every row draws from, built once per run."""

    customers: WeightedCatalog[str]
    carriers: WeightedCatalog[str]
    routes: WeightedCatalog[RouteProfile]
    services: WeightedCatalog[ServiceProfile]
    pickup_days: WeightedCatalog[date]


def _customer_catalog() -> WeightedCatalog[str]:
    rows = build_customers().rows
    weights = [1.0 / (rank + CUSTOMER_WEIGHT_FLOOR) for rank in range(len(rows))]
    return WeightedCatalog.from_weights([row["customer_id"] for row in rows], weights)


def _carrier_catalog() -> WeightedCatalog[str]:
    rows = build_carriers().rows
    weights = [
        OWNED_CARRIER_WEIGHT if row["carrier_type"] == "owned" else THIRD_PARTY_CARRIER_WEIGHT
        for row in rows
    ]
    return WeightedCatalog.from_weights([row["carrier_id"] for row in rows], weights)


def _route_catalog() -> WeightedCatalog[RouteProfile]:
    profiles: list[RouteProfile] = []
    weights: list[float] = []
    for row in build_routes().rows:
        profiles.append(
            RouteProfile(route_id=row["route_id"], estimated_hours=float(row["estimated_hours"]))
        )
        weights.append(ROAD_TYPE_WEIGHTS[row["road_type"]])
    return WeightedCatalog.from_weights(profiles, weights)


def _service_catalog() -> WeightedCatalog[ServiceProfile]:
    profiles: list[ServiceProfile] = []
    weights: list[float] = []
    for service_type in SERVICE_TYPE_CATALOG:
        parameters = SERVICE_PARAMETERS.get(service_type.service_type_id)
        if parameters is None:
            raise ValueError(f"missing generation parameters: {service_type.service_type_id}")
        profiles.append(
            ServiceProfile(
                service_type_id=service_type.service_type_id,
                sla_hours=service_type.sla_hours,
                delivery_adjustment=parameters.delivery_adjustment,
                late_adjustment=parameters.late_adjustment,
                qty_range=parameters.qty_range,
                unit_weight_range=parameters.unit_weight_range,
            )
        )
        weights.append(parameters.weight)
    return WeightedCatalog.from_weights(profiles, weights)


def _pickup_days(months: int) -> WeightedCatalog[date]:
    """Return every day of the window, weighted by seasonality and weekday."""
    days: list[date] = []
    weights: list[float] = []
    current = window_start(CUTOFF_DATE, months)
    while current <= CUTOFF_DATE:
        days.append(current)
        weights.append(seasonal_weight(current.month) * WEEKDAY_WEIGHTS[current.weekday()])
        current += timedelta(days=1)
    return WeightedCatalog.from_weights(days, weights)


def _pickup_timestamp(rng: Random, day: date) -> datetime:
    """Return the planned pickup instant of ``day``, always in UTC."""
    local = datetime.combine(
        day,
        time(hour=rng.randint(*PICKUP_LOCAL_HOUR_RANGE), minute=rng.randint(0, 59)),
        tzinfo=LOCAL_TIMEZONE,
    )
    return local.astimezone(UTC)


def _pickup_delay_minutes(rng: Random) -> int:
    if rng.random() < PICKUP_LATE_RATE:
        return rng.randint(*PICKUP_LATE_MINUTES)
    return rng.randint(*PICKUP_ON_TIME_MINUTES)


def _promise_hours(route: RouteProfile, service: ServiceProfile) -> float:
    """Return the promised window, in hours: the larger of SLA and road transit."""
    return max(float(service.sla_hours), route.estimated_hours + PROMISE_BUFFER_HOURS)


def _late_rate(service: ServiceProfile, month: int) -> float:
    rate = LATE_DELIVERY_BASE_RATE + service.late_adjustment
    if month in RAINY_MONTHS:
        rate += RAINY_LATE_PENALTY
    return min(MAX_LATE_RATE, max(MIN_LATE_RATE, rate))


def _delivery_rate(service: ServiceProfile, month: int) -> float:
    rate = DELIVERY_BASE_RATE + service.delivery_adjustment
    if month in RAINY_MONTHS:
        rate -= RAINY_DELIVERY_PENALTY
    return min(MAX_DELIVERY_RATE, max(MIN_DELIVERY_RATE, rate))


def _transit_hours(rng: Random, promise_hours: float, service: ServiceProfile, month: int) -> float:
    """Return the elapsed hours from the real pickup to the delivery."""
    if rng.random() < _late_rate(service, month):
        return promise_hours * rng.uniform(*LATE_TRANSIT_RANGE)
    return promise_hours * rng.uniform(*ON_TIME_TRANSIT_RANGE)


def _delivery_outcome(
    rng: Random,
    picked_up_at: datetime,
    transit_hours: float,
    ordered_qty: int,
    ordered_weight_kg: float,
    service: ServiceProfile,
    month: int,
) -> tuple[datetime | None, int | None, float | None, bool]:
    """Return the delivery fields and the definitive failure flag of a shipment.

    The four values move together: without a delivery timestamp both quantities
    are null, and the failure flag is only raised for a closed shipment that
    ended without delivery.
    """
    delivered_at = picked_up_at + timedelta(hours=transit_hours)
    if delivered_at > CUTOFF_END:
        # Still on the road at the cutoff: nothing is delivered and nothing failed yet.
        return None, None, None, False
    if rng.random() >= _delivery_rate(service, month):
        return None, None, None, True
    if ordered_qty > 1 and rng.random() < PARTIAL_DELIVERY_RATE:
        delivered_qty = max(1, round(ordered_qty * rng.uniform(*PARTIAL_QTY_RANGE)))
        delivered_qty = min(delivered_qty, ordered_qty - 1)
        delivered_weight_kg = round(ordered_weight_kg * delivered_qty / ordered_qty, 3)
        return delivered_at, delivered_qty, delivered_weight_kg, False
    return delivered_at, ordered_qty, ordered_weight_kg, False


def _format_ts(value: datetime) -> str:
    """Return an ISO 8601 timestamp with an explicit UTC offset."""
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _build_row(rng: Random, index: int, catalogs: _Catalogs) -> dict[str, str]:
    day = catalogs.pickup_days.pick(rng)
    route = catalogs.routes.pick(rng)
    service = catalogs.services.pick(rng)

    pickup_ts = _pickup_timestamp(rng, day)
    picked_up_at = pickup_ts + timedelta(minutes=_pickup_delay_minutes(rng))
    promise_hours = _promise_hours(route, service)
    promised_delivery_ts = pickup_ts + timedelta(hours=promise_hours)

    ordered_qty = rng.randint(*service.qty_range)
    ordered_weight_kg = round(ordered_qty * rng.uniform(*service.unit_weight_range), 3)

    transit_hours = _transit_hours(rng, promise_hours, service, pickup_ts.month)
    delivered_at, delivered_qty, delivered_weight_kg, failure = _delivery_outcome(
        rng,
        picked_up_at,
        transit_hours,
        ordered_qty,
        ordered_weight_kg,
        service,
        pickup_ts.month,
    )

    return {
        "shipment_id": f"SHP-{index:06d}",
        "customer_id": catalogs.customers.pick(rng),
        "carrier_id": catalogs.carriers.pick(rng),
        "route_id": route.route_id,
        "service_type_id": service.service_type_id,
        "pickup_ts": _format_ts(pickup_ts),
        "pickup_date": pickup_ts.date().isoformat(),
        "promised_delivery_ts": _format_ts(promised_delivery_ts),
        "picked_up_at": _format_ts(picked_up_at),
        "delivered_at": "" if delivered_at is None else _format_ts(delivered_at),
        "ordered_qty": str(ordered_qty),
        "delivered_qty": "" if delivered_qty is None else str(delivered_qty),
        "ordered_weight_kg": f"{ordered_weight_kg:.3f}",
        "delivered_weight_kg": "" if delivered_weight_kg is None else f"{delivered_weight_kg:.3f}",
        "definitive_failure_flag": "true" if failure else "false",
    }


def iter_shipments(
    count: int, seed: int = 42, months: int = HISTORY_MONTHS
) -> Iterator[dict[str, str]]:
    """Yield ``count`` shipment rows with the business columns of the contract.

    Rows are numbered from ``SHP-000001`` and pulled from ``seed`` alone; the
    month window ends at the fixed ``CUTOFF_DATE``. Rows come out one at a time
    in a fixed draw order, so iterating twice with the same arguments yields the
    same rows without keeping any of them in memory.
    """
    if count < 1:
        raise ValueError(f"count must be positive: {count}")
    rng = Random(seed)
    catalogs = _Catalogs(
        customers=_customer_catalog(),
        carriers=_carrier_catalog(),
        routes=_route_catalog(),
        services=_service_catalog(),
        pickup_days=_pickup_days(months),
    )
    for index in range(1, count + 1):
        yield _build_row(rng, index, catalogs)


def generate_shipments(
    count: int, seed: int = 42, months: int = HISTORY_MONTHS
) -> tuple[dict[str, str], ...]:
    """Return every row of ``iter_shipments`` with the same arguments."""
    return tuple(iter_shipments(count, seed, months))


def build_shipment_table(count: int, seed: int = 42, months: int = HISTORY_MONTHS) -> ShipmentTable:
    """Return the shipments wrapped in a table ready for CSV export."""
    return ShipmentTable(
        name=SHIPMENTS_ENTITY,
        columns=SHIPMENTS_COLUMNS,
        rows=generate_shipments(count, seed, months),
    )


def write_shipments(
    directory: str | Path, count: int, seed: int = 42, months: int = HISTORY_MONTHS
) -> Path:
    """Write ``shipments.csv`` into ``directory`` and return its path."""
    return build_shipment_table(count, seed, months).write_csv(directory)

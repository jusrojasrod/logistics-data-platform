"""ERP extracts derived from the shipments: tariffs, invoicing, payments and costs.

Tariffs price each customer on the first route and service combinations of its
base shipments, with two versions per combination. Invoices, invoice lines,
payments, carrier settlements and fleet costs are derived one shipment at a
time from the clean base row, so they can be written in the same stream as the
operational tables.

Execution model of the operation: a shipment moved by an owned carrier carries
fleet costs and a shipment moved by a third party carries a settlement line,
never both, so the cost of a shipment is never counted twice.

Every draw comes from a random generator of its own, consumed in a fixed order,
so these extracts never alter the draws of the operational tables. Amounts are
in COP with two decimals and business dates are UTC calendar dates.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from nexora.generators.config import (
    BILLING_VARIANCE_PCT_RANGE,
    BILLING_VARIANCE_RATE,
    COST_CENTER_PREFIX,
    COST_COMPONENTS,
    COST_PER_KM_AMOUNT,
    CURRENCY_CODE,
    CUTOFF_DATE,
    HISTORY_MONTHS,
    INVOICE_DELAY_DAYS,
    INVOICE_ID_PREFIX,
    INVOICE_LINE_CONCEPTS,
    INVOICE_LINE_ID_PREFIX,
    INVOICE_STATUS_ISSUED,
    INVOICE_STATUS_PAID,
    LIST_BASE_AMOUNT,
    LIST_FUEL_SURCHARGE_PCT,
    LIST_PER_KM_AMOUNT,
    PAYMENT_ID_PREFIX,
    PAYMENT_METHODS,
    PAYMENT_OFFSET_DAYS,
    PAYMENT_RATE,
    SETTLEMENT_CONCEPT,
    SETTLEMENT_DELAY_DAYS,
    SETTLEMENT_FUEL_SURCHARGE_PCT,
    SETTLEMENT_ID_PREFIX,
    SETTLEMENT_LATE_DEDUCTION_PCT,
    SETTLEMENT_LINE_ID_PREFIX,
    SETTLEMENT_PER_KM_AMOUNT,
    SETTLEMENT_STATUS,
    SHIPMENT_COST_ID_PREFIX,
    TARIFF_BASE_AMOUNT_RANGE,
    TARIFF_COMBOS_PER_CUSTOMER,
    TARIFF_FUEL_SURCHARGE_PCT_RANGE,
    TARIFF_ID_PREFIX,
    TARIFF_INCREASE_PCT_RANGE,
    TARIFF_PER_KM_AMOUNT_RANGE,
    TARIFF_VERSIONS,
    TAX_PCT,
    TOLL_PER_KM_AMOUNT,
    window_start,
)
from nexora.generators.dimensions import ERP_SOURCE_SYSTEM, DimensionTable

TARIFFS_ENTITY: str = "tariffs"
INVOICES_ENTITY: str = "invoices"
INVOICE_LINES_ENTITY: str = "invoice_lines"
PAYMENTS_ENTITY: str = "payments"
CARRIER_SETTLEMENTS_ENTITY: str = "carrier_settlements"
SHIPMENT_COSTS_ENTITY: str = "shipment_costs"

OWNED_CARRIER_TYPE: str = "owned"
THIRD_PARTY_CARRIER_TYPE: str = "third_party"

TARIFFS_COLUMNS: tuple[str, ...] = (
    "tariff_id",
    "customer_id",
    "route_id",
    "service_type_id",
    "valid_from",
    "valid_to",
    "base_amount",
    "per_km_amount",
    "fuel_surcharge_pct",
    "currency_code",
)

INVOICES_COLUMNS: tuple[str, ...] = (
    "invoice_id",
    "customer_id",
    "shipment_id",
    "invoice_date",
    "due_date",
    "invoice_status",
    "total_amount",
    "currency_code",
)

INVOICE_LINES_COLUMNS: tuple[str, ...] = (
    "invoice_line_id",
    "invoice_id",
    "shipment_id",
    "line_number",
    "concept",
    "tariff_amount",
    "billed_revenue_amount",
    "tax_amount",
    "currency_code",
)

PAYMENTS_COLUMNS: tuple[str, ...] = (
    "payment_id",
    "invoice_id",
    "customer_id",
    "paid_date",
    "payment_amount",
    "payment_method",
    "currency_code",
)

CARRIER_SETTLEMENTS_COLUMNS: tuple[str, ...] = (
    "settlement_line_id",
    "settlement_id",
    "shipment_id",
    "carrier_id",
    "concept",
    "settled_date",
    "payable_amount",
    "deduction_amount",
    "fuel_surcharge_amount",
    "settlement_status",
    "currency_code",
)

SHIPMENT_COSTS_COLUMNS: tuple[str, ...] = (
    "shipment_cost_id",
    "shipment_id",
    "carrier_id",
    "cost_component",
    "cost_date",
    "cost_amount",
    "cost_center",
    "currency_code",
)

CENT = Decimal("0.01")
HUNDRED = Decimal(100)


def money(value: float | Decimal) -> Decimal:
    """Return ``value`` rounded half up to cents."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def format_amount(value: Decimal) -> str:
    """Return an amount with exactly two decimals."""
    return f"{value:.2f}"


def _utc_date(value: str) -> date:
    return datetime.fromisoformat(value).date()


class TariffCombos:
    """Collects, per customer, the first route and service combinations it ships.

    It sees the base shipments in generation order and keeps at most
    ``TARIFF_COMBOS_PER_CUSTOMER`` distinct combinations per customer. It never
    draws, so feeding it does not change any other output.
    """

    def __init__(self) -> None:
        self._combos: dict[str, list[tuple[str, str]]] = {}

    def add(self, row: Mapping[str, str]) -> None:
        """Record the combination of a base shipment, if its customer has room."""
        combos = self._combos.setdefault(row["customer_id"], [])
        if len(combos) >= TARIFF_COMBOS_PER_CUSTOMER:
            return
        combo = (row["route_id"], row["service_type_id"])
        if combo not in combos:
            combos.append(combo)

    def by_customer(self) -> dict[str, tuple[tuple[str, str], ...]]:
        """Return the combinations per customer, customers sorted by id."""
        return {customer: tuple(self._combos[customer]) for customer in sorted(self._combos)}


def build_tariffs(combos: TariffCombos, seed: int, months: int = HISTORY_MONTHS) -> DimensionTable:
    """Return the tariff versions of every collected combination.

    Each combination gets a first version valid from the start of the window and
    a second one, with an increase, valid from a day drawn inside the window.
    ``valid_to`` is exclusive and equals the ``valid_from`` of the next version;
    the last version has no end.
    """
    if TARIFF_VERSIONS != 2:
        raise ValueError(f"unsupported number of tariff versions: {TARIFF_VERSIONS}")
    rng = random.Random(f"tariffs-{seed}")
    start = window_start(CUTOFF_DATE, months)
    window_days = (CUTOFF_DATE - start).days
    rows: list[dict[str, str]] = []
    number = 0
    for customer_id, customer_combos in combos.by_customer().items():
        for route_id, service_type_id in customer_combos:
            number += 1
            base = money(rng.randint(*TARIFF_BASE_AMOUNT_RANGE))
            per_km = money(rng.randint(*TARIFF_PER_KM_AMOUNT_RANGE))
            fuel_pct = money(rng.uniform(*TARIFF_FUEL_SURCHARGE_PCT_RANGE))
            change = start + timedelta(days=rng.randint(1, window_days))
            factor = 1 + Decimal(str(rng.uniform(*TARIFF_INCREASE_PCT_RANGE))) / HUNDRED
            versions = (
                (start, change, base, per_km),
                (change, None, money(base * factor), money(per_km * factor)),
            )
            for valid_from, valid_to, base_amount, per_km_amount in versions:
                rows.append(
                    {
                        "tariff_id": f"{TARIFF_ID_PREFIX}-{number:05d}",
                        "customer_id": customer_id,
                        "route_id": route_id,
                        "service_type_id": service_type_id,
                        "valid_from": valid_from.isoformat(),
                        "valid_to": "" if valid_to is None else valid_to.isoformat(),
                        "base_amount": format_amount(base_amount),
                        "per_km_amount": format_amount(per_km_amount),
                        "fuel_surcharge_pct": format_amount(fuel_pct),
                        "currency_code": CURRENCY_CODE,
                    }
                )
    return DimensionTable(
        name=TARIFFS_ENTITY,
        source_system=ERP_SOURCE_SYSTEM,
        columns=TARIFFS_COLUMNS,
        rows=tuple(rows),
    )


@dataclass(frozen=True)
class TariffVersion:
    """A priced version of a tariff, valid on ``[valid_from, valid_to)``."""

    valid_from: date
    valid_to: date | None
    base_amount: Decimal
    per_km_amount: Decimal
    fuel_surcharge_pct: Decimal


class TariffIndex:
    """Finds the tariff version in force for a customer, route, service and day."""

    def __init__(self, rows: Iterable[Mapping[str, str]]) -> None:
        versions: dict[tuple[str, str, str], list[TariffVersion]] = {}
        for row in rows:
            key = (row["customer_id"], row["route_id"], row["service_type_id"])
            versions.setdefault(key, []).append(
                TariffVersion(
                    valid_from=date.fromisoformat(row["valid_from"]),
                    valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
                    base_amount=Decimal(row["base_amount"]),
                    per_km_amount=Decimal(row["per_km_amount"]),
                    fuel_surcharge_pct=Decimal(row["fuel_surcharge_pct"]),
                )
            )
        self._versions = {
            key: tuple(sorted(items, key=lambda version: version.valid_from))
            for key, items in versions.items()
        }
        self._starts = {
            key: tuple(version.valid_from for version in items)
            for key, items in self._versions.items()
        }

    def __len__(self) -> int:
        return len(self._versions)

    def find(
        self, customer_id: str, route_id: str, service_type_id: str, day: date
    ) -> TariffVersion | None:
        """Return the version in force on ``day``, or ``None`` if there is none."""
        key = (customer_id, route_id, service_type_id)
        versions = self._versions.get(key)
        if versions is None:
            return None
        position = bisect_right(self._starts[key], day) - 1
        if position < 0:
            return None
        version = versions[position]
        if version.valid_to is not None and day >= version.valid_to:
            return None
        return version


@dataclass(frozen=True)
class CarrierProfile:
    """What the ERP needs from a carrier: its type and its rate index."""

    carrier_type: str
    rate_index: Decimal


@dataclass(frozen=True)
class RouteCost:
    """What the ERP needs from a route: its distance and its road type."""

    distance_km: Decimal
    road_type: str


@dataclass(frozen=True)
class ErpContext:
    """Catalog lookups shared by every shipment of the stream."""

    payment_terms: Mapping[str, int]
    carriers: Mapping[str, CarrierProfile]
    routes: Mapping[str, RouteCost]
    tariffs: TariffIndex

    @classmethod
    def from_tables(
        cls,
        customers: DimensionTable,
        carriers: DimensionTable,
        routes: DimensionTable,
        tariffs: DimensionTable,
    ) -> ErpContext:
        """Build the lookups from the catalogs and the tariff versions."""
        return cls(
            payment_terms={
                row["customer_id"]: int(row["payment_term_days"]) for row in customers.rows
            },
            carriers={
                row["carrier_id"]: CarrierProfile(
                    carrier_type=row["carrier_type"], rate_index=Decimal(row["rate_index"])
                )
                for row in carriers.rows
            },
            routes={
                row["route_id"]: RouteCost(
                    distance_km=Decimal(row["distance_km"]), road_type=row["road_type"]
                )
                for row in routes.rows
            },
            tariffs=TariffIndex(tariffs.rows),
        )


@dataclass
class ErpIds:
    """Running counters of the natural ids of the ERP extracts."""

    invoice: int = 0
    invoice_line: int = 0
    payment: int = 0
    settlement_line: int = 0
    shipment_cost: int = 0

    def next_invoice(self) -> str:
        self.invoice += 1
        return f"{INVOICE_ID_PREFIX}-{self.invoice:08d}"

    def next_invoice_line(self) -> str:
        self.invoice_line += 1
        return f"{INVOICE_LINE_ID_PREFIX}-{self.invoice_line:09d}"

    def next_payment(self) -> str:
        self.payment += 1
        return f"{PAYMENT_ID_PREFIX}-{self.payment:08d}"

    def next_settlement_line(self) -> str:
        self.settlement_line += 1
        return f"{SETTLEMENT_LINE_ID_PREFIX}-{self.settlement_line:08d}"

    def next_shipment_cost(self) -> str:
        self.shipment_cost += 1
        return f"{SHIPMENT_COST_ID_PREFIX}-{self.shipment_cost:09d}"


@dataclass(frozen=True)
class ShipmentFinance:
    """The ERP rows derived from a single shipment."""

    invoices: tuple[dict[str, str], ...] = ()
    invoice_lines: tuple[dict[str, str], ...] = ()
    payments: tuple[dict[str, str], ...] = ()
    settlements: tuple[dict[str, str], ...] = ()
    costs: tuple[dict[str, str], ...] = ()


def _billed(rng: random.Random, tariff_amount: Decimal) -> Decimal:
    """Return the billed amount of a line: the tariff, or a small variation of it.

    Every line consumes the same three draws, whether it varies or not.
    """
    varies = rng.random() < BILLING_VARIANCE_RATE
    pct = Decimal(str(rng.uniform(*BILLING_VARIANCE_PCT_RANGE)))
    sign = rng.choice((-1, 1))
    if not varies:
        return tariff_amount
    return money(tariff_amount * (1 + sign * pct / HUNDRED))


def _invoice(
    row: Mapping[str, str],
    context: ErpContext,
    rng: random.Random,
    ids: ErpIds,
    route: RouteCost,
) -> tuple[tuple[dict[str, str], ...], tuple[dict[str, str], ...], tuple[dict[str, str], ...]]:
    """Return the invoice, its lines and its payment, or empty tuples if not issued.

    Every delivered shipment consumes the same draws, issued or not.
    """
    delivered = _utc_date(row["delivered_at"])
    invoice_date = delivered + timedelta(days=rng.randint(*INVOICE_DELAY_DAYS))
    version = context.tariffs.find(
        row["customer_id"],
        row["route_id"],
        row["service_type_id"],
        date.fromisoformat(row["pickup_date"]),
    )
    if version is None:
        base, per_km = money(LIST_BASE_AMOUNT), money(LIST_PER_KM_AMOUNT)
        fuel_pct = money(LIST_FUEL_SURCHARGE_PCT)
    else:
        base, per_km, fuel_pct = (
            version.base_amount,
            version.per_km_amount,
            version.fuel_surcharge_pct,
        )
    freight = money(base + per_km * route.distance_km)
    tariff_amounts = {
        "freight": freight,
        "fuel_surcharge": money(freight * fuel_pct / HUNDRED),
    }
    billed = [_billed(rng, tariff_amounts[concept]) for concept in INVOICE_LINE_CONCEPTS]
    pays = rng.random() < PAYMENT_RATE
    paid_offset = rng.randint(*PAYMENT_OFFSET_DAYS)
    method = rng.choice(PAYMENT_METHODS)

    if invoice_date > CUTOFF_DATE:
        return (), (), ()

    due_date = invoice_date + timedelta(days=context.payment_terms[row["customer_id"]])
    paid_date = max(due_date + timedelta(days=paid_offset), invoice_date)
    paid = pays and paid_date <= CUTOFF_DATE

    invoice_id = ids.next_invoice()
    lines: list[dict[str, str]] = []
    total = Decimal(0)
    for number, (concept, billed_amount) in enumerate(
        zip(INVOICE_LINE_CONCEPTS, billed, strict=True), start=1
    ):
        tax = money(billed_amount * Decimal(str(TAX_PCT)) / HUNDRED)
        total += billed_amount + tax
        lines.append(
            {
                "invoice_line_id": ids.next_invoice_line(),
                "invoice_id": invoice_id,
                "shipment_id": row["shipment_id"],
                "line_number": str(number),
                "concept": concept,
                "tariff_amount": format_amount(tariff_amounts[concept]),
                "billed_revenue_amount": format_amount(billed_amount),
                "tax_amount": format_amount(tax),
                "currency_code": CURRENCY_CODE,
            }
        )
    invoice = {
        "invoice_id": invoice_id,
        "customer_id": row["customer_id"],
        "shipment_id": row["shipment_id"],
        "invoice_date": invoice_date.isoformat(),
        "due_date": due_date.isoformat(),
        "invoice_status": INVOICE_STATUS_PAID if paid else INVOICE_STATUS_ISSUED,
        "total_amount": format_amount(total),
        "currency_code": CURRENCY_CODE,
    }
    payments: tuple[dict[str, str], ...] = ()
    if paid:
        payments = (
            {
                "payment_id": ids.next_payment(),
                "invoice_id": invoice_id,
                "customer_id": row["customer_id"],
                "paid_date": paid_date.isoformat(),
                "payment_amount": format_amount(total),
                "payment_method": method,
                "currency_code": CURRENCY_CODE,
            },
        )
    return (invoice,), tuple(lines), payments


def _settlement(
    row: Mapping[str, str],
    rng: random.Random,
    ids: ErpIds,
    carrier: CarrierProfile,
    route: RouteCost,
) -> tuple[dict[str, str], ...]:
    """Return the freight settlement line of a delivered third-party shipment."""
    delivered_at = datetime.fromisoformat(row["delivered_at"])
    settled_date = delivered_at.date() + timedelta(days=rng.randint(*SETTLEMENT_DELAY_DAYS))
    if settled_date > CUTOFF_DATE:
        return ()
    payable = money(route.distance_km * Decimal(str(SETTLEMENT_PER_KM_AMOUNT)) * carrier.rate_index)
    late = delivered_at > datetime.fromisoformat(row["promised_delivery_ts"])
    deduction = (
        money(payable * Decimal(str(SETTLEMENT_LATE_DEDUCTION_PCT)) / HUNDRED) if late else money(0)
    )
    fuel = money(payable * Decimal(str(SETTLEMENT_FUEL_SURCHARGE_PCT)) / HUNDRED)
    return (
        {
            "settlement_line_id": ids.next_settlement_line(),
            "settlement_id": (f"{SETTLEMENT_ID_PREFIX}-{row['carrier_id']}-{settled_date:%Y%m}"),
            "shipment_id": row["shipment_id"],
            "carrier_id": row["carrier_id"],
            "concept": SETTLEMENT_CONCEPT,
            "settled_date": settled_date.isoformat(),
            "payable_amount": format_amount(payable),
            "deduction_amount": format_amount(deduction),
            "fuel_surcharge_amount": format_amount(fuel),
            "settlement_status": SETTLEMENT_STATUS,
            "currency_code": CURRENCY_CODE,
        },
    )


def _costs(row: Mapping[str, str], ids: ErpIds, route: RouteCost) -> tuple[dict[str, str], ...]:
    """Return one fleet cost row per component of an owned-fleet shipment."""
    per_km = {**COST_PER_KM_AMOUNT, "tolls": TOLL_PER_KM_AMOUNT[route.road_type]}
    return tuple(
        {
            "shipment_cost_id": ids.next_shipment_cost(),
            "shipment_id": row["shipment_id"],
            "carrier_id": row["carrier_id"],
            "cost_component": component,
            "cost_date": row["pickup_date"],
            "cost_amount": format_amount(
                money(route.distance_km * Decimal(str(per_km[component])))
            ),
            "cost_center": f"{COST_CENTER_PREFIX}-{row['carrier_id']}",
            "currency_code": CURRENCY_CODE,
        }
        for component in COST_COMPONENTS
    )


def shipment_finance(
    row: Mapping[str, str], context: ErpContext, rng: random.Random, ids: ErpIds
) -> ShipmentFinance:
    """Return the invoice, lines, payment, settlement or costs of a clean base shipment.

    Only delivered shipments are invoiced. An owned-fleet shipment carries fleet
    costs, delivered or not; a delivered third-party shipment carries a
    settlement line. ``rng`` is shared by the whole stream and consumed in a
    fixed order; ``ids`` numbers the rows across the stream.
    """
    route = context.routes[row["route_id"]]
    carrier = context.carriers[row["carrier_id"]]
    delivered = bool(row["delivered_at"])

    invoices: tuple[dict[str, str], ...] = ()
    lines: tuple[dict[str, str], ...] = ()
    payments: tuple[dict[str, str], ...] = ()
    if delivered:
        invoices, lines, payments = _invoice(row, context, rng, ids, route)

    settlements: tuple[dict[str, str], ...] = ()
    costs: tuple[dict[str, str], ...] = ()
    if carrier.carrier_type == OWNED_CARRIER_TYPE:
        costs = _costs(row, ids, route)
    elif carrier.carrier_type == THIRD_PARTY_CARRIER_TYPE:
        if delivered:
            settlements = _settlement(row, rng, ids, carrier, route)
    else:
        raise ValueError(f"unknown carrier type: {carrier.carrier_type}")

    return ShipmentFinance(
        invoices=invoices,
        invoice_lines=lines,
        payments=payments,
        settlements=settlements,
        costs=costs,
    )

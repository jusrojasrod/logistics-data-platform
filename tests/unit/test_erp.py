"""Pruebas de los extractos ERP sobre 2000 envíos base: tarifas, facturas, líneas,
pagos, liquidaciones de terceros y costos de flota propia."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from random import Random

import pytest

from nexora.generators.config import (
    COST_COMPONENTS,
    CUTOFF_DATE,
    HISTORY_MONTHS,
    INVOICE_LINE_CONCEPTS,
    TARIFF_COMBOS_PER_CUSTOMER,
    window_start,
)
from nexora.generators.dimensions import build_carriers, build_customers, build_routes
from nexora.generators.erp import (
    CARRIER_SETTLEMENTS_COLUMNS,
    INVOICE_LINES_COLUMNS,
    INVOICES_COLUMNS,
    PAYMENTS_COLUMNS,
    SHIPMENT_COSTS_COLUMNS,
    TARIFFS_COLUMNS,
    ErpContext,
    ErpIds,
    TariffCombos,
    TariffIndex,
    build_tariffs,
    shipment_finance,
)
from nexora.generators.shipments import generate_shipments

SEED = 42
SHIPMENTS = 2000
AMOUNT = re.compile(r"^-?\d+\.\d{2}$")


@dataclass(frozen=True)
class ErpRun:
    shipments: tuple[dict[str, str], ...]
    tariffs: tuple[dict[str, str], ...]
    invoices: tuple[dict[str, str], ...]
    invoice_lines: tuple[dict[str, str], ...]
    payments: tuple[dict[str, str], ...]
    settlements: tuple[dict[str, str], ...]
    costs: tuple[dict[str, str], ...]


def run_erp(seed: int = SEED) -> ErpRun:
    shipments = generate_shipments(SHIPMENTS, seed, HISTORY_MONTHS)
    combos = TariffCombos()
    for row in shipments:
        combos.add(row)
    tariffs = build_tariffs(combos, seed)
    context = ErpContext.from_tables(build_customers(), build_carriers(), build_routes(), tariffs)
    rng = Random(f"erp-{seed}")
    ids = ErpIds()
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in shipments:
        finance = shipment_finance(row, context, rng, ids)
        out["invoices"].extend(finance.invoices)
        out["invoice_lines"].extend(finance.invoice_lines)
        out["payments"].extend(finance.payments)
        out["settlements"].extend(finance.settlements)
        out["costs"].extend(finance.costs)
    return ErpRun(
        shipments=shipments,
        tariffs=tariffs.rows,
        **{name: tuple(rows) for name, rows in out.items()},
    )


@pytest.fixture(scope="module")
def erp() -> ErpRun:
    return run_erp()


@pytest.fixture(scope="module")
def carrier_types() -> dict[str, str]:
    return {row["carrier_id"]: row["carrier_type"] for row in build_carriers().rows}


def test_rows_follow_the_declared_columns(erp: ErpRun) -> None:
    for rows, columns in (
        (erp.tariffs, TARIFFS_COLUMNS),
        (erp.invoices, INVOICES_COLUMNS),
        (erp.invoice_lines, INVOICE_LINES_COLUMNS),
        (erp.payments, PAYMENTS_COLUMNS),
        (erp.settlements, CARRIER_SETTLEMENTS_COLUMNS),
        (erp.costs, SHIPMENT_COSTS_COLUMNS),
    ):
        assert rows, columns[0]
        assert all(tuple(row) == columns for row in rows), columns[0]


def test_natural_ids_are_unique(erp: ErpRun) -> None:
    for rows, key in (
        (erp.invoices, "invoice_id"),
        (erp.invoice_lines, "invoice_line_id"),
        (erp.payments, "payment_id"),
        (erp.settlements, "settlement_line_id"),
        (erp.costs, "shipment_cost_id"),
    ):
        assert len({row[key] for row in rows}) == len(rows), key
    tariff_keys = {(row["tariff_id"], row["valid_from"]) for row in erp.tariffs}
    assert len(tariff_keys) == len(erp.tariffs)


def test_one_invoice_per_delivered_shipment_up_to_the_cutoff(erp: ErpRun) -> None:
    delivered = {row["shipment_id"]: row for row in erp.shipments if row["delivered_at"]}
    invoiced = Counter(row["shipment_id"] for row in erp.invoices)
    assert set(invoiced) <= set(delivered)
    assert set(invoiced.values()) == {1}
    for invoice in erp.invoices:
        shipment = delivered[invoice["shipment_id"]]
        invoice_date = date.fromisoformat(invoice["invoice_date"])
        delivered_on = date.fromisoformat(shipment["delivered_at"][:10])
        assert invoice_date <= CUTOFF_DATE
        assert 0 <= (invoice_date - delivered_on).days <= 3
        assert invoice["customer_id"] == shipment["customer_id"]
    # A delivered shipment is left out only when its invoice would fall after the cutoff.
    for shipment_id in set(delivered) - set(invoiced):
        delivered_on = date.fromisoformat(delivered[shipment_id]["delivered_at"][:10])
        assert (CUTOFF_DATE - delivered_on).days < 3


def test_two_lines_per_invoice(erp: ErpRun) -> None:
    lines: dict[str, list[dict[str, str]]] = defaultdict(list)
    for line in erp.invoice_lines:
        lines[line["invoice_id"]].append(line)
    assert set(lines) == {row["invoice_id"] for row in erp.invoices}
    for invoice_id, invoice_lines in lines.items():
        assert [line["concept"] for line in invoice_lines] == list(
            INVOICE_LINE_CONCEPTS
        ), invoice_id
        assert [line["line_number"] for line in invoice_lines] == ["1", "2"]


def test_total_amount_is_billed_plus_tax(erp: ErpRun) -> None:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for line in erp.invoice_lines:
        totals[line["invoice_id"]] += Decimal(line["billed_revenue_amount"]) + Decimal(
            line["tax_amount"]
        )
    for invoice in erp.invoices:
        assert Decimal(invoice["total_amount"]) == totals[invoice["invoice_id"]]


def test_at_most_one_payment_per_invoice_never_before_the_invoice(erp: ErpRun) -> None:
    invoices = {row["invoice_id"]: row for row in erp.invoices}
    paid = Counter(row["invoice_id"] for row in erp.payments)
    assert set(paid) <= set(invoices)
    assert set(paid.values()) == {1}
    for payment in erp.payments:
        invoice = invoices[payment["invoice_id"]]
        assert payment["paid_date"] >= invoice["invoice_date"]
        assert payment["payment_amount"] == invoice["total_amount"]
        assert payment["customer_id"] == invoice["customer_id"]


def test_invoice_status_is_paid_only_with_a_payment(erp: ErpRun) -> None:
    paid = {row["invoice_id"] for row in erp.payments}
    for invoice in erp.invoices:
        expected = "paid" if invoice["invoice_id"] in paid else "issued"
        assert invoice["invoice_status"] == expected


def test_costs_only_for_owned_and_settlements_only_for_third_party(
    erp: ErpRun, carrier_types: dict[str, str]
) -> None:
    assert {carrier_types[row["carrier_id"]] for row in erp.costs} == {"owned"}
    assert {carrier_types[row["carrier_id"]] for row in erp.settlements} == {"third_party"}
    shipment_carrier = {row["shipment_id"]: row["carrier_id"] for row in erp.shipments}
    for row in (*erp.costs, *erp.settlements):
        assert row["carrier_id"] == shipment_carrier[row["shipment_id"]]


def test_no_shipment_has_costs_and_settlements(erp: ErpRun) -> None:
    with_costs = {row["shipment_id"] for row in erp.costs}
    with_settlements = {row["shipment_id"] for row in erp.settlements}
    assert with_costs and with_settlements
    assert not with_costs & with_settlements


def test_three_cost_components_per_owned_shipment(
    erp: ErpRun, carrier_types: dict[str, str]
) -> None:
    owned = {
        row["shipment_id"] for row in erp.shipments if carrier_types[row["carrier_id"]] == "owned"
    }
    components: dict[str, list[str]] = defaultdict(list)
    for row in erp.costs:
        components[row["shipment_id"]].append(row["cost_component"])
        assert row["cost_center"] == f"CC-{row['carrier_id']}"
    assert set(components) == owned
    assert all(found == list(COST_COMPONENTS) for found in components.values())


def test_one_settlement_line_per_delivered_third_party_shipment(
    erp: ErpRun, carrier_types: dict[str, str]
) -> None:
    shipments = {row["shipment_id"]: row for row in erp.shipments}
    lines = Counter(row["shipment_id"] for row in erp.settlements)
    assert set(lines.values()) == {1}
    for row in erp.settlements:
        shipment = shipments[row["shipment_id"]]
        assert shipment["delivered_at"]
        assert row["settlement_id"] == (
            f"STL-{row['carrier_id']}-{row['settled_date'][:4]}{row['settled_date'][5:7]}"
        )
        late = shipment["delivered_at"] > shipment["promised_delivery_ts"]
        assert (Decimal(row["deduction_amount"]) > 0) == late
    delivered_third_party = {
        row["shipment_id"]
        for row in erp.shipments
        if row["delivered_at"] and carrier_types[row["carrier_id"]] == "third_party"
    }
    assert set(lines) <= delivered_third_party


def test_twelve_tariffs_per_customer_with_six_combinations_without_overlaps(
    erp: ErpRun,
) -> None:
    seen: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in erp.shipments:
        seen[row["customer_id"]].add((row["route_id"], row["service_type_id"]))
    per_customer = Counter(row["customer_id"] for row in erp.tariffs)
    assert set(per_customer) == set(seen)
    for customer_id, combos in seen.items():
        expected = min(TARIFF_COMBOS_PER_CUSTOMER, len(combos))
        assert per_customer[customer_id] == 2 * expected, customer_id
    # Every customer with six combinations or more gets twelve versions.
    enough = {customer_id for customer_id, combos in seen.items() if len(combos) >= 6}
    assert enough
    assert {customer_id for customer_id, count in per_customer.items() if count == 12} == enough

    versions: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in erp.tariffs:
        versions[row["tariff_id"]].append(row)
    start = window_start(CUTOFF_DATE, HISTORY_MONTHS).isoformat()
    for tariff_id, rows in versions.items():
        assert len(rows) == 2, tariff_id
        first, second = sorted(rows, key=lambda row: row["valid_from"])
        assert first["valid_from"] == start
        assert first["valid_to"] == second["valid_from"]
        assert first["valid_from"] < second["valid_from"] <= CUTOFF_DATE.isoformat()
        assert second["valid_to"] == ""
        assert Decimal(second["base_amount"]) > Decimal(first["base_amount"])
        key = {(row["customer_id"], row["route_id"], row["service_type_id"]) for row in rows}
        assert len(key) == 1


def test_tariff_amount_comes_from_the_version_in_force(erp: ErpRun) -> None:
    index = TariffIndex(erp.tariffs)
    shipments = {row["shipment_id"]: row for row in erp.shipments}
    distance = {row["route_id"]: Decimal(row["distance_km"]) for row in build_routes().rows}
    priced = 0
    for line in erp.invoice_lines:
        if line["concept"] != "freight":
            continue
        shipment = shipments[line["shipment_id"]]
        version = index.find(
            shipment["customer_id"],
            shipment["route_id"],
            shipment["service_type_id"],
            date.fromisoformat(shipment["pickup_date"]),
        )
        if version is None:
            continue
        priced += 1
        expected = version.base_amount + version.per_km_amount * distance[shipment["route_id"]]
        assert Decimal(line["tariff_amount"]) == expected.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    assert priced > 0


def test_tariff_index_honours_the_exclusive_end() -> None:
    rows = (
        {
            "customer_id": "C",
            "route_id": "R",
            "service_type_id": "S",
            "valid_from": "2025-01-01",
            "valid_to": "2025-06-01",
            "base_amount": "1.00",
            "per_km_amount": "1.00",
            "fuel_surcharge_pct": "1.00",
        },
        {
            "customer_id": "C",
            "route_id": "R",
            "service_type_id": "S",
            "valid_from": "2025-06-01",
            "valid_to": "",
            "base_amount": "2.00",
            "per_km_amount": "2.00",
            "fuel_surcharge_pct": "2.00",
        },
    )
    index = TariffIndex(rows)
    assert index.find("C", "R", "S", date(2024, 12, 31)) is None
    assert index.find("C", "R", "S", date(2025, 5, 31)).base_amount == Decimal("1.00")
    assert index.find("C", "R", "S", date(2025, 6, 1)).base_amount == Decimal("2.00")
    assert index.find("C", "R", "X", date(2025, 6, 1)) is None


def test_no_business_date_after_the_cutoff(erp: ErpRun) -> None:
    cutoff = CUTOFF_DATE.isoformat()
    for rows, column in (
        (erp.invoices, "invoice_date"),
        (erp.payments, "paid_date"),
        (erp.settlements, "settled_date"),
        (erp.costs, "cost_date"),
        (erp.tariffs, "valid_from"),
    ):
        assert rows, column
        assert max(row[column] for row in rows) <= cutoff, column


def test_amounts_have_two_decimals_in_cop(erp: ErpRun) -> None:
    for rows in (
        erp.tariffs,
        erp.invoices,
        erp.invoice_lines,
        erp.payments,
        erp.settlements,
        erp.costs,
    ):
        for row in rows:
            assert row["currency_code"] == "COP"
            for column, value in row.items():
                if column.endswith(("_amount", "_pct")):
                    assert AMOUNT.match(value), (column, value)


def test_two_runs_are_identical() -> None:
    assert run_erp() == run_erp()


def test_combos_never_draw_and_keep_generation_order() -> None:
    combos = TariffCombos()
    rows = [
        {"customer_id": "C1", "route_id": f"R{index % 8}", "service_type_id": "S"}
        for index in range(20)
    ]
    for row in rows:
        combos.add(row)
    assert combos.by_customer() == {"C1": tuple((f"R{index}", "S") for index in range(6))}

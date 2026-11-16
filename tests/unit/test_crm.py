"""Pruebas de los extractos CRM: contactos, contratos y oportunidades sobre el
catálogo de clientes, con datos personales ficticios y deterministas."""

from __future__ import annotations

import re
from datetime import date
from itertools import product

from nexora.generators.config import (
    CONTACT_ROLES,
    CUTOFF_DATE,
    DRIVER_FAMILY_NAMES,
    DRIVER_GIVEN_NAMES,
    LEAD_SOURCES,
    LEAD_STAGES,
    SERVICE_TYPE_CATALOG,
    window_start,
)
from nexora.generators.crm import (
    CONTACTS_COLUMNS,
    CONTRACTS_COLUMNS,
    CRM_SOURCE_SYSTEM,
    LEADS_COLUMNS,
    build_contacts,
    build_contracts,
    build_leads,
)
from nexora.generators.dimensions import build_customers
from nexora.generators.fleet import build_drivers

SEED = 42
AMOUNT = re.compile(r"^-?\d+\.\d{2}$")
PCT = re.compile(r"^\d+\.\d{2}$")


def customer_ids() -> set[str]:
    return {row["customer_id"] for row in build_customers().rows}


def test_counts_are_450_contacts_150_contracts_500_leads() -> None:
    customers = build_customers()
    assert len(build_contacts(customers).rows) == 450
    assert len(build_contracts(customers).rows) == 150
    assert len(build_leads(customers, SEED).rows) == 500


def test_tables_declare_name_source_and_columns() -> None:
    customers = build_customers()
    tables = (
        (build_contacts(customers), "contacts", CONTACTS_COLUMNS),
        (build_contracts(customers), "contracts", CONTRACTS_COLUMNS),
        (build_leads(customers, SEED), "leads", LEADS_COLUMNS),
    )
    assert CRM_SOURCE_SYSTEM == "crm"
    for table, name, columns in tables:
        assert table.name == name
        assert table.source_system == "crm"
        assert table.columns == columns
        assert all(tuple(row) == columns for row in table.rows), name


def test_ids_are_unique() -> None:
    customers = build_customers()
    contacts = build_contacts(customers).rows
    contracts = build_contracts(customers).rows
    leads = build_leads(customers, SEED).rows
    for column in ("contact_id", "contact_document", "customer_phone", "customer_email"):
        assert len({row[column] for row in contacts}) == 450, column
    assert len({row["contract_id"] for row in contracts}) == 150
    assert len({row["lead_id"] for row in leads}) == 500
    assert (contacts[0]["contact_id"], contacts[-1]["contact_id"]) == ("CNT-00001", "CNT-00450")
    assert (contracts[0]["contract_id"], contracts[-1]["contract_id"]) == ("CTR-0001", "CTR-0150")
    assert (leads[0]["lead_id"], leads[-1]["lead_id"]) == ("LEA-0001", "LEA-0500")


def test_every_customer_id_exists() -> None:
    customers = build_customers()
    known = customer_ids()
    for table in (build_contacts(customers), build_contracts(customers)):
        assert {row["customer_id"] for row in table.rows} == known, table.name
    assert {row["customer_id"] for row in build_leads(customers, SEED).rows} <= known


def test_contacts_per_customer_and_city() -> None:
    customers = build_customers()
    city = {row["customer_id"]: row["city"] for row in customers.rows}
    contacts = build_contacts(customers).rows
    per_customer: dict[str, list[str]] = {}
    for row in contacts:
        per_customer.setdefault(row["customer_id"], []).append(row["contact_role"])
        assert row["city"] == city[row["customer_id"]], row
    assert all(roles == list(CONTACT_ROLES) for roles in per_customer.values())


def test_contact_names_only_from_pools() -> None:
    names = {f"{g} {f}" for g, f in product(DRIVER_GIVEN_NAMES, DRIVER_FAMILY_NAMES)}
    for row in build_contacts(build_customers()).rows:
        assert row["customer_contact_name"] in names, row


def test_contact_data_is_fictitious() -> None:
    contacts = build_contacts(build_customers()).rows
    for row in contacts:
        assert re.fullmatch(r"NXD-\d{7}", row["contact_document"]), row
        assert re.fullmatch(r"\+57 000 \d{3} \d{4}", row["customer_phone"]), row
        assert re.fullmatch(r"cnt\d{5}@example\.com", row["customer_email"]), row
    first = contacts[0]
    assert first["contact_document"] == "NXD-5000001"
    assert first["customer_phone"] == "+57 000 500 0001"
    assert first["customer_email"] == "cnt00001@example.com"


def test_contact_documents_and_phones_do_not_overlap_drivers() -> None:
    drivers = build_drivers().rows
    contacts = build_contacts(build_customers()).rows
    assert not {r["driver_document"] for r in drivers} & {r["contact_document"] for r in contacts}
    assert not {r["driver_phone"] for r in drivers} & {r["customer_phone"] for r in contacts}


def test_contracts_follow_customer_position() -> None:
    customers = build_customers().rows
    contracts = build_contracts(build_customers()).rows
    start = window_start()
    services = [s.service_type_id for s in SERVICE_TYPE_CATALOG]
    for index, (customer, row) in enumerate(zip(customers, contracts, strict=True)):
        assert row["customer_id"] == customer["customer_id"]
        assert row["service_type_id"] == services[index % len(services)]
        assert date.fromisoformat(row["start_date"]) <= start, row
        assert row["end_date"] == ""
        assert row["payment_term_days"] == customer["payment_term_days"]
        assert PCT.match(row["otif_target_pct"]), row
        assert row["contract_status"] == "active"


def test_amounts_have_two_decimals_and_cop() -> None:
    for row in build_leads(build_customers(), SEED).rows:
        assert AMOUNT.match(row["estimated_value_amount"]), row
        assert row["currency_code"] == "COP"


def test_leads_inside_window_and_catalog() -> None:
    start = window_start()
    for row in build_leads(build_customers(), SEED).rows:
        assert start <= date.fromisoformat(row["created_date"]) <= CUTOFF_DATE, row
        assert row["lead_source"] in LEAD_SOURCES
        assert row["stage"] in LEAD_STAGES


def test_expected_close_date_empty_only_when_lost() -> None:
    leads = build_leads(build_customers(), SEED).rows
    assert any(row["stage"] == "lost" for row in leads)
    for row in leads:
        if row["stage"] == "lost":
            assert row["expected_close_date"] == "", row
        else:
            close = date.fromisoformat(row["expected_close_date"])
            assert close > date.fromisoformat(row["created_date"]), row


def test_two_calls_are_equal() -> None:
    customers = build_customers()
    assert build_contacts(customers) == build_contacts(build_customers())
    assert build_contracts(customers) == build_contracts(build_customers())
    assert build_leads(customers, SEED) == build_leads(build_customers(), SEED)


def test_leads_depend_on_seed() -> None:
    customers = build_customers()
    assert build_leads(customers, SEED).rows != build_leads(customers, SEED + 1).rows

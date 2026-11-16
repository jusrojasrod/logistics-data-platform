"""CRM extracts derived from the customer catalog: contacts, contracts and leads.

Contacts and contracts are deterministic by position, like the fleet catalogs:
their attributes depend only on the index of the customer and of the row. Leads
are drawn with their own random generator, so they never consume the draws of
the operational tables.

Contact data is fictitious by construction: names combine the invented name
pools, documents use a prefix no authority issues, phones use an unassigned
prefix and emails a domain reserved for documentation.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from nexora.generators.config import (
    CONTACT_DOCUMENT_PREFIX,
    CONTACT_DOCUMENT_START,
    CONTACT_EMAIL_DOMAIN,
    CONTACT_ID_PREFIX,
    CONTACT_PHONE_PREFIX,
    CONTACT_ROLES,
    CONTACTS_PER_CUSTOMER,
    CONTRACT_ID_PREFIX,
    CONTRACT_OTIF_TARGET_PCT,
    CONTRACT_START_LEAD_DAYS,
    CONTRACT_START_STRIDE_DAYS,
    CONTRACT_STATUS,
    CONTRACTS_PER_CUSTOMER,
    CURRENCY_CODE,
    CUTOFF_DATE,
    DRIVER_FAMILY_NAMES,
    DRIVER_GIVEN_NAMES,
    HISTORY_MONTHS,
    LEAD_CLOSE_DAYS,
    LEAD_COUNT,
    LEAD_ID_PREFIX,
    LEAD_LOST_STAGE,
    LEAD_SOURCES,
    LEAD_STAGES,
    LEAD_VALUE_AMOUNT_RANGE,
    SERVICE_TYPE_CATALOG,
    window_start,
)
from nexora.generators.dimensions import DimensionTable

CRM_SOURCE_SYSTEM: str = "crm"

CONTACTS_ENTITY: str = "contacts"
CONTRACTS_ENTITY: str = "contracts"
LEADS_ENTITY: str = "leads"

CONTACTS_COLUMNS: tuple[str, ...] = (
    "contact_id",
    "customer_id",
    "customer_contact_name",
    "contact_role",
    "contact_document",
    "customer_phone",
    "customer_email",
    "city",
)

CONTRACTS_COLUMNS: tuple[str, ...] = (
    "contract_id",
    "customer_id",
    "service_type_id",
    "start_date",
    "end_date",
    "payment_term_days",
    "otif_target_pct",
    "contract_status",
)

LEADS_COLUMNS: tuple[str, ...] = (
    "lead_id",
    "customer_id",
    "created_date",
    "lead_source",
    "stage",
    "estimated_value_amount",
    "currency_code",
    "expected_close_date",
)


def _contact_name(index: int) -> str:
    given = DRIVER_GIVEN_NAMES[index % len(DRIVER_GIVEN_NAMES)]
    family = DRIVER_FAMILY_NAMES[(index // len(DRIVER_GIVEN_NAMES)) % len(DRIVER_FAMILY_NAMES)]
    return f"{given} {family}"


def build_contacts(customers: DimensionTable) -> DimensionTable:
    """Return the CRM contacts, CONTACTS_PER_CUSTOMER per customer."""
    rows: list[dict[str, str]] = []
    for customer in customers.rows:
        for role_index in range(CONTACTS_PER_CUSTOMER):
            index = len(rows)
            number = index + 1
            document = CONTACT_DOCUMENT_START + index
            rows.append(
                {
                    "contact_id": f"{CONTACT_ID_PREFIX}-{number:05d}",
                    "customer_id": customer["customer_id"],
                    "customer_contact_name": _contact_name(index),
                    "contact_role": CONTACT_ROLES[role_index % len(CONTACT_ROLES)],
                    "contact_document": f"{CONTACT_DOCUMENT_PREFIX}-{document:07d}",
                    "customer_phone": (
                        f"{CONTACT_PHONE_PREFIX} {document // 10_000 % 1_000:03d} "
                        f"{document % 10_000:04d}"
                    ),
                    "customer_email": f"cnt{number:05d}@{CONTACT_EMAIL_DOMAIN}",
                    "city": customer["city"],
                }
            )
    return DimensionTable(
        name=CONTACTS_ENTITY,
        source_system=CRM_SOURCE_SYSTEM,
        columns=CONTACTS_COLUMNS,
        rows=tuple(rows),
    )


def _contract_start(index: int, start: date) -> date:
    low, high = CONTRACT_START_LEAD_DAYS
    lead_days = low + (index * CONTRACT_START_STRIDE_DAYS) % (high - low + 1)
    return start - timedelta(days=lead_days)


def build_contracts(customers: DimensionTable) -> DimensionTable:
    """Return the CRM contracts, one active contract per customer.

    The service type of a contract follows the position of its customer in the
    catalog; the start date falls on or before the start of the window.
    """
    start = window_start(CUTOFF_DATE, HISTORY_MONTHS)
    rows: list[dict[str, str]] = []
    for index, customer in enumerate(customers.rows):
        for _ in range(CONTRACTS_PER_CUSTOMER):
            number = len(rows) + 1
            service_type = SERVICE_TYPE_CATALOG[index % len(SERVICE_TYPE_CATALOG)]
            otif = CONTRACT_OTIF_TARGET_PCT[index % len(CONTRACT_OTIF_TARGET_PCT)]
            rows.append(
                {
                    "contract_id": f"{CONTRACT_ID_PREFIX}-{number:04d}",
                    "customer_id": customer["customer_id"],
                    "service_type_id": service_type.service_type_id,
                    "start_date": _contract_start(index, start).isoformat(),
                    "end_date": "",
                    "payment_term_days": customer["payment_term_days"],
                    "otif_target_pct": f"{otif:.2f}",
                    "contract_status": CONTRACT_STATUS,
                }
            )
    return DimensionTable(
        name=CONTRACTS_ENTITY,
        source_system=CRM_SOURCE_SYSTEM,
        columns=CONTRACTS_COLUMNS,
        rows=tuple(rows),
    )


def build_leads(customers: DimensionTable, seed: int) -> DimensionTable:
    """Return the CRM leads over the existing customers, created inside the window.

    Every lead consumes the same fixed sequence of draws from its own generator,
    so the result depends only on ``seed`` and the customer catalog.
    """
    rng = random.Random(f"crm-{seed}")
    customer_ids = [row["customer_id"] for row in customers.rows]
    start = window_start(CUTOFF_DATE, HISTORY_MONTHS)
    window_days = (CUTOFF_DATE - start).days
    value_low, value_high = LEAD_VALUE_AMOUNT_RANGE
    close_low, close_high = LEAD_CLOSE_DAYS
    rows: list[dict[str, str]] = []
    for index in range(LEAD_COUNT):
        customer_id = rng.choice(customer_ids)
        created = start + timedelta(days=rng.randint(0, window_days))
        source = rng.choice(LEAD_SOURCES)
        stage = rng.choice(LEAD_STAGES)
        value = rng.randint(value_low, value_high)
        close = created + timedelta(days=rng.randint(close_low, close_high))
        rows.append(
            {
                "lead_id": f"{LEAD_ID_PREFIX}-{index + 1:04d}",
                "customer_id": customer_id,
                "created_date": created.isoformat(),
                "lead_source": source,
                "stage": stage,
                "estimated_value_amount": f"{value:.2f}",
                "currency_code": CURRENCY_CODE,
                "expected_close_date": "" if stage == LEAD_LOST_STAGE else close.isoformat(),
            }
        )
    return DimensionTable(
        name=LEADS_ENTITY,
        source_system=CRM_SOURCE_SYSTEM,
        columns=LEADS_COLUMNS,
        rows=tuple(rows),
    )

"""Static catalogs and generation parameters for the synthetic dataset.

Every value in this module is synthetic: company and carrier names are invented
and the temporal anchor is fixed, so two runs on different days produce
byte-identical files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Fixed temporal anchor: the last day covered by the generated history. It never
# depends on the system clock, so the reproducible checks do not change per day.
CUTOFF_DATE: date = date(2026, 11, 9)

# Length of the generated history, in whole calendar months ending at CUTOFF_DATE.
HISTORY_MONTHS: int = 24

# Relative demand weight per calendar month (1.0 = average month). It shapes the
# pickup dates: December peaks, January drops after the holiday season.
MONTHLY_SEASONALITY: dict[int, float] = {
    1: 0.72,
    2: 0.86,
    3: 0.94,
    4: 0.97,
    5: 1.02,
    6: 0.99,
    7: 1.00,
    8: 1.03,
    9: 0.98,
    10: 1.06,
    11: 1.10,
    12: 1.35,
}

# Rainy season months, which add transit variability to the generated shipments.
RAINY_MONTHS: frozenset[int] = frozenset({4, 5, 10, 11})


def seasonal_weight(month: int) -> float:
    """Return the relative demand weight for a calendar month (1-12)."""
    try:
        return MONTHLY_SEASONALITY[month]
    except KeyError as error:
        raise ValueError(f"month out of range: {month}") from error


def window_start(cutoff: date = CUTOFF_DATE, months: int = HISTORY_MONTHS) -> date:
    """Return the first day of the generation window.

    The window covers ``months`` whole calendar months and ends on ``cutoff``.
    """
    if months < 1:
        raise ValueError(f"months must be positive: {months}")
    month_index = cutoff.year * 12 + (cutoff.month - 1) - (months - 1)
    return date(month_index // 12, month_index % 12 + 1, 1)


# Defect injection rates, consumed by the defect generator. Rates apply to the
# base rows of each profile; injected rows live on top of that base.
DUPLICATE_RATE: float = 0.02  # DQ-001: byte-identical duplicate rows.
CRITICAL_NULL_RATE: float = 0.01  # DQ-004, DQ-005 and DQ-011 as alerts.
DELIVERED_BEFORE_PICKUP_RATE: float = 0.008  # DQ-002: delivered before pickup.

# Columns that admit the injected critical null. The rate is split evenly across
# them and every affected row carries exactly one null.
CRITICAL_NULL_COLUMNS: tuple[str, ...] = (
    "promised_delivery_ts",
    "customer_id",
    "carrier_id",
)

# Late arrivals when publishing into the operational TMS: about 2 % of the
# shipments become available between 2 and 7 days after their pickup date. The
# choice is a hash of shipment_id, so it is deterministic and every copy of a
# shipment shares the same availability date.
LATE_ARRIVAL_PCT: int = 2
LATE_ARRIVAL_LAG_DAYS: tuple[int, int] = (2, 7)


@dataclass(frozen=True)
class City:
    """A city served by the operation, with the coordinates of its centre."""

    name: str
    region: str
    latitude: float
    longitude: float


CITY_CATALOG: tuple[City, ...] = (
    City("Bogotá", "andina", 4.711, -74.072),
    City("Medellín", "andina", 6.244, -75.581),
    City("Cali", "pacifico", 3.451, -76.532),
    City("Barranquilla", "caribe", 10.963, -74.796),
    City("Cartagena", "caribe", 10.391, -75.479),
    City("Bucaramanga", "andina", 7.119, -73.122),
    City("Pereira", "andina", 4.813, -75.696),
    City("Manizales", "andina", 5.070, -75.513),
    City("Armenia", "andina", 4.533, -75.681),
    City("Cúcuta", "andina", 7.889, -72.496),
    City("Ibagué", "andina", 4.438, -75.232),
    City("Villavicencio", "orinoquia", 4.142, -73.626),
    City("Neiva", "andina", 2.934, -75.280),
    City("Santa Marta", "caribe", 11.240, -74.199),
    City("Valledupar", "caribe", 10.463, -73.253),
    City("Montería", "caribe", 8.748, -75.881),
    City("Pasto", "pacifico", 1.214, -77.278),
    City("Tunja", "andina", 5.535, -73.367),
)


# Customer catalog parameters. Name pools are combined by position, so the
# number of distinct names is the product of both lengths.
CUSTOMER_COUNT: int = 150
CUSTOMER_SEGMENTS: tuple[str, ...] = (
    "retail",
    "wholesale",
    "manufacturing",
    "ecommerce",
    "pharma",
)
CUSTOMER_INDUSTRIES: tuple[str, ...] = (
    "food",
    "beverage",
    "retail",
    "pharma",
    "automotive",
    "technology",
    "agro",
    "chemical",
)
CUSTOMER_PAYMENT_TERMS_DAYS: tuple[int, ...] = (30, 45, 60, 90)
CUSTOMER_NAME_PREFIXES: tuple[str, ...] = (
    "Comercializadora",
    "Distribuidora",
    "Almacenes",
    "Industrias",
    "Agropecuaria",
    "Farmacias",
    "Mercados",
    "Bodegas",
    "Suministros",
    "Exportadora",
)
CUSTOMER_NAME_ROOTS: tuple[str, ...] = (
    "Aurora",
    "El Roble",
    "Monte Verde",
    "Los Pinos",
    "Nueva Era",
    "El Faro",
    "La Cumbre",
    "Pradera",
    "Río Claro",
    "Estrella",
    "Horizonte",
    "Alameda",
    "El Mirador",
    "Las Acacias",
    "El Trigal",
    "La Esperanza",
    "Villa Nueva",
    "El Portal",
)


# Carrier catalog parameters. The first carriers are owned fleet; the rest are
# third parties, as declared by the execution-model story of the operation.
CARRIER_COUNT: int = 40
OWNED_CARRIER_COUNT: int = 10
CARRIER_SERVICE_LEVELS: tuple[str, ...] = (
    "standard",
    "standard",
    "premium",
    "economy",
    "standard",
)
CARRIER_RATE_INDEX_MIN: float = 0.85
CARRIER_RATE_INDEX_STEP: float = 0.05
CARRIER_NAME_PREFIXES: tuple[str, ...] = (
    "Transportes",
    "Logística",
    "Carga",
    "Flota",
    "Rutas",
    "Cargo",
    "Tránsito",
    "Movilidad",
)
CARRIER_NAME_ROOTS: tuple[str, ...] = (
    "Cordillera",
    "del Norte",
    "del Sur",
    "Andina",
    "Pacífica",
    "Oriental",
    "Central",
    "del Caribe",
)


# Route catalog parameters. A route is an ordered city pair; the road distance
# and the estimated transit time are derived from the city coordinates.
EARTH_RADIUS_KM: float = 6371.0
ROAD_FACTOR: float = 1.28
AVERAGE_SPEED_KMH: float = 62.0
LOADING_HOURS: float = 0.75
PRIMARY_ROAD_MIN_KM: float = 700.0
SECONDARY_ROAD_MIN_KM: float = 300.0


@dataclass(frozen=True)
class ServiceType:
    """A service level offered to customers, with its promised delivery SLA."""

    service_type_id: str
    service_type_name: str
    sla_hours: int


SERVICE_TYPE_CATALOG: tuple[ServiceType, ...] = (
    ServiceType("SVC-STD", "standard", 72),
    ServiceType("SVC-EXP", "express", 24),
    ServiceType("SVC-ECO", "economy", 120),
    ServiceType("SVC-REF", "refrigerated", 48),
    ServiceType("SVC-FTL", "full_truckload", 96),
    ServiceType("SVC-LTL", "less_than_truckload", 72),
)


# Fleet catalog parameters. Vehicles and drivers belong only to the owned
# carriers and are split evenly among them, in contiguous blocks of ids.
VEHICLES_PER_OWNED_CARRIER: int = 30
DRIVERS_PER_OWNED_CARRIER: int = 40


@dataclass(frozen=True)
class VehicleType:
    """A vehicle class of the owned fleet, with its nominal capacity and yield."""

    vehicle_type: str
    capacity_kg: float
    capacity_m3: float
    fuel_type: str
    km_per_gallon: float


VEHICLE_TYPE_CATALOG: tuple[VehicleType, ...] = (
    VehicleType("tractor_trailer", 34000.0, 90.00, "diesel", 7.80),
    VehicleType("rigid_truck", 17000.0, 48.00, "diesel", 10.50),
    VehicleType("rigid_truck", 17000.0, 48.00, "diesel", 10.50),
    VehicleType("medium_truck", 8500.0, 32.00, "diesel", 14.20),
    VehicleType("light_truck", 3500.0, 18.00, "natural_gas", 21.00),
)
# Each vehicle deviates from the nominal yield of its class by a position-based
# step, so the fleet is not perfectly uniform.
VEHICLE_YIELD_STEPS: tuple[float, ...] = (-0.40, -0.20, 0.00, 0.20, 0.40)

# Purchase and hire dates are spread by position over fixed windows that end
# before CUTOFF_DATE, so the catalogs never depend on the system clock.
FLEET_PURCHASE_START: date = date(2016, 1, 1)
FLEET_PURCHASE_SPAN_DAYS: int = 3287
DRIVER_HIRE_START: date = date(2014, 1, 1)
DRIVER_HIRE_SPAN_DAYS: int = 4383
FLEET_DATE_STRIDE_DAYS: int = 97

# Plate, document, phone and email formats of the fleet catalogs. They are
# deliberately unofficial: the plate and document prefixes are not issued by
# any authority, the phone prefix is not assigned and the email domain is
# reserved for documentation.
VEHICLE_PLATE_PREFIX: str = "NXV"
DRIVER_DOCUMENT_PREFIX: str = "NXD"
DRIVER_PHONE_PREFIX: str = "+57 000"
DRIVER_EMAIL_DOMAIN: str = "example.com"
DRIVER_LICENSE_TYPES: tuple[str, ...] = ("C2", "C3", "C2", "C1")

# Driver name pools, combined by position. Surnames are invented compounds, so
# no combination matches a real person on purpose.
DRIVER_GIVEN_NAMES: tuple[str, ...] = (
    "Andrés",
    "Lucía",
    "Mateo",
    "Valentina",
    "Julián",
    "Camila",
    "Tomás",
    "Isabela",
    "Esteban",
    "Mariana",
    "Felipe",
    "Daniela",
    "Samuel",
    "Paula",
    "Martín",
    "Sofía",
    "Emilio",
    "Laura",
    "Gabriel",
    "Natalia",
)
DRIVER_FAMILY_NAMES: tuple[str, ...] = (
    "Montealto",
    "Pradoverde",
    "Rocaclara",
    "Sierralta",
    "Lagomar",
    "Campoluz",
    "Peñarroble",
    "Vientosur",
    "Brisamar",
    "Arenalta",
    "Fuentelar",
    "Valleluz",
    "Cumbrera",
    "Olivaral",
    "Piedrasol",
    "Riomenor",
    "Cañaverde",
    "Lomaclara",
    "Nevadal",
    "Bosquerial",
)


# Child rows per base shipment, as averages over the population. Stops are a
# pickup, a delivery and up to two transit hubs; every shipment gets an accepted
# tender and half of them a previous offer to another carrier.
EVENTS_PER_SHIPMENT: int = 10
STOPS_PER_SHIPMENT: int = 3
TENDERS_PER_SHIPMENT: float = 1.5
INCIDENTS_PER_SHIPMENT: float = 0.1

# Trips, legs and fuel purchases exist only for the owned fleet. After a loaded
# trip, this share of trips adds an empty repositioning trip without shipments.
REPOSITIONING_TRIP_RATE: float = 0.03

# Kilometres covered between two fuel purchases of a trip.
FUEL_STOP_KM: float = 600.0

# Currency of every amount of the operation.
CURRENCY_CODE: str = "COP"

# Defect injection rates of the operational tables, over the base rows of each
# table. Every affected row carries a single defect; the critical nulls of a
# table alternate between its two columns.
EVENT_DUPLICATE_RATE: float = 0.02  # DQ-001: byte-identical copy of an event.
EVENT_CRITICAL_NULL_RATE: float = 0.01
EVENT_IMPOSSIBLE_DATE_RATE: float = 0.005
FUEL_CRITICAL_NULL_RATE: float = 0.01
TENDER_NO_RESPONSE_RATE: float = 0.02  # DQ-014: tender without an answer.
INCIDENT_UNCLOSED_RATE: float = 0.01

EVENT_CRITICAL_NULL_COLUMNS: tuple[str, ...] = ("event_type", "location_city")
FUEL_CRITICAL_NULL_COLUMNS: tuple[str, ...] = ("gallons", "total_amount")

# An impossible event date lands this many days after the real one.
IMPOSSIBLE_DATE_SHIFT_DAYS: int = 36_525

# Only incidents reported this many days before the end of the cutoff date are
# old enough to be expected closed, so only they can lose their resolution.
UNCLOSED_MIN_AGE_DAYS: int = 30


# Late files of the ERP and CRM extracts. A row is late when the first 8 hex
# digits of the SHA-256 of its natural key, read as an integer, modulo 100 fall
# below LATE_FILE_PCT. Late rows are written only to
# LATE_DIRECTORY/<entity>LATE_FILE_SUFFIX.csv and never to the main file.
LATE_FILE_PCT: int = 3
LATE_DIRECTORY: str = "late"
LATE_FILE_SUFFIX: str = "_late"


# Customer tariffs. Each customer is priced on the first route and service
# combinations of its shipments, with two versions per combination: the first
# one valid from the start of the window and the second one, with an increase,
# valid from a day drawn inside the window.
TARIFF_COMBOS_PER_CUSTOMER: int = 6
TARIFF_VERSIONS: int = 2
TARIFF_ID_PREFIX: str = "TRF"
TARIFF_BASE_AMOUNT_RANGE: tuple[int, int] = (180_000, 450_000)
TARIFF_PER_KM_AMOUNT_RANGE: tuple[int, int] = (2_800, 5_200)
TARIFF_FUEL_SURCHARGE_PCT_RANGE: tuple[float, float] = (6.0, 14.0)
TARIFF_INCREASE_PCT_RANGE: tuple[float, float] = (3.0, 8.0)

# List price used when a customer has no tariff for a route and service.
LIST_BASE_AMOUNT: float = 320_000.0
LIST_PER_KM_AMOUNT: float = 4_600.0
LIST_FUEL_SURCHARGE_PCT: float = 12.0


# Invoicing. Only delivered shipments are invoiced, a few days after delivery;
# invoices dated after CUTOFF_DATE are not issued yet. A share of the lines is
# billed with a small variation against the tariff amount.
INVOICE_ID_PREFIX: str = "INV"
INVOICE_LINE_CONCEPTS: tuple[str, ...] = ("freight", "fuel_surcharge")
INVOICE_DELAY_DAYS: tuple[int, int] = (0, 3)
BILLING_VARIANCE_RATE: float = 0.10
BILLING_VARIANCE_PCT_RANGE: tuple[float, float] = (1.0, 8.0)
# Synthetic value-added tax applied to every invoice line.
TAX_PCT: float = 19.0
INVOICE_STATUS_ISSUED: str = "issued"
INVOICE_STATUS_PAID: str = "paid"


# Payments. Most invoices receive a single payment for their total, around the
# due date and never before the invoice date; payments after CUTOFF_DATE do not
# exist yet.
PAYMENT_ID_PREFIX: str = "PAY"
PAYMENT_RATE: float = 0.95
PAYMENT_OFFSET_DAYS: tuple[int, int] = (-10, 20)
PAYMENT_METHODS: tuple[str, ...] = ("bank_transfer", "bank_transfer", "check", "card")


# Carrier settlements of the third-party carriers, one freight line per
# delivered shipment. Late deliveries carry a deduction over the payable amount.
SETTLEMENT_ID_PREFIX: str = "STL"
SETTLEMENT_LINE_ID_PREFIX: str = "STLL"
SETTLEMENT_CONCEPT: str = "freight"
SETTLEMENT_STATUS: str = "settled"
SETTLEMENT_DELAY_DAYS: tuple[int, int] = (7, 20)
SETTLEMENT_PER_KM_AMOUNT: float = 3_400.0
SETTLEMENT_FUEL_SURCHARGE_PCT: float = 9.0
SETTLEMENT_LATE_DEDUCTION_PCT: float = 5.0


# Fleet costs of the owned carriers, one row per component and shipment, dated
# on the pickup date. Tolls depend on the road type of the route.
SHIPMENT_COST_ID_PREFIX: str = "SHC"
COST_COMPONENTS: tuple[str, ...] = ("fuel", "driver", "tolls")
COST_PER_KM_AMOUNT: dict[str, float] = {"fuel": 1_350.0, "driver": 620.0}
TOLL_PER_KM_AMOUNT: dict[str, float] = {
    "primary": 410.0,
    "secondary": 260.0,
    "tertiary": 90.0,
}
COST_CENTER_PREFIX: str = "CC"


# CRM contacts. Names combine the driver name pools by position; documents,
# phones and emails use the same unofficial formats as the fleet catalogs, with
# a document range that does not overlap the drivers.
CONTACTS_PER_CUSTOMER: int = 3
CONTACT_ID_PREFIX: str = "CNT"
CONTACT_ROLES: tuple[str, ...] = ("logistics_manager", "procurement", "accounts_payable")
CONTACT_DOCUMENT_PREFIX: str = "NXD"
CONTACT_DOCUMENT_START: int = 5_000_001
CONTACT_PHONE_PREFIX: str = "+57 000"
CONTACT_EMAIL_DOMAIN: str = "example.com"


# CRM contracts, one active contract per customer that starts on or before the
# start of the window and has no end date.
CONTRACTS_PER_CUSTOMER: int = 1
CONTRACT_ID_PREFIX: str = "CTR"
CONTRACT_START_LEAD_DAYS: tuple[int, int] = (0, 720)
CONTRACT_OTIF_TARGET_PCT: tuple[float, ...] = (92.0, 95.0, 97.0)
CONTRACT_STATUS: str = "active"


# CRM leads over the existing customers, created inside the window. Lost leads
# have no expected close date.
LEAD_COUNT: int = 500
LEAD_ID_PREFIX: str = "LEA"
LEAD_SOURCES: tuple[str, ...] = ("referral", "website", "trade_show", "outbound", "partner")
LEAD_STAGES: tuple[str, ...] = ("prospect", "qualified", "proposal", "negotiation", "won", "lost")
LEAD_LOST_STAGE: str = "lost"
LEAD_VALUE_AMOUNT_RANGE: tuple[int, int] = (5_000_000, 250_000_000)
LEAD_CLOSE_DAYS: tuple[int, int] = (15, 120)

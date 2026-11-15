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

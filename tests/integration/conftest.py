"""Configuración de las pruebas de integración contra la base PostgreSQL local.

Las pruebas de este directorio solo se ejecutan con ``NEXORA_INTEGRATION=1``
(``make test-integration``); en otro caso se informan como omitidas. Se conectan
como superusuario con ``POSTGRES_USER``, ``POSTGRES_PASSWORD`` y
``POSTGRES_PORT`` del entorno o de ``.env`` y trabajan en esquemas temporales
``it_<hex>_tms``, ``it_<hex>_raw`` e ``it_<hex>_meta``, que se borran siempre al
terminar cada prueba. Los esquemas ``tms``, ``raw`` y ``meta`` no se tocan.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg import sql

from nexora.generators.dataset import write_dataset

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]
ENV_FLAG = "NEXORA_INTEGRATION"

SCHEMA_PREFIX = "it_"
SCALE = "demo"
SHIPMENTS = 500
SEED = 42
MONTHS = 1

DEFAULT_USER = "nexora"
DEFAULT_SECRET = "change-me"
DEFAULT_PORT = "5432"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Omite las pruebas de este directorio salvo con ``NEXORA_INTEGRATION=1``."""
    if os.environ.get(ENV_FLAG) == "1":
        return
    skip = pytest.mark.skip(reason=f"pruebas de integración: requieren {ENV_FLAG}=1")
    for item in items:
        if INTEGRATION_DIR in Path(item.path).resolve().parents:
            item.add_marker(skip)


def _setting(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    from_file = dotenv_values(REPO_ROOT / ".env").get(name)
    return from_file if from_file else default


@dataclass(frozen=True)
class Schemas:
    """Esquemas temporales de una prueba."""

    tms: str
    raw: str
    meta: str

    @property
    def all(self) -> tuple[str, str, str]:
        return (self.tms, self.raw, self.meta)


@pytest.fixture(scope="session")
def pg() -> Iterator[psycopg.Connection]:
    """Conexión de superusuario en modo autocommit a la base local."""
    conn = psycopg.connect(
        host="127.0.0.1",
        port=int(_setting("POSTGRES_PORT", DEFAULT_PORT)),
        dbname=_setting("POSTGRES_DB", DEFAULT_USER),
        user=_setting("POSTGRES_USER", DEFAULT_USER),
        autocommit=True,
        **{"password": _setting("POSTGRES_PASSWORD", DEFAULT_SECRET)},
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def schemas(pg: psycopg.Connection) -> Iterator[Schemas]:
    """Crea ``it_<hex>_tms``, ``it_<hex>_raw`` e ``it_<hex>_meta`` y los borra al final."""
    prefix = f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"
    created = Schemas(tms=f"{prefix}_tms", raw=f"{prefix}_raw", meta=f"{prefix}_meta")
    try:
        for name in created.all:
            pg.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
        yield created
    finally:
        if pg.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            pg.rollback()
        for name in created.all:
            pg.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(name)))


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    """Carpeta con todos los CSV del generador y su manifiesto.

    500 envíos base con defectos (semilla 42, un mes), las dimensiones, la flota
    y las entidades que cuelgan de envíos y viajes, con sus defectos.
    """
    write_dataset(tmp_path, SCALE, SHIPMENTS, SEED, MONTHS)
    return tmp_path

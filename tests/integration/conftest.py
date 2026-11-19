"""Configuración de las pruebas de integración contra las bases locales.

Las pruebas de este directorio solo se ejecutan con ``NEXORA_INTEGRATION=1``
(``make test-integration``); en otro caso se informan como omitidas. PostgreSQL
se conecta como superusuario con ``POSTGRES_USER``, ``POSTGRES_PASSWORD`` y
``POSTGRES_PORT`` del entorno o de ``.env`` y usa los esquemas temporales
``it_<hex>_tms``, ``it_<hex>_raw`` e ``it_<hex>_meta``, borrados siempre al
terminar cada prueba. Los esquemas ``tms``, ``raw`` y ``meta`` no se tocan.

MongoDB se conecta con el usuario de aplicación ``nexora_app`` (nunca con root)
y aísla cada prueba en colecciones ``it_<hex>_*`` dentro de ``raw`` y ``meta``.
El usuario tiene ``readWrite`` solo en esas dos bases, así que no puede crear
bases nuevas: las colecciones temporales se crean dentro de ellas con el
validador y los índices copiados de las colecciones reales, y se borran al
terminar. No se crean usuarios ni se amplían roles durante la prueba.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg import sql
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from nexora.generators.dataset import write_dataset
from nexora.ingestion.api.landing import (
    DRIFT_COLLECTION,
    DRIFT_DATABASE,
    PAYLOADS_COLLECTION,
    PAYLOADS_DATABASE,
)

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
MONGO_APP_USER = "nexora_app"
MONGO_HOST = "127.0.0.1"
MONGO_PORT = "27017"


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


@dataclass(frozen=True)
class MongoCollections:
    """Colecciones temporales de una prueba de aterrizaje."""

    prefix: str
    payloads: Collection
    drift: Collection


def _copy_contract(source: Collection, target_db: Database, target_name: str) -> Collection:
    """Copia el validador y los índices de una colección real a una temporal."""
    info = next(source.database.list_collections(filter={"name": source.name}), None)
    if info is None:
        raise RuntimeError(f"{source.full_name} no existe: no se puede replicar su contrato")
    options = info.get("options", {})
    settings = {
        name: options[name]
        for name in ("validator", "validationLevel", "validationAction")
        if name in options
    }
    target = target_db.create_collection(target_name, **settings)
    for index in source.list_indexes():
        if index["name"] == "_id_":
            continue
        index_options: dict[str, Any] = {"name": index["name"]}
        if index.get("unique"):
            index_options["unique"] = True
        target.create_index(list(index["key"].items()), **index_options)
    return target


@pytest.fixture(scope="session")
def mongo_app() -> Iterator[MongoClient]:
    """Conexión de aplicación a MongoDB, sin root y sin crear usuarios."""
    client = MongoClient(
        host=MONGO_HOST,
        port=int(_setting("MONGO_PORT", MONGO_PORT)),
        username=MONGO_APP_USER,
        password=_setting("MONGO_APP_PASSWORD", DEFAULT_SECRET),
        authSource="admin",
        serverSelectionTimeoutMS=5000,
        tz_aware=True,
    )
    try:
        client.admin.command("ping")
        yield client
    finally:
        client.close()


@pytest.fixture()
def mongo_api_collections(mongo_app: MongoClient) -> Iterator[MongoCollections]:
    """Crea ``it_<hex>_*`` en ``raw`` y ``meta`` y los borra siempre al terminar."""
    prefix = f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}_"
    raw = mongo_app[PAYLOADS_DATABASE]
    meta = mongo_app[DRIFT_DATABASE]
    payloads = _copy_contract(raw[PAYLOADS_COLLECTION], raw, f"{prefix}{PAYLOADS_COLLECTION}")
    drift = _copy_contract(meta[DRIFT_COLLECTION], meta, f"{prefix}{DRIFT_COLLECTION}")
    try:
        yield MongoCollections(prefix=prefix, payloads=payloads, drift=drift)
    finally:
        raw.drop_collection(payloads.name)
        meta.drop_collection(drift.name)
        remaining = [
            f"{db.name}.{name}"
            for db in (raw, meta)
            for name in db.list_collection_names()
            if name.startswith(prefix)
        ]
        assert not remaining, f"quedaron colecciones temporales: {remaining}"


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    """Carpeta con todos los CSV del generador y su manifiesto.

    500 envíos base con defectos (semilla 42, un mes), las dimensiones, la flota
    y las entidades que cuelgan de envíos y viajes, con sus defectos.
    """
    write_dataset(tmp_path, SCALE, SHIPMENTS, SEED, MONTHS)
    return tmp_path

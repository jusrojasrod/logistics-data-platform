"""Repositorio de los recursos del mock TMS.

``InMemoryRepository`` sostiene las pruebas unitarias sin base de datos;
``PostgresRepository`` lee el esquema ``tms`` con el usuario de solo lectura.
Ambos devuelven filas con los valores nativos de la fuente: la conversión al
contrato JSON la hace :mod:`nexora.generators.api.resources`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from nexora.common.api_contracts import ResourceContract

TMS_SCHEMA: str = "tms"
ROW_ID: str = "tms_row_id"


class ResourceRepository(ABC):
    """Fuente de filas de un recurso, contada y paginada por posición."""

    @abstractmethod
    def count(self, contract: ResourceContract) -> int:
        """Devuelve el número de filas del recurso."""

    @abstractmethod
    def page(
        self, contract: ResourceContract, page: int, page_size: int
    ) -> list[dict[str, object]]:
        """Devuelve una página de filas con los valores nativos de la fuente."""


class InMemoryRepository(ResourceRepository):
    """Repositorio en memoria, con filas añadidas explícitamente por recurso."""

    def __init__(self, rows: Mapping[str, Sequence[Mapping[str, object]]] | None = None) -> None:
        self._rows: dict[str, list[dict[str, object]]] = {
            resource: [dict(row) for row in data] for resource, data in (rows or {}).items()
        }

    def add(self, resource: str, rows: Iterable[Mapping[str, object]]) -> None:
        """Añade filas a un recurso, conservando las repeticiones de clave natural."""
        self._rows.setdefault(resource, []).extend(dict(row) for row in rows)

    def count(self, contract: ResourceContract) -> int:
        return len(self._rows.get(contract.resource, ()))

    def page(
        self, contract: ResourceContract, page: int, page_size: int
    ) -> list[dict[str, object]]:
        rows = self._rows.get(contract.resource, ())
        start = (page - 1) * page_size
        return [dict(row) for row in rows[start : start + page_size]]


class PostgresRepository(ResourceRepository):
    """Repositorio sobre el esquema ``tms``, en orden de inserción estable."""

    def __init__(self, dsn: str, schema: str = TMS_SCHEMA) -> None:
        self._dsn = dsn
        self._schema = schema

    def _table(self, contract: ResourceContract) -> sql.Identifier:
        return sql.Identifier(self._schema, contract.resource)

    def count(self, contract: ResourceContract) -> int:
        statement = sql.SQL("select count(*) from {table}").format(table=self._table(contract))
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(statement)
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def page(
        self, contract: ResourceContract, page: int, page_size: int
    ) -> list[dict[str, object]]:
        columns = sql.SQL(", ").join(sql.Identifier(name) for name in contract.field_names)
        statement = sql.SQL(
            "select {columns} from {table} order by {row_id} offset %s limit %s"
        ).format(
            columns=columns,
            table=self._table(contract),
            row_id=sql.Identifier(ROW_ID),
        )
        offset = (page - 1) * page_size
        with psycopg.connect(self._dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(statement, (offset, page_size))
            return [dict(row) for row in cur.fetchall()]

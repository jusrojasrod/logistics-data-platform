"""Línea de ordenes de la vía de ingesta por API.

Los tres subcomandos comparten la configuración del proyecto. Las DSN se leen
del entorno salvo que se indique lo contrario; nunca se imprimen. Las consultas
de ``freshness`` y ``verify-keys`` solo leen, aunque usen la DSN de aplicación.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from nexora.common.config import Settings, settings
from nexora.generators.api.repository import PostgresRepository
from nexora.ingestion.api.drift import ApiSchemaDriftError
from nexora.ingestion.api.runner import (
    DATASET_NAMES,
    TMS_DATASET_NAMES,
    EmptySourceTableError,
    KeyMismatchError,
    UnknownDatasetError,
    dataset_slos,
    freshness_rows,
    mongo_collections,
    postgres_connection,
    run_extraction,
    verify_keys,
)

PROG = "python -m nexora.ingestion.api.cli"


def build_parser() -> argparse.ArgumentParser:
    """Construye el analizador con los tres subcomandos de la vía API."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Ingesta por API: extracción, claves y frescura",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    extract = subcommands.add_parser("extract", help="extrae y aterriza un dataset de API")
    extract.add_argument("--dataset", required=True, choices=DATASET_NAMES)
    extract.add_argument("--run-id", required=True)
    extract.add_argument("--pg-dsn", default=None, help="DSN alternativa de PostgreSQL")
    extract.add_argument("--mongo-dsn", default=None, help="DSN alternativa de MongoDB")
    extract.add_argument("--on-date", default=None, help="fecha ISO de las fuentes simuladas")

    keys = subcommands.add_parser("verify-keys", help="compara las claves con la tabla tms")
    keys.add_argument("--dataset", required=True, choices=TMS_DATASET_NAMES)
    keys.add_argument("--run-id", required=True)
    keys.add_argument("--pg-dsn", default=None, help="DSN alternativa de PostgreSQL")
    keys.add_argument("--mongo-dsn", default=None, help="DSN alternativa de MongoDB")
    keys.add_argument("--tms-schema", default="tms", help="esquema de las tablas de origen")

    freshness = subcommands.add_parser("freshness", help="frescura y SLO de cada dataset")
    freshness.add_argument("--pg-dsn", default=None, help="DSN alternativa de PostgreSQL")
    freshness.add_argument("--mongo-dsn", default=None, help="DSN alternativa de MongoDB")

    return parser


def main(argv: Sequence[str] | None = None, *, config: Settings | None = None) -> int:
    """Ejecuta el subcomando indicado; devuelve el estado de salida."""
    args = build_parser().parse_args(argv)
    conf = config if config is not None else settings
    try:
        if args.command == "extract":
            with (
                postgres_connection(conf, args.pg_dsn) as connection,
                mongo_collections(conf, args.mongo_dsn) as (payloads, drift_rows),
            ):
                result = run_extraction(
                    args.dataset,
                    run_id=args.run_id,
                    connection=connection,
                    payloads=payloads,
                    drift_rows=drift_rows,
                    config=conf,
                    on_date=args.on_date,
                )
            print(
                f"{result.dataset}: {result.status}; páginas = {result.pages}, "
                f"documentos = {result.documents}, drift = {result.drift_count}"
            )
            return 0

        if args.command == "verify-keys":
            with mongo_collections(conf, args.mongo_dsn) as (payloads, _):
                counts = verify_keys(
                    args.dataset,
                    run_id=args.run_id,
                    repository=PostgresRepository(
                        args.pg_dsn or conf.etl_conninfo(), schema=args.tms_schema
                    ),
                    payloads=payloads,
                    tms_schema=args.tms_schema,
                )
            print(counts)
            return 0

        with (
            postgres_connection(conf, args.pg_dsn) as connection,
            mongo_collections(conf, args.mongo_dsn) as (payloads, _),
        ):
            slos = dataset_slos(connection)
            rows = freshness_rows(slos=slos, payloads=payloads)
        for row in rows:
            print(row)
        return 0
    except (
        ApiSchemaDriftError,
        EmptySourceTableError,
        KeyMismatchError,
        UnknownDatasetError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # La CLI informa del fallo y devuelve un estado sin exponer la DSN.
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]

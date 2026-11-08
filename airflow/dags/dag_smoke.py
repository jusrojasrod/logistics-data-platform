"""DAG de humo: valida orquestador, base de datos y rutas montadas."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

from airflow.sdk import dag, task


@dag(
    dag_id="dag_smoke",
    description="Verifica que Airflow, Postgres y el código del proyecto estén operativos",
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    schedule=None,
    catchup=False,
    tags=["smoke"],
    default_args={"retries": 1},
)
def dag_smoke() -> None:
    @task
    def check_python() -> str:
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    @task
    def check_project_import() -> str:
        from nexora.common.config import settings

        return settings.environment

    @task
    def check_postgres() -> int:
        import psycopg

        dsn = os.environ["NEXORA_PG_DSN"]
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select count(*) from information_schema.schemata where schema_name = 'raw'"
            )
            return int(cur.fetchone()[0])

    python_version = check_python()
    environment = check_project_import()
    raw_schema = check_postgres()

    python_version >> environment >> raw_schema


dag_smoke()

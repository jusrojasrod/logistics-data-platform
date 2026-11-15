"""Ingesta batch diaria al esquema raw: extracción de tms por watermark y archivos ERP por hash."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from airflow.sdk import dag, task


@dag(
    dag_id="dag_ingest_batch",
    description="Extrae las tablas de tms por watermark y carga los archivos ERP por hash en raw",
    start_date=datetime(2026, 1, 1, tzinfo=ZoneInfo("America/Bogota")),
    schedule="0 2 * * *",
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["ingest", "batch"],
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
    },
)
def dag_ingest_batch() -> None:
    @task
    def extract_tms() -> dict[str, int]:
        """Extrae las tablas de tms hacia raw y devuelve las filas insertadas por tabla."""
        import psycopg

        from nexora.common.config import settings
        from nexora.ingestion.batch.loaders import run_tms

        with psycopg.connect(settings.etl_conninfo()) as conn:
            results = run_tms(conn)
        return {result.table: result.rows for result in results}

    @task
    def load_erp_files() -> dict[str, int]:
        """Carga los archivos ERP de la escala configurada y devuelve las filas por archivo."""
        from pathlib import Path

        import psycopg

        from nexora.common.config import settings
        from nexora.ingestion.batch.loaders import run_files

        directory = Path(settings.data_dir) / settings.scale
        with psycopg.connect(settings.etl_conninfo()) as conn:
            results = run_files(conn, directory)
        return {result.source_file: result.rows for result in results}

    extract_tms()
    load_erp_files()


dag_ingest_batch()

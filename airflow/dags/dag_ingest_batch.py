"""Ingesta batch diaria en raw: extracción de tms por watermark y archivos ERP y CRM por hash.

Los archivos se cargan desde la carpeta de la escala configurada y después desde su
subcarpeta de tardíos; si esa subcarpeta no existe, la tarea falla.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from airflow.sdk import dag, task


@dag(
    dag_id="dag_ingest_batch",
    description=(
        "Extrae las tablas de tms por watermark y carga los archivos ERP y CRM, "
        "de la carpeta y de late/, por hash en raw"
    ),
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
        """Carga los archivos ERP y CRM de la escala configurada y después sus tardíos.

        Devuelve las filas insertadas por archivo; los tardíos tienen nombres propios
        (``<tabla>_late.csv``), así que no se pisan con los de la carpeta principal.
        """
        from pathlib import Path

        import psycopg

        from nexora.common.config import settings
        from nexora.ingestion.batch.loaders import LATE_DIRECTORY, run_files

        directory = Path(settings.data_dir) / settings.scale
        with psycopg.connect(settings.etl_conninfo()) as conn:
            results = run_files(conn, directory)
            late_results = run_files(conn, directory / LATE_DIRECTORY)
        return {result.source_file: result.rows for result in results + late_results}

    extract_tms()
    load_erp_files()


dag_ingest_batch()

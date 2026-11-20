"""Ingesta horaria de las fuentes de API en raw: una tarea por dataset.

Cada tarea delega en el runner de ``nexora.ingestion.api``: recorre la fuente
(TMS por HTTP o los simuladores deterministas), aterriza el payload crudo en
MongoDB y registra la ejecución en ``meta.api_runs``. El ``run_id`` se genera
por tarea y se devuelve por XCom junto con el estado del dataset.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from airflow.sdk import dag, task

# Un dataset por fuente, según el contrato de la vía API.
DATASETS: tuple[str, ...] = (
    "tms_shipments",
    "tms_shipment_events",
    "tms_carrier_incidents",
    "weather_api",
    "geocoding_api",
    "exchange_rate_api",
)


@dag(
    dag_id="dag_ingest_apis",
    description=(
        "Extrae las fuentes de API por dataset y aterriza el payload crudo en raw, "
        "con registro en meta.api_runs y bloqueo por DQ-008"
    ),
    start_date=datetime(2026, 1, 1, tzinfo=ZoneInfo("America/Bogota")),
    schedule="0 * * * *",
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["ingest", "api"],
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
    },
)
def dag_ingest_apis() -> None:
    def make_ingest_task(dataset: str):
        """Crea la tarea de un dataset; solo arma la llamada al runner."""

        @task(task_id=f"ingest_{dataset}")
        def ingest() -> dict[str, object]:
            import uuid

            from nexora.ingestion.api.runner import (
                mongo_collections,
                postgres_connection,
                run_extraction,
            )

            run_id = uuid.uuid4().hex
            with (
                postgres_connection() as connection,
                mongo_collections() as (payloads, drift_rows),
            ):
                result = run_extraction(
                    dataset,
                    run_id=run_id,
                    connection=connection,
                    payloads=payloads,
                    drift_rows=drift_rows,
                )
            return {
                "run_id": result.run_id,
                "dataset": result.dataset,
                "status": result.status,
                "pages": result.pages,
                "documents": result.documents,
                "drift_count": result.drift_count,
            }

        return ingest

    for dataset in DATASETS:
        make_ingest_task(dataset)()


dag_ingest_apis()

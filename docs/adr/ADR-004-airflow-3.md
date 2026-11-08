# ADR-004 — Airflow 3

**Estado:** aceptada · **Fecha:** 2026-11-07

## Contexto

Se necesita un orquestador con dependencias, reintentos, SLA, alertas y separación entre ingesta y transformación. La línea 2.x de Airflow agotó su soporte en abril de 2026; arrancar sobre una versión sin mantenimiento no es defendible.

## Decisión

Usar Apache Airflow 3.4 con Python 3.12, `LocalExecutor` y Astronomer Cosmos para ejecutar dbt. La validación se hace con un DAG de humo en la primera semana.

## Alternativas consideradas

| Alternativa | Motivo de descarte |
|---|---|
| Airflow 2.11 | Fin de soporte; sin parches de seguridad |
| Dagster | Válido, pero el objetivo es demostrar el estándar más extendido |
| Cron + scripts | Sin dependencias, reintentos ni observabilidad |

## Consecuencias

- Componentes separados: `api-server`, `scheduler`, `dag-processor`, `triggerer`.
- Assets en lugar de datasets y *DAG versioning* disponibles.
- La versión es reciente: si aparece un bloqueo con Cosmos, se documenta el retroceso a una versión anterior soportada.
- Consume memoria: se dimensionan límites por componente y se ejecuta un solo *worker* local.

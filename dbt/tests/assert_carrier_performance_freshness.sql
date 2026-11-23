-- DQ-006a (alerta): frescura batch inferior a 26 horas en las dos fuentes TMS.
--
-- Se toma la carga más reciente de cada modelo válido y se compara con la hora
-- actual. Se emite una fila por entidad cuya última ingesta supera el umbral.
-- Con severity warn el aviso no detiene la ejecución.
-- Devuelve cero filas cuando ambas fuentes están dentro del umbral.

{{ config(severity='warn') }}

with latest_ingestion as (

    select
        'carrier_incidents' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__carrier_incidents_valid') }}

    union all

    select
        'carrier_tenders' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__carrier_tenders_valid') }}

)

select
    'DQ-006a' as rule_id,
    latest_ingestion.entity,
    latest_ingestion.last_ingested_at,
    now() - latest_ingestion.last_ingested_at as age
from latest_ingestion
where now() - latest_ingestion.last_ingested_at > interval '26 hours'

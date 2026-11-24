-- DQ-006a (alerta): frescura batch inferior a 26 horas en las ocho fuentes
-- operativas TMS.
--
-- Se toma la carga más reciente de cada modelo válido y se compara con la hora
-- actual. Se emite una fila por entidad cuya última ingesta supera el umbral.
-- Con severidad warn el aviso no detiene la ejecución.
-- Devuelve cero filas cuando las ocho fuentes están dentro del umbral.

{{ config(severity='warn') }}

with latest_ingestion as (

    select
        'vehicles' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__vehicles_valid') }}

    union all

    select
        'drivers' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__drivers_valid') }}

    union all

    select
        'trips' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__trips_valid') }}

    union all

    select
        'trip_legs' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__trip_legs_valid') }}

    union all

    select
        'shipment_legs' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__shipment_legs_valid') }}

    union all

    select
        'shipment_stops' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__shipment_stops_valid') }}

    union all

    select
        'shipment_events' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__shipment_events_valid') }}

    union all

    select
        'fuel_transactions' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_tms__fuel_transactions_valid') }}

)

select
    'DQ-006a' as rule_id,
    latest_ingestion.entity,
    latest_ingestion.last_ingested_at,
    now() - latest_ingestion.last_ingested_at as age
from latest_ingestion
where now() - latest_ingestion.last_ingested_at > interval '26 hours'

-- DQ-006a (alerta): frescura batch inferior a 26 horas en las seis fuentes ERP.
--
-- Se toma la carga más reciente de cada modelo válido y se compara con la hora
-- actual. Se emite una fila por entidad cuya última ingesta supera el umbral.
-- Con severity warn el aviso no detiene la ejecución.
-- Devuelve cero filas cuando las seis fuentes están dentro del umbral.

{{ config(severity='warn') }}

with latest_ingestion as (

    select
        'tariffs' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_erp__tariffs_valid') }}

    union all

    select
        'invoices' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_erp__invoices_valid') }}

    union all

    select
        'invoice_lines' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_erp__invoice_lines_valid') }}

    union all

    select
        'payments' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_erp__payments_valid') }}

    union all

    select
        'carrier_settlements' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_erp__carrier_settlements_valid') }}

    union all

    select
        'shipment_costs' as entity,
        max(ingested_at) as last_ingested_at
    from {{ ref('stg_erp__shipment_costs_valid') }}

)

select
    'DQ-006a' as rule_id,
    latest_ingestion.entity,
    latest_ingestion.last_ingested_at,
    now() - latest_ingestion.last_ingested_at as age
from latest_ingestion
where now() - latest_ingestion.last_ingested_at > interval '26 hours'

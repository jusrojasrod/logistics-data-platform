-- Partición exacta de raw.api_items entre elementos válidos de staging y
-- cuarentena de la vía API.
--
-- Por ejecución y clave de grano (run_id, endpoint, page, item_index):
--   expuestos = válidos + cuarentena, sin solapamiento y sin copias duplicadas.
-- Los tres conjuntos se unen con su recuento para detectar también filas de
-- válidos o cuarentena sin su elemento expuesto. La cuarentena conserva los
-- metadatos dentro de payload. Devuelve una fila por incumplimiento; sin
-- incumplimientos, cero filas.

with items as (

    select
        exposed.run_id,
        exposed.endpoint,
        exposed.page,
        exposed.item_index,
        'exposed' as set_name
    from {{ source('api', 'api_items') }} as exposed

    union all

    select
        shipments.run_id,
        shipments.endpoint,
        shipments.page,
        shipments.item_index,
        'valid' as set_name
    from {{ ref('stg_api__shipments_valid') }} as shipments

    union all

    select
        events.run_id,
        events.endpoint,
        events.page,
        events.item_index,
        'valid' as set_name
    from {{ ref('stg_api__shipment_events_valid') }} as events

    union all

    select
        incidents.run_id,
        incidents.endpoint,
        incidents.page,
        incidents.item_index,
        'valid' as set_name
    from {{ ref('stg_api__carrier_incidents_valid') }} as incidents

    union all

    select
        quarantined.run_id,
        cast(quarantined.payload ->> 'endpoint' as text) as endpoint,
        cast(quarantined.payload ->> 'page' as integer) as page,
        cast(quarantined.payload ->> 'item_index' as integer) as item_index,
        'quarantine' as set_name
    from {{ ref('api_items') }} as quarantined

),

key_counts as (

    select
        items.run_id,
        items.endpoint,
        items.page,
        items.item_index,
        sum(case when items.set_name = 'exposed' then 1 else 0 end) as exposed_count,
        sum(case when items.set_name = 'valid' then 1 else 0 end) as valid_count,
        sum(
            case when items.set_name = 'quarantine' then 1 else 0 end
        ) as quarantine_count
    from items
    group by items.run_id, items.endpoint, items.page, items.item_index

),

failures as (

    select
        key_counts.run_id,
        key_counts.endpoint,
        key_counts.page,
        key_counts.item_index,
        key_counts.exposed_count,
        key_counts.valid_count,
        key_counts.quarantine_count,
        key_counts.valid_count + key_counts.quarantine_count as expected_count,
        key_counts.valid_count > 1 as multiple_valid,
        key_counts.quarantine_count > 1 as multiple_quarantine,
        key_counts.valid_count > 0 and key_counts.quarantine_count > 0 as overlap
    from key_counts

)

select
    failures.run_id,
    failures.endpoint,
    failures.page,
    failures.item_index,
    failures.exposed_count,
    failures.valid_count,
    failures.quarantine_count,
    case
        when failures.exposed_count <> failures.expected_count
            then 'partition_mismatch'
        when failures.multiple_valid then 'multiple_valid_rows'
        when failures.multiple_quarantine then 'multiple_quarantine_rows'
        else 'valid_and_quarantine_overlap'
    end as failure_type
from failures
where
    failures.exposed_count <> failures.expected_count
    or failures.multiple_valid
    or failures.multiple_quarantine
    or failures.overlap

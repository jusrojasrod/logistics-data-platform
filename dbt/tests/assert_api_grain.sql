-- Unicidad del grano declarado (run_id, endpoint, page, item_index) en los
-- modelos nuevos de staging y de core de la vía API.
--
-- Cada modelo se etiqueta para localizar la clave repetida. La comprobación
-- cubre los tres recursos en sus capas tipada, clasificada, válida y publicada.
-- Devuelve una fila por clave duplicada; sin duplicados, cero filas.

with grain_keys as (

    select
        'stg_api__shipments' as model_name,
        shipments.run_id,
        shipments.endpoint,
        shipments.page,
        shipments.item_index
    from {{ ref('stg_api__shipments') }} as shipments

    union all

    select
        'stg_api__shipment_events' as model_name,
        events.run_id,
        events.endpoint,
        events.page,
        events.item_index
    from {{ ref('stg_api__shipment_events') }} as events

    union all

    select
        'stg_api__carrier_incidents' as model_name,
        incidents.run_id,
        incidents.endpoint,
        incidents.page,
        incidents.item_index
    from {{ ref('stg_api__carrier_incidents') }} as incidents

    union all

    select
        'stg_api__shipments_validated' as model_name,
        shipments.run_id,
        shipments.endpoint,
        shipments.page,
        shipments.item_index
    from {{ ref('stg_api__shipments_validated') }} as shipments

    union all

    select
        'stg_api__shipment_events_validated' as model_name,
        events.run_id,
        events.endpoint,
        events.page,
        events.item_index
    from {{ ref('stg_api__shipment_events_validated') }} as events

    union all

    select
        'stg_api__carrier_incidents_validated' as model_name,
        incidents.run_id,
        incidents.endpoint,
        incidents.page,
        incidents.item_index
    from {{ ref('stg_api__carrier_incidents_validated') }} as incidents

    union all

    select
        'stg_api__shipments_valid' as model_name,
        shipments.run_id,
        shipments.endpoint,
        shipments.page,
        shipments.item_index
    from {{ ref('stg_api__shipments_valid') }} as shipments

    union all

    select
        'stg_api__shipment_events_valid' as model_name,
        events.run_id,
        events.endpoint,
        events.page,
        events.item_index
    from {{ ref('stg_api__shipment_events_valid') }} as events

    union all

    select
        'stg_api__carrier_incidents_valid' as model_name,
        incidents.run_id,
        incidents.endpoint,
        incidents.page,
        incidents.item_index
    from {{ ref('stg_api__carrier_incidents_valid') }} as incidents

    union all

    select
        'core_api__shipments' as model_name,
        shipments.run_id,
        shipments.endpoint,
        shipments.page,
        shipments.item_index
    from {{ ref('core_api__shipments') }} as shipments

    union all

    select
        'core_api__shipment_events' as model_name,
        events.run_id,
        events.endpoint,
        events.page,
        events.item_index
    from {{ ref('core_api__shipment_events') }} as events

    union all

    select
        'core_api__carrier_incidents' as model_name,
        incidents.run_id,
        incidents.endpoint,
        incidents.page,
        incidents.item_index
    from {{ ref('core_api__carrier_incidents') }} as incidents

),

duplicates as (

    select
        grain_keys.model_name,
        grain_keys.run_id,
        grain_keys.endpoint,
        grain_keys.page,
        grain_keys.item_index,
        count(*) as row_count
    from grain_keys
    group by
        grain_keys.model_name,
        grain_keys.run_id,
        grain_keys.endpoint,
        grain_keys.page,
        grain_keys.item_index
    having count(*) > 1

)

select
    duplicates.model_name,
    duplicates.run_id,
    duplicates.endpoint,
    duplicates.page,
    duplicates.item_index,
    duplicates.row_count
from duplicates

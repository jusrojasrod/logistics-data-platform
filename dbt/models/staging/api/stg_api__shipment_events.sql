-- Eventos de envío tipados desde la vía API (raw.api_items, dataset
-- tms_shipment_events). Una fila por elemento de data[]; el payload crudo se
-- conserva íntegro. Los duplicados de la clave natural dentro de un run_id se
-- marcan con copy_rank para clasificarlos después. No se borra ninguna fila.

with source as (

    select * from {{ source('api', 'api_items') }}
    where dataset_name = 'tms_shipment_events'

),

typed as (

    select
        cast(payload ->> 'event_id' as text) as event_id,
        cast(payload ->> 'shipment_id' as text) as shipment_id,
        cast(payload ->> 'event_ts' as timestamptz) as event_ts,
        cast(payload ->> 'event_type' as text) as event_type,
        cast(payload ->> 'location_city' as text) as location_city,
        cast(payload ->> 'latitude' as numeric) as latitude,
        cast(payload ->> 'longitude' as numeric) as longitude,
        cast(payload ->> 'severity' as text) as severity,
        cast(run_id as text) as run_id,
        cast(source_system as text) as source_system,
        cast(endpoint as text) as endpoint,
        cast(page as integer) as page,
        cast(payload_sha256 as text) as payload_sha256,
        cast(ingested_at as timestamptz) as ingested_at,
        cast(dataset_name as text) as dataset_name,
        cast(source_key as text) as source_key,
        cast(item_index as integer) as item_index,
        payload,
        cast(has_drift_flag as boolean) as has_drift_flag,
        cast(drift_fields as jsonb) as drift_fields,
        cast(exposed_at as timestamptz) as exposed_at
    from source

),

ranked as (

    select
        typed.*,
        row_number() over (
            partition by typed.run_id, typed.dataset_name, typed.source_key
            order by typed.ingested_at, typed.item_index
        ) as copy_rank
    from typed

)

select * from ranked

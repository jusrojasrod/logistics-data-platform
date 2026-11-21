-- Envíos tipados desde la vía API (raw.api_items, dataset tms_shipments).
-- Una fila por elemento de data[]; el payload crudo se conserva íntegro. Los
-- duplicados de la clave natural dentro de un run_id se marcan con copy_rank
-- para clasificarlos después. No se borra ninguna fila.

with source as (

    select * from {{ source('api', 'api_items') }}
    where dataset_name = 'tms_shipments'

),

typed as (

    select
        cast(payload ->> 'shipment_id' as text) as shipment_id,
        cast(payload ->> 'customer_id' as text) as customer_id,
        cast(payload ->> 'carrier_id' as text) as carrier_id,
        cast(payload ->> 'route_id' as text) as route_id,
        cast(payload ->> 'service_type_id' as text) as service_type_id,
        cast(payload ->> 'pickup_ts' as timestamptz) as pickup_ts,
        cast(payload ->> 'pickup_date' as date) as pickup_date,
        cast(payload ->> 'promised_delivery_ts' as timestamptz) as promised_delivery_ts,
        cast(payload ->> 'picked_up_at' as timestamptz) as picked_up_at,
        cast(payload ->> 'delivered_at' as timestamptz) as delivered_at,
        cast(payload ->> 'ordered_qty' as integer) as ordered_qty,
        cast(payload ->> 'delivered_qty' as integer) as delivered_qty,
        cast(payload ->> 'ordered_weight_kg' as numeric(12, 3)) as ordered_weight_kg,
        cast(payload ->> 'delivered_weight_kg' as numeric(12, 3)) as delivered_weight_kg,
        cast(payload ->> 'definitive_failure_flag' as boolean) as definitive_failure_flag,
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

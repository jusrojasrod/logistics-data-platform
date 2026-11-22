-- Envíos tipados desde la vía API (raw.api_items, dataset tms_shipments).
-- Una fila por elemento de data[]; el payload crudo se conserva íntegro. Los
-- duplicados de la clave natural dentro de un run_id se marcan con copy_rank
-- para clasificarlos después. No se borra ninguna fila. Los valores de drift
-- no parseables en campos tipados (por ejemplo '42' en fechas o 'drift' en el
-- indicador booleano) pasan a NULL: la fila conserva su payload y
-- has_drift_flag para que DQ-008 la clasifique después.

with source as (

    select * from {{ source('api', 'api_items') }}
    where dataset_name = 'tms_shipments'

),

typed as (

    -- Columnas simples primero y cálculos después; los valores no parseables
    -- pasan a NULL sin perder el payload ni la bandera de drift.
    select
        cast(payload ->> 'shipment_id' as text) as shipment_id,
        cast(payload ->> 'customer_id' as text) as customer_id,
        cast(payload ->> 'carrier_id' as text) as carrier_id,
        cast(payload ->> 'route_id' as text) as route_id,
        cast(payload ->> 'service_type_id' as text) as service_type_id,
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
        cast(exposed_at as timestamptz) as exposed_at,
        case
            when pg_input_is_valid(payload ->> 'pickup_ts', 'timestamptz')
                then cast(payload ->> 'pickup_ts' as timestamptz)
        end as pickup_ts,
        case
            when pg_input_is_valid(payload ->> 'pickup_date', 'date')
                then cast(payload ->> 'pickup_date' as date)
        end as pickup_date,
        case
            when pg_input_is_valid(payload ->> 'promised_delivery_ts', 'timestamptz')
                then cast(payload ->> 'promised_delivery_ts' as timestamptz)
        end as promised_delivery_ts,
        case
            when pg_input_is_valid(payload ->> 'picked_up_at', 'timestamptz')
                then cast(payload ->> 'picked_up_at' as timestamptz)
        end as picked_up_at,
        case
            when pg_input_is_valid(payload ->> 'delivered_at', 'timestamptz')
                then cast(payload ->> 'delivered_at' as timestamptz)
        end as delivered_at,
        case
            when pg_input_is_valid(payload ->> 'ordered_qty', 'integer')
                then cast(payload ->> 'ordered_qty' as integer)
        end as ordered_qty,
        case
            when pg_input_is_valid(payload ->> 'delivered_qty', 'integer')
                then cast(payload ->> 'delivered_qty' as integer)
        end as delivered_qty,
        case
            when pg_input_is_valid(payload ->> 'ordered_weight_kg', 'numeric')
                then cast(payload ->> 'ordered_weight_kg' as numeric(12, 3))
        end as ordered_weight_kg,
        case
            when pg_input_is_valid(payload ->> 'delivered_weight_kg', 'numeric')
                then cast(payload ->> 'delivered_weight_kg' as numeric(12, 3))
        end as delivered_weight_kg,
        case
            when pg_input_is_valid(payload ->> 'definitive_failure_flag', 'boolean')
                then cast(payload ->> 'definitive_failure_flag' as boolean)
        end as definitive_failure_flag
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

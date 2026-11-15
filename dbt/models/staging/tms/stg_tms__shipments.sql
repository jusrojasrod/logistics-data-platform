-- Envíos tipados desde raw.shipments. No se borra ninguna fila: los duplicados
-- se marcan con copy_rank > 1 y se clasifican en el modelo validado.

with source as (

    select * from {{ source('tms', 'shipments') }}

),

typed as (

    select
        cast(shipment_id as text) as shipment_id,
        cast(customer_id as text) as customer_id,
        cast(carrier_id as text) as carrier_id,
        cast(route_id as text) as route_id,
        cast(service_type_id as text) as service_type_id,
        cast(pickup_ts as timestamptz) as pickup_ts,
        cast(pickup_date as date) as pickup_date,
        cast(promised_delivery_ts as timestamptz) as promised_delivery_ts,
        cast(picked_up_at as timestamptz) as picked_up_at,
        cast(delivered_at as timestamptz) as delivered_at,
        cast(ordered_qty as integer) as ordered_qty,
        cast(delivered_qty as integer) as delivered_qty,
        cast(ordered_weight_kg as numeric(12, 3)) as ordered_weight_kg,
        cast(delivered_weight_kg as numeric(12, 3)) as delivered_weight_kg,
        cast(definitive_failure_flag as boolean) as definitive_failure_flag,
        cast(run_id as text) as run_id,
        cast(source_system as text) as source_system,
        cast(source_file as text) as source_file,
        cast(source_file_hash as text) as source_file_hash,
        cast(ingested_at as timestamptz) as ingested_at
    from source

),

ranked as (

    select
        typed.*,
        row_number() over (
            partition by typed.run_id, typed.shipment_id, typed.source_file_hash
            order by typed.ingested_at
        ) as copy_rank
    from typed

)

select * from ranked

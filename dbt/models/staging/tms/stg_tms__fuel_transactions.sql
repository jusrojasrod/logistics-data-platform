-- Compras de combustible tipadas desde raw.fuel_transactions. Se lee todo raw
-- sin filtrar por run_id ni por fecha: las publicaciones posteriores a la fecha
-- de negocio llegan en la siguiente carga. No se borra ninguna fila; las copias
-- repetidas por clave natural se marcan con copy_rank para clasificarlas en el
-- modelo validado.

with source as (

    select * from {{ source('tms_operations', 'fuel_transactions') }}

),

typed as (

    select
        cast(fuel_txn_id as text) as fuel_txn_id,
        cast(trip_id as text) as trip_id,
        cast(vehicle_id as text) as vehicle_id,
        cast(driver_id as text) as driver_id,
        cast(txn_ts as timestamptz) as txn_ts,
        cast(station_city as text) as station_city,
        cast(gallons as numeric) as gallons,
        cast(price_per_gallon_amount as numeric) as price_per_gallon_amount,
        cast(total_amount as numeric) as total_amount,
        cast(currency_code as text) as currency_code,
        cast(odometer_km as numeric) as odometer_km,
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
            partition by typed.fuel_txn_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

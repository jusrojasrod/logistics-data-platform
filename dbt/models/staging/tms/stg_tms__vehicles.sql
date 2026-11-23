-- Vehículos tipados desde raw.vehicles. Se lee todo raw sin filtrar por run_id
-- ni por fecha: las publicaciones posteriores a la fecha de negocio llegan en
-- la siguiente carga. No se borra ninguna fila; las copias repetidas por clave
-- natural se marcan con copy_rank para clasificarlas en el modelo validado.

with source as (

    select * from {{ source('tms_operations', 'vehicles') }}

),

typed as (

    select
        cast(vehicle_id as text) as vehicle_id,
        cast(carrier_id as text) as carrier_id,
        cast(plate as text) as plate,
        cast(vehicle_type as text) as vehicle_type,
        cast(capacity_kg as numeric) as capacity_kg,
        cast(capacity_m3 as numeric) as capacity_m3,
        cast(fuel_type as text) as fuel_type,
        cast(km_per_gallon as numeric) as km_per_gallon,
        cast(purchase_date as date) as purchase_date,
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
            partition by typed.vehicle_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

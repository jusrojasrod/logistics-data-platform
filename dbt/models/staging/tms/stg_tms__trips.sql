-- Viajes tipados desde raw.trips. Se lee todo raw sin filtrar por run_id ni por
-- fecha: las publicaciones posteriores a la fecha de negocio llegan en la
-- siguiente carga. No se borra ninguna fila; las copias repetidas por clave
-- natural se marcan con copy_rank para clasificarlas en el modelo validado.

with source as (

    select * from {{ source('tms_operations', 'trips') }}

),

typed as (

    select
        cast(trip_id as text) as trip_id,
        cast(carrier_id as text) as carrier_id,
        cast(vehicle_id as text) as vehicle_id,
        cast(driver_id as text) as driver_id,
        cast(trip_type as text) as trip_type,
        cast(origin_city as text) as origin_city,
        cast(start_ts as timestamptz) as start_ts,
        cast(end_ts as timestamptz) as end_ts,
        cast(distance_km as numeric) as distance_km,
        cast(empty_km as numeric) as empty_km,
        cast(stopped_minutes as integer) as stopped_minutes,
        cast(fuel_gallons as numeric) as fuel_gallons,
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
            partition by typed.trip_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

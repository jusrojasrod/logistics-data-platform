-- Tramos de viaje tipados desde raw.trip_legs. Se lee todo raw sin filtrar por
-- run_id ni por fecha: las publicaciones posteriores a la fecha de negocio
-- llegan en la siguiente carga. No se borra ninguna fila; las copias repetidas
-- por clave natural se marcan con copy_rank para clasificarlas en el modelo
-- validado.

with source as (

    select * from {{ source('tms_operations', 'trip_legs') }}

),

typed as (

    select
        cast(trip_id as text) as trip_id,
        cast(leg_sequence as integer) as leg_sequence,
        cast(origin_city as text) as origin_city,
        cast(destination_city as text) as destination_city,
        cast(distance_km as numeric) as distance_km,
        cast(departure_ts as timestamptz) as departure_ts,
        cast(arrival_ts as timestamptz) as arrival_ts,
        cast(loaded_flag as boolean) as loaded_flag,
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
            partition by typed.trip_id, typed.leg_sequence
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

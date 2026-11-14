-- Rutas tipadas desde raw.routes. Una fila por route_id: se conserva la carga
-- más reciente según ingested_at.

with source as (

    select * from {{ source('tms', 'routes') }}

),

typed as (

    select
        cast(route_id as text) as route_id,
        cast(origin_city as text) as origin_city,
        cast(destination_city as text) as destination_city,
        cast(distance_km as numeric) as distance_km,
        cast(estimated_hours as numeric) as estimated_hours,
        cast(road_type as text) as road_type,
        cast(run_id as text) as run_id,
        cast(source_system as text) as source_system,
        cast(source_file_hash as text) as source_file_hash,
        cast(ingested_at as timestamptz) as ingested_at
    from source

),

ranked as (

    select
        typed.*,
        row_number() over (
            partition by typed.route_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as load_rank
    from typed

),

latest as (

    select
        route_id,
        origin_city,
        destination_city,
        distance_km,
        estimated_hours,
        road_type,
        run_id,
        source_system,
        source_file_hash,
        ingested_at
    from ranked
    where load_rank = 1

)

select * from latest

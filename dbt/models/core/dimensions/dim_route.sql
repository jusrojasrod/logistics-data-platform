-- Dimensión de rutas de una sola versión: una fila por route_id con clave
-- sustituta estable md5(route_id). Sin historia (sin SCD2).

with source as (

    select * from {{ ref('stg_tms__routes') }}

),

keyed as (

    select
        source.route_id,
        source.origin_city,
        source.destination_city,
        source.distance_km,
        source.estimated_hours,
        source.road_type,
        md5(source.route_id) as route_key
    from source

)

select
    route_key,
    route_id,
    origin_city,
    destination_city,
    distance_km,
    estimated_hours,
    road_type
from keyed

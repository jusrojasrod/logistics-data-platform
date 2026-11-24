-- Grano, medidas y reparto del puente envío–viaje.
--
-- La clave compuesta (shipment_id, trip_id, leg_sequence) debe ser única y no
-- nula; loaded_km se reconcilia con stg_tms__shipment_legs_valid para las mismas
-- claves; allocation_method y empty_km_allocated no pueden ser nulos. Los viajes
-- repositioning no tienen tramos de envío: el puente no genera filas para ellos
-- y su vacío queda como deadhead no asignado en fact_trips (§5.4.6). Se comprueba
-- que existen esos viajes antes de exigir cero kilómetros vacíos asignados, y se
-- lee desde fact_trips con left join para no perder los viajes sin filas en el
-- puente. El puente y esos viajes deben estar poblados para que el test no pase
-- en vacío.
-- Devuelve una fila por incumplimiento; sin incumplimientos, cero filas.

with bridge_grain as (

    select
        legs.shipment_id,
        legs.trip_id,
        legs.leg_sequence,
        legs.loaded_km,
        legs.allocation_method,
        legs.empty_km_allocated,
        concat_ws(
            '|',
            legs.shipment_id,
            legs.trip_id,
            legs.leg_sequence
        ) as grain_key
    from {{ ref('bridge_shipment_trip') }} as legs

),

duplicates as (

    select
        bridge_grain.grain_key,
        count(*) as row_count
    from bridge_grain
    group by bridge_grain.grain_key
    having count(*) > 1

),

null_grains as (

    select
        bridge_grain.grain_key,
        count(*) as row_count
    from bridge_grain
    where
        bridge_grain.shipment_id is null
        or bridge_grain.trip_id is null
        or bridge_grain.leg_sequence is null
    group by bridge_grain.grain_key

),

null_methods as (

    select
        bridge_grain.grain_key,
        count(*) as row_count
    from bridge_grain
    where bridge_grain.allocation_method is null
    group by bridge_grain.grain_key

),

null_allocations as (

    select
        bridge_grain.grain_key,
        count(*) as row_count
    from bridge_grain
    where bridge_grain.empty_km_allocated is null
    group by bridge_grain.grain_key

),

bridge_loaded as (

    select coalesce(sum(bridge_grain.loaded_km), 0) as loaded_km from bridge_grain

),

bridge_keys as (

    select distinct
        bridge_grain.shipment_id,
        bridge_grain.trip_id,
        bridge_grain.leg_sequence
    from bridge_grain

),

staging_loaded as (

    select coalesce(sum(staging.loaded_km), 0) as loaded_km
    from {{ ref('stg_tms__shipment_legs_valid') }} as staging
    inner join bridge_keys
        on
            staging.shipment_id = bridge_keys.shipment_id
            and staging.trip_id = bridge_keys.trip_id
            and staging.leg_sequence = bridge_keys.leg_sequence

),

repositioning_trips as (

    select trips.trip_id
    from {{ ref('fact_trips') }} as trips
    where trips.trip_type = 'repositioning'

),

repositioning_counts as (

    select count(*) as row_count from repositioning_trips

),

bridge_allocated as (

    select
        bridge_grain.trip_id,
        sum(bridge_grain.empty_km_allocated) as allocated_km
    from bridge_grain
    group by bridge_grain.trip_id

),

repositioning_allocated as (

    select
        trips.trip_id,
        count(*) as row_count
    from repositioning_trips as trips
    left join bridge_allocated
        on trips.trip_id = bridge_allocated.trip_id
    where coalesce(bridge_allocated.allocated_km, 0) <> 0
    group by trips.trip_id

),

bridge_counts as (

    select count(*) as row_count from bridge_grain

)

select
    'duplicate_grain' as failure_type,
    'bridge_shipment_trip' as model_name,
    duplicates.grain_key as detail,
    duplicates.row_count
from duplicates

union all

select
    'null_grain' as failure_type,
    'bridge_shipment_trip' as model_name,
    null_grains.grain_key as detail,
    null_grains.row_count
from null_grains

union all

select
    'null_allocation_method' as failure_type,
    'bridge_shipment_trip' as model_name,
    null_methods.grain_key as detail,
    null_methods.row_count
from null_methods

union all

select
    'null_empty_km_allocated' as failure_type,
    'bridge_shipment_trip' as model_name,
    null_allocations.grain_key as detail,
    null_allocations.row_count
from null_allocations

union all

select
    'loaded_km_mismatch' as failure_type,
    'stg_tms__shipment_legs_valid' as model_name,
    bridge_loaded.loaded_km::text || '|' || staging_loaded.loaded_km::text as detail,
    1::bigint as row_count
from bridge_loaded
cross join staging_loaded
where bridge_loaded.loaded_km is distinct from staging_loaded.loaded_km

union all

select
    'repositioning_allocated' as failure_type,
    'bridge_shipment_trip' as model_name,
    repositioning_allocated.trip_id as detail,
    repositioning_allocated.row_count
from repositioning_allocated

union all

select
    'repositioning_count_zero' as failure_type,
    'fact_trips' as model_name,
    'trip_type = repositioning' as detail,
    0::bigint as row_count
from repositioning_counts
where repositioning_counts.row_count = 0

union all

select
    'empty_bridge' as failure_type,
    'bridge_shipment_trip' as model_name,
    null::text as detail,
    0::bigint as row_count
from bridge_counts
where bridge_counts.row_count = 0

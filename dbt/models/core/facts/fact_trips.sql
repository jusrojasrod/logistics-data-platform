-- Hecho de viajes: una fila por viaje válido (trip_id), con fecha de negocio
-- start_ts y sin partición ni versión. trip_type (delivery/repositioning) se
-- expone para el reparto de §5.4: el viaje sin envíos queda como deadhead no
-- asignado en este hecho. Las claves de vehículo y conductor se resuelven por
-- fecha de negocio contra la vigencia de las dimensiones, con fallback a la
-- versión más antigua cuando la fecha precede a todas; la bandera de versión
-- vigente solo describe la dimensión y nunca atribuye historia. carrier_key
-- se deriva con md5(carrier_id). Sin KPIs, montos ni conversión de moneda.

with trips as (

    select * from {{ ref('stg_tms__trips_valid') }}

),

vehicles as (

    select
        vehicle_id,
        vehicle_key,
        valid_from,
        valid_to
    from {{ ref('dim_vehicle') }}

),

drivers as (

    select
        driver_id,
        driver_key,
        valid_from,
        valid_to
    from {{ ref('dim_driver') }}

),

vehicle_versions as (

    select
        vehicles.*,
        row_number() over (
            partition by vehicles.vehicle_id
            order by vehicles.valid_from
        ) as version_rank
    from vehicles

),

driver_versions as (

    select
        drivers.*,
        row_number() over (
            partition by drivers.driver_id
            order by drivers.valid_from
        ) as version_rank
    from drivers

),

joined as (

    select
        trips.trip_id,
        trips.carrier_id,
        trips.vehicle_id,
        trips.driver_id,
        trips.trip_type,
        trips.origin_city,
        trips.start_ts,
        trips.end_ts,
        trips.distance_km,
        trips.empty_km,
        trips.stopped_minutes,
        trips.fuel_gallons,
        vehicle_match.vehicle_key as matched_vehicle_key,
        vehicle_first.vehicle_key as first_vehicle_key,
        driver_match.driver_key as matched_driver_key,
        driver_first.driver_key as first_driver_key,
        trips.run_id,
        trips.source_system,
        trips.source_file,
        trips.source_file_hash,
        trips.ingested_at
    from trips
    left join vehicle_versions as vehicle_match
        on
            trips.vehicle_id = vehicle_match.vehicle_id
            and trips.start_ts >= vehicle_match.valid_from
            and trips.start_ts < vehicle_match.valid_to
    left join vehicle_versions as vehicle_first
        on
            trips.vehicle_id = vehicle_first.vehicle_id
            and vehicle_first.version_rank = 1
    left join driver_versions as driver_match
        on
            trips.driver_id = driver_match.driver_id
            and trips.start_ts >= driver_match.valid_from
            and trips.start_ts < driver_match.valid_to
    left join driver_versions as driver_first
        on
            trips.driver_id = driver_first.driver_id
            and driver_first.version_rank = 1

),

resolved as (

    select
        joined.*,
        md5(joined.carrier_id) as carrier_key,
        coalesce(joined.matched_vehicle_key, joined.first_vehicle_key) as vehicle_key,
        coalesce(joined.matched_driver_key, joined.first_driver_key) as driver_key
    from joined

)

select
    trip_id,
    carrier_id,
    vehicle_id,
    driver_id,
    trip_type,
    origin_city,
    start_ts,
    end_ts,
    distance_km,
    empty_km,
    stopped_minutes,
    fuel_gallons,
    run_id,
    source_system,
    source_file,
    source_file_hash,
    ingested_at,
    carrier_key,
    vehicle_key,
    driver_key
from resolved

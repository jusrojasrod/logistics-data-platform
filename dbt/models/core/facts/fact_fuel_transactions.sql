-- Hecho de compras de combustible: una fila por compra válida (fuel_txn_id),
-- con fecha de negocio txn_ts y sin partición ni versión. Las claves de vehículo
-- y conductor se resuelven por fecha de negocio contra la vigencia de las
-- dimensiones, con fallback a la versión más antigua cuando la fecha precede a
-- todas; la bandera de versión vigente solo describe la dimensión y nunca
-- atribuye historia. DQ-010 ya dejó en staging solo las compras en COP. Sin
-- KPIs, TRM ni conversión de moneda.

with fuel as (

    select * from {{ ref('stg_tms__fuel_transactions_valid') }}

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
        fuel.fuel_txn_id,
        fuel.trip_id,
        fuel.vehicle_id,
        fuel.driver_id,
        fuel.txn_ts,
        fuel.station_city,
        fuel.gallons,
        fuel.price_per_gallon_amount,
        fuel.total_amount,
        fuel.currency_code,
        fuel.odometer_km,
        vehicle_match.vehicle_key as matched_vehicle_key,
        vehicle_first.vehicle_key as first_vehicle_key,
        driver_match.driver_key as matched_driver_key,
        driver_first.driver_key as first_driver_key,
        fuel.run_id,
        fuel.source_system,
        fuel.source_file,
        fuel.source_file_hash,
        fuel.ingested_at
    from fuel
    left join vehicle_versions as vehicle_match
        on
            fuel.vehicle_id = vehicle_match.vehicle_id
            and fuel.txn_ts >= vehicle_match.valid_from
            and fuel.txn_ts < vehicle_match.valid_to
    left join vehicle_versions as vehicle_first
        on
            fuel.vehicle_id = vehicle_first.vehicle_id
            and vehicle_first.version_rank = 1
    left join driver_versions as driver_match
        on
            fuel.driver_id = driver_match.driver_id
            and fuel.txn_ts >= driver_match.valid_from
            and fuel.txn_ts < driver_match.valid_to
    left join driver_versions as driver_first
        on
            fuel.driver_id = driver_first.driver_id
            and driver_first.version_rank = 1

),

resolved as (

    select
        joined.*,
        coalesce(joined.matched_vehicle_key, joined.first_vehicle_key) as vehicle_key,
        coalesce(joined.matched_driver_key, joined.first_driver_key) as driver_key
    from joined

)

select
    fuel_txn_id,
    trip_id,
    vehicle_id,
    driver_id,
    txn_ts,
    station_city,
    gallons,
    price_per_gallon_amount,
    total_amount,
    currency_code,
    odometer_km,
    run_id,
    source_system,
    source_file,
    source_file_hash,
    ingested_at,
    vehicle_key,
    driver_key
from resolved

-- Dimensión de vehículos con historia (SCD2): una fila por versión de vehículo.
-- El catálogo no trae vigencia, así que valid_from se deriva de ingested_at;
-- el miembro inferido de un vehículo visto antes en un hecho usa su event_ts.
-- vehicle_key = md5(vehicle_id || '|' || valid_from) identifica la versión;
-- valid_to es exclusivo con centinela 9999-12-31 y version vale 1. is_current
-- describe la versión, nunca atribuye historia: los hechos resuelven la
-- vigencia por fecha de negocio con fallback a la versión más antigua.
--
-- Solo se lee staging: el catálogo y las claves observadas en viajes y
-- combustible, sin leer los hechos publicados, para no cerrar un ciclo.

with catalog as (

    select
        vehicles.vehicle_id,
        vehicles.carrier_id,
        vehicles.plate,
        vehicles.vehicle_type,
        vehicles.capacity_kg,
        vehicles.capacity_m3,
        vehicles.fuel_type,
        vehicles.km_per_gallon,
        vehicles.purchase_date,
        vehicles.ingested_at,
        vehicles.run_id,
        vehicles.source_system,
        vehicles.source_file,
        vehicles.source_file_hash
    from {{ ref('stg_tms__vehicles_valid') }} as vehicles

),

observed as (

    select
        trips.vehicle_id,
        trips.start_ts as event_ts
    from {{ ref('stg_tms__trips_valid') }} as trips
    where trips.vehicle_id is not null

    union all

    select
        fuel.vehicle_id,
        fuel.txn_ts as event_ts
    from {{ ref('stg_tms__fuel_transactions_valid') }} as fuel
    where fuel.vehicle_id is not null

),

first_seen as (

    select
        observed.vehicle_id,
        min(observed.event_ts) as event_ts
    from observed
    group by observed.vehicle_id

),

-- Catálogo: una fila por vehículo. Si el id ya tenía fila inferida, se
-- completa in-place con los atributos reales y is_inferred = false, sin
-- crear una segunda versión ni cambiar el grano ni la regla de clave.
known as (

    select
        catalog.vehicle_id,
        catalog.carrier_id,
        catalog.plate,
        catalog.vehicle_type,
        catalog.capacity_kg,
        catalog.capacity_m3,
        catalog.fuel_type,
        catalog.km_per_gallon,
        catalog.purchase_date,
        catalog.ingested_at as valid_from,
        false as is_inferred,
        catalog.run_id,
        catalog.source_system,
        catalog.source_file,
        catalog.source_file_hash,
        catalog.ingested_at
    from catalog

),

-- Miembro inferido: id observado en un hecho sin fila en el catálogo. Lleva
-- atributos de texto 'UNKNOWN', medidas nulas y valid_from = event_ts de la
-- primera observación (§5.2). DQ-015 vigila su antigüedad como incidente.
inferred as (

    select
        first_seen.vehicle_id,
        'UNKNOWN'::text as carrier_id,
        'UNKNOWN'::text as plate,
        'UNKNOWN'::text as vehicle_type,
        null::numeric as capacity_kg,
        null::numeric as capacity_m3,
        'UNKNOWN'::text as fuel_type,
        null::numeric as km_per_gallon,
        null::date as purchase_date,
        first_seen.event_ts as valid_from,
        true as is_inferred,
        null::text as run_id,
        null::text as source_system,
        null::text as source_file,
        null::text as source_file_hash,
        null::timestamp with time zone as ingested_at
    from first_seen
    left join catalog on first_seen.vehicle_id = catalog.vehicle_id
    where catalog.vehicle_id is null

),

entities as (

    select * from known

    union all

    select * from inferred

)

select
    entities.vehicle_id,
    entities.carrier_id,
    entities.plate,
    entities.vehicle_type,
    entities.capacity_kg,
    entities.capacity_m3,
    entities.fuel_type,
    entities.km_per_gallon,
    entities.purchase_date,
    entities.valid_from,
    true as is_current,
    1 as "version",
    entities.is_inferred,
    entities.run_id,
    entities.source_system,
    entities.source_file,
    entities.source_file_hash,
    entities.ingested_at,
    timestamp '9999-12-31 00:00:00' as valid_to,
    md5(entities.vehicle_id || '|' || entities.valid_from::text) as vehicle_key
from entities

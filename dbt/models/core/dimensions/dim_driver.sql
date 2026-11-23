-- Dimensión de conductores con historia (SCD2): una fila por versión de
-- conductor. El catálogo no trae vigencia, así que valid_from se deriva de
-- ingested_at; el miembro inferido de un conductor visto antes en un hecho usa
-- su event_ts. driver_key = md5(driver_id || '|' || valid_from) identifica la
-- versión; valid_to es exclusivo con centinela 9999-12-31 y version vale 1.
-- is_current describe la versión, nunca atribuye historia: los hechos resuelven
-- la vigencia por fecha de negocio con fallback a la versión más antigua. La
-- PII del catálogo no se enmascara en core.
--
-- Solo se lee staging: el catálogo y las claves observadas en viajes y
-- combustible, sin leer los hechos publicados, para no cerrar un ciclo.

with catalog as (

    select
        drivers.driver_id,
        drivers.carrier_id,
        drivers.driver_name,
        drivers.driver_document,
        drivers.driver_phone,
        drivers.driver_email,
        drivers.license_type,
        drivers.hire_date,
        drivers.base_city,
        drivers.ingested_at,
        drivers.run_id,
        drivers.source_system,
        drivers.source_file,
        drivers.source_file_hash
    from {{ ref('stg_tms__drivers_valid') }} as drivers

),

observed as (

    select
        trips.driver_id,
        trips.start_ts as event_ts
    from {{ ref('stg_tms__trips_valid') }} as trips
    where trips.driver_id is not null

    union all

    select
        fuel.driver_id,
        fuel.txn_ts as event_ts
    from {{ ref('stg_tms__fuel_transactions_valid') }} as fuel
    where fuel.driver_id is not null

),

first_seen as (

    select
        observed.driver_id,
        min(observed.event_ts) as event_ts
    from observed
    group by observed.driver_id

),

-- Catálogo: una fila por conductor. Si el id ya tenía fila inferida, se
-- completa in-place con los atributos reales y is_inferred = false, sin
-- crear una segunda versión ni cambiar el grano ni la regla de clave.
known as (

    select
        catalog.driver_id,
        catalog.carrier_id,
        catalog.driver_name,
        catalog.driver_document,
        catalog.driver_phone,
        catalog.driver_email,
        catalog.license_type,
        catalog.hire_date,
        catalog.base_city,
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
-- atributos de texto 'UNKNOWN', fecha de contratación nula y valid_from =
-- event_ts de la primera observación (§5.2). DQ-015 vigila su antigüedad como
-- incidente.
inferred as (

    select
        first_seen.driver_id,
        'UNKNOWN'::text as carrier_id,
        'UNKNOWN'::text as driver_name,
        'UNKNOWN'::text as driver_document,
        'UNKNOWN'::text as driver_phone,
        'UNKNOWN'::text as driver_email,
        'UNKNOWN'::text as license_type,
        null::date as hire_date,
        'UNKNOWN'::text as base_city,
        first_seen.event_ts as valid_from,
        true as is_inferred,
        null::text as run_id,
        null::text as source_system,
        null::text as source_file,
        null::text as source_file_hash,
        null::timestamp with time zone as ingested_at
    from first_seen
    left join catalog on first_seen.driver_id = catalog.driver_id
    where catalog.driver_id is null

),

entities as (

    select * from known

    union all

    select * from inferred

)

select
    entities.driver_id,
    entities.carrier_id,
    entities.driver_name,
    entities.driver_document,
    entities.driver_phone,
    entities.driver_email,
    entities.license_type,
    entities.hire_date,
    entities.base_city,
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
    md5(entities.driver_id || '|' || entities.valid_from::text) as driver_key
from entities

-- Cuarentena operativa: una fila por fila rechazada de las ocho entidades TMS
-- aún no modeladas, nunca una por regla incumplida. Cubre vehículos,
-- conductores, viajes, tramos de viaje, tramos y paradas de envío, eventos de
-- envío y compras de combustible; no mezcla el linaje de shipments, que tiene
-- su propia cuarentena.
--
-- rule_id es la regla principal según la prioridad declarada en cada modelo
-- validado (DQ-010, moneda fuera del conjunto permitido). reason nombra la
-- regla principal y enumera todas las incumplidas; payload conserva la clave
-- natural, los metadatos y dq_errors. La severidad es high para esa regla.
-- Junto con los ocho modelos stg_tms__*_valid forma la partición de cada
-- entidad sobre raw.

with rejected as (

    select
        'raw.vehicles' as source_table,
        'vehicle_id' as natural_key_column,
        vehicle_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__vehicles_validated') }}
    where not is_valid

    union all

    select
        'raw.drivers' as source_table,
        'driver_id' as natural_key_column,
        driver_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__drivers_validated') }}
    where not is_valid

    union all

    select
        'raw.trips' as source_table,
        'trip_id' as natural_key_column,
        trip_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__trips_validated') }}
    where not is_valid

    union all

    select
        'raw.trip_legs' as source_table,
        'trip_id|leg_sequence' as natural_key_column,
        concat_ws('|', trip_id, leg_sequence) as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__trip_legs_validated') }}
    where not is_valid

    union all

    select
        'raw.shipment_legs' as source_table,
        'shipment_id|trip_id|leg_sequence' as natural_key_column,
        concat_ws('|', shipment_id, trip_id, leg_sequence) as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__shipment_legs_validated') }}
    where not is_valid

    union all

    select
        'raw.shipment_stops' as source_table,
        'shipment_id|stop_sequence' as natural_key_column,
        concat_ws('|', shipment_id, stop_sequence) as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__shipment_stops_validated') }}
    where not is_valid

    union all

    select
        'raw.shipment_events' as source_table,
        'event_id' as natural_key_column,
        event_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__shipment_events_validated') }}
    where not is_valid

    union all

    select
        'raw.fuel_transactions' as source_table,
        'fuel_txn_id' as natural_key_column,
        fuel_txn_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__fuel_transactions_validated') }}
    where not is_valid

),

keyed as (

    -- Columnas simples primero y cálculos después; el select final fija el
    -- orden de las ocho columnas canónicas de cuarentena.

    select
        rejected.run_id,
        rejected.dq_primary_rule as rule_id,
        rejected.source_table,
        'high' as severity,
        md5(
            concat_ws(
                '|',
                rejected.source_table,
                rejected.run_id,
                rejected.natural_key,
                rejected.source_file_hash,
                rejected.copy_rank
            )
        ) as quarantine_id,
        'Regla principal: '
        || rejected.dq_primary_rule
        || '. Reglas incumplidas: '
        || array_to_string(rejected.dq_errors, ', ')
        || '.' as reason,
        jsonb_build_object(
            'source_table', rejected.source_table,
            'natural_key_column', rejected.natural_key_column,
            'natural_key', rejected.natural_key,
            'run_id', rejected.run_id,
            'source_system', rejected.source_system,
            'source_file', rejected.source_file,
            'source_file_hash', rejected.source_file_hash,
            'ingested_at', rejected.ingested_at,
            'copy_rank', rejected.copy_rank,
            'dq_errors', to_jsonb(rejected.dq_errors),
            'dq_primary_rule', rejected.dq_primary_rule
        ) as payload,
        current_timestamp as detected_at
    from rejected

)

select
    quarantine_id,
    source_table,
    rule_id,
    reason,
    payload,
    detected_at,
    run_id,
    severity
from keyed

-- Cuarentena de incidencias y ofertas de transportistas: una fila por fila
-- rechazada de las dos entidades, nunca una por regla incumplida.
--
-- rule_id es la regla principal según la prioridad declarada en cada modelo
-- validado (DQ-010, moneda fuera del conjunto permitido). reason nombra la
-- regla principal y enumera todas las incumplidas; payload conserva la clave
-- natural, los metadatos y dq_errors. La severidad es high para esa regla.
-- Junto con los modelos _valid de incidencias y ofertas forma la partición de
-- cada entidad sobre raw.

with rejected as (

    select
        'raw.carrier_incidents' as source_table,
        'incident_id' as natural_key_column,
        incident_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__carrier_incidents_validated') }}
    where not is_valid

    union all

    select
        'raw.carrier_tenders' as source_table,
        'tender_id' as natural_key_column,
        tender_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_tms__carrier_tenders_validated') }}
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

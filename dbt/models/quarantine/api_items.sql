-- Cuarentena de la vía API: una fila por elemento rechazado de raw.api_items,
-- nunca una por regla incumplida. Reúne los tres recursos de la vía API.
--
-- rule_id es la regla principal según la prioridad declarada (DQ-001, DQ-002,
-- DQ-008). reason nombra la regla principal y enumera todas las incumplidas;
-- payload conserva el elemento original, sus metadatos y dq_errors. La
-- severidad es blocking si el elemento tiene drift y high en caso contrario.
-- La cuarentena de la vía por lotes no se modifica.

with rejected as (

    select
        run_id,
        dataset_name,
        source_key,
        item_index,
        source_system,
        endpoint,
        page,
        payload_sha256,
        ingested_at,
        payload,
        has_drift_flag,
        drift_fields,
        exposed_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_api__shipments_validated') }}
    where not is_valid

    union all

    select
        run_id,
        dataset_name,
        source_key,
        item_index,
        source_system,
        endpoint,
        page,
        payload_sha256,
        ingested_at,
        payload,
        has_drift_flag,
        drift_fields,
        exposed_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_api__shipment_events_validated') }}
    where not is_valid

    union all

    select
        run_id,
        dataset_name,
        source_key,
        item_index,
        source_system,
        endpoint,
        page,
        payload_sha256,
        ingested_at,
        payload,
        has_drift_flag,
        drift_fields,
        exposed_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_api__carrier_incidents_validated') }}
    where not is_valid

),

keyed as (

    -- Columnas simples primero y cálculos después; el select final fija el
    -- orden de las ocho columnas canónicas de cuarentena.

    select
        rejected.run_id,
        rejected.dq_primary_rule as rule_id,
        'raw.api_items' as source_table,
        case
            when array_position(rejected.dq_errors, 'DQ-008') is not null
                then 'blocking'
            else 'high'
        end as severity,
        md5(
            concat_ws(
                '|',
                rejected.run_id,
                rejected.dataset_name,
                rejected.source_key,
                rejected.item_index,
                rejected.page,
                rejected.copy_rank
            )
        ) as quarantine_id,
        'Regla principal: '
        || rejected.dq_primary_rule
        || '. Reglas incumplidas: '
        || array_to_string(rejected.dq_errors, ', ')
        || '.' as reason,
        jsonb_build_object(
            'run_id', rejected.run_id,
            'source_system', rejected.source_system,
            'endpoint', rejected.endpoint,
            'page', rejected.page,
            'payload_sha256', rejected.payload_sha256,
            'ingested_at', rejected.ingested_at,
            'dataset_name', rejected.dataset_name,
            'source_key', rejected.source_key,
            'item_index', rejected.item_index,
            'element', rejected.payload,
            'has_drift_flag', rejected.has_drift_flag,
            'drift_fields', rejected.drift_fields,
            'exposed_at', rejected.exposed_at,
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

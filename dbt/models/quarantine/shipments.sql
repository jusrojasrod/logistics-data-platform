-- Cuarentena de envíos: una fila por fila rechazada de raw.shipments, nunca
-- una por regla incumplida.
--
-- rule_id es la regla principal según la prioridad declarada (DQ-001 antes que
-- DQ-002). reason nombra la regla principal y enumera todas las incumplidas;
-- payload conserva la fila original junto con dq_errors (en el mismo orden) y
-- dq_primary_rule. El shipment_id se lee de payload->>'shipment_id'.

with rejected as (

    select * from {{ ref('stg_tms__shipments_validated') }}
    where not is_valid

),

keyed as (

    -- Columnas simples primero y cálculos después; el select final fija el
    -- orden de las ocho columnas de cuarentena.

    select
        rejected.run_id,
        rejected.dq_primary_rule as rule_id,
        'raw.shipments' as source_table,
        'high' as severity,
        md5(
            concat_ws(
                '|',
                rejected.run_id,
                rejected.shipment_id,
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
            'shipment_id', rejected.shipment_id,
            'customer_id', rejected.customer_id,
            'carrier_id', rejected.carrier_id,
            'route_id', rejected.route_id,
            'service_type_id', rejected.service_type_id,
            'pickup_ts', rejected.pickup_ts,
            'pickup_date', rejected.pickup_date,
            'promised_delivery_ts', rejected.promised_delivery_ts,
            'picked_up_at', rejected.picked_up_at,
            'delivered_at', rejected.delivered_at,
            'ordered_qty', rejected.ordered_qty,
            'delivered_qty', rejected.delivered_qty,
            'ordered_weight_kg', rejected.ordered_weight_kg,
            'delivered_weight_kg', rejected.delivered_weight_kg,
            'definitive_failure_flag', rejected.definitive_failure_flag,
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

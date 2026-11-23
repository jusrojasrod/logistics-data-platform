-- Cuarentena financiera: una fila por fila rechazada de las seis entidades ERP,
-- nunca una por regla incumplida. Reúne tarifas, cabeceras y líneas de factura,
-- pagos, liquidaciones a carrier y costos de flota propia.
--
-- rule_id es la regla principal según la prioridad declarada en cada modelo
-- validado (DQ-009, DQ-010 y DQ-013). reason nombra la regla principal y
-- enumera todas las incumplidas; payload conserva la clave natural, los
-- metadatos y dq_errors. La severidad es high para las tres reglas aplicables.
-- Junto con los seis modelos stg_erp__*_valid forma la partición de cada
-- entidad sobre raw.

with rejected as (

    select
        'raw.tariffs' as source_table,
        'tariff_id|valid_from' as natural_key_column,
        concat_ws('|', tariff_id, valid_from) as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_erp__tariffs_validated') }}
    where not is_valid

    union all

    select
        'raw.invoices' as source_table,
        'invoice_id' as natural_key_column,
        invoice_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_erp__invoices_validated') }}
    where not is_valid

    union all

    select
        'raw.invoice_lines' as source_table,
        'invoice_line_id' as natural_key_column,
        invoice_line_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_erp__invoice_lines_validated') }}
    where not is_valid

    union all

    select
        'raw.payments' as source_table,
        'payment_id' as natural_key_column,
        payment_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_erp__payments_validated') }}
    where not is_valid

    union all

    select
        'raw.carrier_settlements' as source_table,
        'settlement_line_id' as natural_key_column,
        settlement_line_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_erp__carrier_settlements_validated') }}
    where not is_valid

    union all

    select
        'raw.shipment_costs' as source_table,
        'shipment_cost_id' as natural_key_column,
        shipment_cost_id as natural_key,
        run_id,
        source_system,
        source_file,
        source_file_hash,
        ingested_at,
        copy_rank,
        dq_errors,
        dq_primary_rule
    from {{ ref('stg_erp__shipment_costs_validated') }}
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

-- Envíos de la vía API clasificados por reglas de calidad. Cada fila de
-- stg_api__shipments aparece una sola vez con su lista de reglas incumplidas.
--
-- Prioridad declarada (determinista): DQ-001 antes que DQ-002 y DQ-008.
-- dq_errors conserva ese orden y dq_primary_rule es su primer elemento.
--   DQ-001: shipment_id nulo o copia repetida (copy_rank > 1).
--   DQ-002: delivered_at < picked_up_at. Si falta alguna fecha no incumple.
--   DQ-008: has_drift_flag (elemento con entradas en meta.api_schema_drift).
-- Una fila sin reglas incumplidas tiene dq_errors vacío e is_valid = true.

with shipments as (

    select * from {{ ref('stg_api__shipments') }}

),

checked as (

    select
        shipments.*,
        array_remove(
            array[
                case
                    when shipments.shipment_id is null or shipments.copy_rank > 1
                        then 'DQ-001'
                end,
                case
                    when shipments.delivered_at < shipments.picked_up_at
                        then 'DQ-002'
                end,
                case
                    when shipments.has_drift_flag
                        then 'DQ-008'
                end
            ]::text [],
            null
        ) as dq_errors
    from shipments

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

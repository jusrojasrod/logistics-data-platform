-- Eventos de envío de la vía API clasificados por reglas de calidad. Cada fila
-- de stg_api__shipment_events aparece una sola vez con sus reglas incumplidas.
--
-- Prioridad declarada (determinista): DQ-001 antes que DQ-008. dq_errors
-- conserva ese orden y dq_primary_rule es su primer elemento.
--   DQ-001: event_id nulo o copia repetida (copy_rank > 1).
--   DQ-008: has_drift_flag (elemento con entradas en meta.api_schema_drift).
-- DQ-002 no aplica a este recurso: no tiene fechas de entrega y recogida.
-- Una fila sin reglas incumplidas tiene dq_errors vacío e is_valid = true.

with events as (

    select * from {{ ref('stg_api__shipment_events') }}

),

checked as (

    select
        events.*,
        array_remove(
            array[
                case
                    when events.event_id is null or events.copy_rank > 1
                        then 'DQ-001'
                end,
                case
                    when events.has_drift_flag
                        then 'DQ-008'
                end
            ]::text [],
            null
        ) as dq_errors
    from events

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

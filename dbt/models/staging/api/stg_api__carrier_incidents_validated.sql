-- Incidencias de transportista de la vía API clasificadas por reglas de calidad.
-- Cada fila de stg_api__carrier_incidents aparece una sola vez con sus reglas
-- incumplidas.
--
-- Prioridad declarada (determinista): DQ-001 antes que DQ-008. dq_errors
-- conserva ese orden y dq_primary_rule es su primer elemento.
--   DQ-001: incident_id nulo o copia repetida (copy_rank > 1).
--   DQ-008: has_drift_flag (elemento con entradas en meta.api_schema_drift).
-- DQ-002 no aplica a este recurso: no tiene fechas de entrega y recogida.
-- Una fila sin reglas incumplidas tiene dq_errors vacío e is_valid = true.

with incidents as (

    select * from {{ ref('stg_api__carrier_incidents') }}

),

checked as (

    select
        incidents.*,
        array_remove(
            array[
                case
                    when incidents.incident_id is null or incidents.copy_rank > 1
                        then 'DQ-001'
                end,
                case
                    when incidents.has_drift_flag
                        then 'DQ-008'
                end
            ]::text [],
            null
        ) as dq_errors
    from incidents

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

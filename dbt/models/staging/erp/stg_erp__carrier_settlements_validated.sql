-- Liquidaciones a carrier ERP clasificadas por reglas de calidad. Cada fila
-- de stg_erp__carrier_settlements aparece una sola vez con su lista de
-- reglas incumplidas.
--
-- Prioridad declarada (determinista): DQ-013 antes que DQ-010. dq_errors
-- conserva ese orden y dq_primary_rule es su primer elemento.
--   DQ-013: copia repetida de settlement_line_id (copy_rank > 1).
--   DQ-010: moneda fuera del conjunto permitido (COP).
-- Una fila sin reglas incumplidas tiene dq_errors vacío e is_valid = true.

with carrier_settlements as (

    select * from {{ ref('stg_erp__carrier_settlements') }}

),

checked as (

    select
        carrier_settlements.*,
        array_remove(
            array[
                case
                    when carrier_settlements.copy_rank > 1
                        then 'DQ-013'
                end,
                case
                    when
                        carrier_settlements.currency_code is null
                        or carrier_settlements.currency_code <> 'COP'
                        then 'DQ-010'
                end
            ]::text [],
            null
        ) as dq_errors
    from carrier_settlements

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

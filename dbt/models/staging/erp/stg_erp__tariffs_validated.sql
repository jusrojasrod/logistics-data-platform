-- Tarifas ERP clasificadas por reglas de calidad. Cada fila de
-- stg_erp__tariffs aparece una sola vez con su lista de reglas incumplidas.
--
-- Prioridad declarada (determinista): DQ-010 (moneda fuera del conjunto
-- permitido: COP). dq_errors conserva ese orden y dq_primary_rule es su
-- primer elemento. Una fila sin reglas incumplidas tiene dq_errors vacío
-- e is_valid = true.

with tariffs as (

    select * from {{ ref('stg_erp__tariffs') }}

),

checked as (

    select
        tariffs.*,
        array_remove(
            array[
                case
                    when
                        tariffs.currency_code is null
                        or tariffs.currency_code <> 'COP'
                        then 'DQ-010'
                end
            ]::text [],
            null
        ) as dq_errors
    from tariffs

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

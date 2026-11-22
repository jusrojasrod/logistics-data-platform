-- Líneas de factura ERP clasificadas por reglas de calidad. Cada fila de
-- stg_erp__invoice_lines aparece una sola vez con su lista de reglas
-- incumplidas.
--
-- Prioridad declarada (determinista): DQ-009 antes que DQ-010. dq_errors
-- conserva ese orden y dq_primary_rule es su primer elemento.
--   DQ-009: copia repetida de invoice_line_id (copy_rank > 1).
--   DQ-010: moneda fuera del conjunto permitido (COP).
-- Una fila sin reglas incumplidas tiene dq_errors vacío e is_valid = true.

with invoice_lines as (

    select * from {{ ref('stg_erp__invoice_lines') }}

),

checked as (

    select
        invoice_lines.*,
        array_remove(
            array[
                case
                    when invoice_lines.copy_rank > 1
                        then 'DQ-009'
                end,
                case
                    when
                        invoice_lines.currency_code is null
                        or invoice_lines.currency_code <> 'COP'
                        then 'DQ-010'
                end
            ]::text [],
            null
        ) as dq_errors
    from invoice_lines

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

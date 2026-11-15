-- Reconciliación de mart_operations con fact_shipments.
--
-- Cada una de las cuatro medidas del mart sumada sobre todas sus filas debe
-- coincidir con el conteo equivalente en el hecho: closed_shipments con los
-- envíos con closed_flag, otif_shipments con otif_flag, on_time_shipments con
-- on_time_flag e in_full_shipments con in_full_flag. Las banderas nulas no
-- cuentan. Un mart vacío suma 0, no NULL. Devuelve una fila por medida
-- descuadrada; si todo cuadra, cero filas.

with mart_totals as (

    select
        coalesce(sum(mart.closed_shipments), 0) as closed_shipments,
        coalesce(sum(mart.otif_shipments), 0) as otif_shipments,
        coalesce(sum(mart.on_time_shipments), 0) as on_time_shipments,
        coalesce(sum(mart.in_full_shipments), 0) as in_full_shipments
    from {{ ref('mart_operations') }} as mart

),

fact_totals as (

    select
        count(*) filter (where fact.closed_flag) as closed_shipments,
        count(*) filter (where fact.otif_flag) as otif_shipments,
        count(*) filter (where fact.on_time_flag) as on_time_shipments,
        count(*) filter (where fact.in_full_flag) as in_full_shipments
    from {{ ref('fact_shipments') }} as fact

),

comparison as (

    select
        'closed_shipments' as measure_name,
        mart_totals.closed_shipments as mart_total,
        fact_totals.closed_shipments as fact_total
    from mart_totals
    cross join fact_totals

    union all

    select
        'otif_shipments' as measure_name,
        mart_totals.otif_shipments as mart_total,
        fact_totals.otif_shipments as fact_total
    from mart_totals
    cross join fact_totals

    union all

    select
        'on_time_shipments' as measure_name,
        mart_totals.on_time_shipments as mart_total,
        fact_totals.on_time_shipments as fact_total
    from mart_totals
    cross join fact_totals

    union all

    select
        'in_full_shipments' as measure_name,
        mart_totals.in_full_shipments as mart_total,
        fact_totals.in_full_shipments as fact_total
    from mart_totals
    cross join fact_totals

)

select
    comparison.measure_name,
    comparison.mart_total,
    comparison.fact_total,
    comparison.mart_total - comparison.fact_total as difference
from comparison
where comparison.mart_total <> comparison.fact_total

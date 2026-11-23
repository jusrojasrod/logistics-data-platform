-- Las filas tardías de las seis fuentes ERP llegan a su hecho o dimensión.
--
-- Por entidad se comparan dos recuentos: las filas de origen tardío que
-- sobreviven en el modelo válido y las que además están presentes en el hecho
-- o dimensión publicados. Cada entidad debe aportar al menos una clave tardía
-- en ambos lados. Se emite una fila de fallo cuando el recuento del lado del
-- hecho es cero, de modo que la aserción no pueda pasar con una población
-- tardía vacía ni con todas las claves tardías descartadas.
-- Devuelve cero filas cuando las seis entidades tienen tardíos en el hecho.

with late_counts as (

    select
        'tariffs' as entity,
        (
            select count(*)
            from {{ ref('stg_erp__tariffs_valid') }} as tariffs
            where tariffs.source_file like '%\_late.csv'
        ) as valid_late_count,
        (
            select count(*)
            from {{ ref('stg_erp__tariffs_valid') }} as tariffs
            inner join {{ ref('dim_tariff') }} as tariffs_dim
                on
                    md5(tariffs.tariff_id || '|' || tariffs.valid_from)
                    = tariffs_dim.tariff_key
            where tariffs.source_file like '%\_late.csv'
        ) as fact_late_count

    union all

    select
        'invoices' as entity,
        (
            select count(*)
            from {{ ref('stg_erp__invoices_valid') }} as invoices
            where invoices.source_file like '%\_late.csv'
        ) as valid_late_count,
        (
            select count(*)
            from {{ ref('stg_erp__invoices_valid') }} as invoices
            inner join {{ ref('fact_invoices') }} as facts
                on invoices.invoice_id = facts.invoice_id
            where invoices.source_file like '%\_late.csv'
        ) as fact_late_count

    union all

    select
        'invoice_lines' as entity,
        (
            select count(*)
            from {{ ref('stg_erp__invoice_lines_valid') }} as line_items
            where line_items.source_file like '%\_late.csv'
        ) as valid_late_count,
        (
            select count(*)
            from {{ ref('stg_erp__invoice_lines_valid') }} as line_items
            inner join {{ ref('fact_invoices') }} as facts
                on line_items.invoice_line_id = facts.invoice_line_id
            where line_items.source_file like '%\_late.csv'
        ) as fact_late_count

    union all

    select
        'payments' as entity,
        (
            select count(*)
            from {{ ref('stg_erp__payments_valid') }} as payments
            where payments.source_file like '%\_late.csv'
        ) as valid_late_count,
        (
            select count(*)
            from {{ ref('stg_erp__payments_valid') }} as payments
            inner join {{ ref('fact_invoices') }} as facts
                on payments.payment_id = facts.payment_id
            where payments.source_file like '%\_late.csv'
        ) as fact_late_count

    union all

    select
        'carrier_settlements' as entity,
        (
            select count(*)
            from {{ ref('stg_erp__carrier_settlements_valid') }} as settlements
            where settlements.source_file like '%\_late.csv'
        ) as valid_late_count,
        (
            select count(*)
            from {{ ref('stg_erp__carrier_settlements_valid') }} as settlements
            inner join {{ ref('fact_carrier_settlements') }} as facts
                on settlements.settlement_line_id = facts.settlement_line_id
            where settlements.source_file like '%\_late.csv'
        ) as fact_late_count

    union all

    select
        'shipment_costs' as entity,
        (
            select count(*)
            from {{ ref('stg_erp__shipment_costs_valid') }} as ship_costs
            where ship_costs.source_file like '%\_late.csv'
        ) as valid_late_count,
        (
            select count(*)
            from {{ ref('stg_erp__shipment_costs_valid') }} as ship_costs
            inner join {{ ref('fact_shipment_costs') }} as facts
                on ship_costs.shipment_cost_id = facts.shipment_cost_id
            where ship_costs.source_file like '%\_late.csv'
        ) as fact_late_count

)

select
    late_counts.entity,
    late_counts.valid_late_count,
    late_counts.fact_late_count
from late_counts
where late_counts.fact_late_count = 0

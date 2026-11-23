-- Hecho de facturación: una fila por línea de factura válida. Integra la
-- cabecera y el pago en la propia línea, con los ids naturales y las claves de
-- las dimensiones existentes. El tariff_key se resuelve contra la versión de
-- tarifa vigente en pickup_date, con point-in-time de ambos límites: la
-- atribución histórica se hace por vigencia, nunca por la marca de versión
-- vigente. La unión es interna a la cabecera y al envío (una línea huérfana
-- queda fuera del hecho hasta que llegue su padre); el pago es opcional.

with lines as (

    select * from {{ ref('stg_erp__invoice_lines_valid') }}

),

invoices as (

    select * from {{ ref('stg_erp__invoices_valid') }}

),

payments as (

    select * from {{ ref('stg_erp__payments_valid') }}

),

shipments as (

    select * from {{ ref('fact_shipments') }}

),

tariffs as (

    select * from {{ ref('dim_tariff') }}

),

joined as (

    select
        lines.invoice_line_id,
        lines.invoice_id,
        lines.shipment_id,
        lines.line_number,
        lines.concept,
        lines.tariff_amount,
        lines.billed_revenue_amount,
        lines.tax_amount,
        lines.currency_code,
        invoices.invoice_date,
        invoices.due_date,
        invoices.invoice_status,
        invoices.total_amount,
        payments.payment_id,
        payments.paid_date,
        payments.payment_amount,
        payments.payment_method,
        shipments.customer_id,
        shipments.carrier_id,
        shipments.route_id,
        shipments.service_type_id,
        shipments.pickup_date,
        shipments.customer_key,
        shipments.carrier_key,
        shipments.route_key,
        shipments.service_type_key,
        tariffs.tariff_key
    from lines
    inner join invoices
        on lines.invoice_id = invoices.invoice_id
    left join payments
        on lines.invoice_id = payments.invoice_id
    inner join shipments
        on lines.shipment_id = shipments.shipment_id
    left join tariffs
        on
            shipments.customer_id = tariffs.customer_id
            and shipments.route_id = tariffs.route_id
            and shipments.service_type_id = tariffs.service_type_id
            and shipments.pickup_date >= tariffs.valid_from
            and shipments.pickup_date < tariffs.valid_to

)

select
    invoice_line_id,
    invoice_id,
    shipment_id,
    line_number,
    concept,
    tariff_amount,
    billed_revenue_amount,
    tax_amount,
    currency_code,
    invoice_date,
    due_date,
    invoice_status,
    total_amount,
    payment_id,
    paid_date,
    payment_amount,
    payment_method,
    customer_id,
    carrier_id,
    route_id,
    service_type_id,
    pickup_date,
    customer_key,
    carrier_key,
    route_key,
    service_type_key,
    tariff_key,
    billed_revenue_amount - tariff_amount as variance_amount,
    paid_date - invoice_date as payment_days
from joined

-- Hecho de liquidaciones a carrier: una fila por línea de liquidación válida
-- (settlement_line_id), es decir, envío × carrier × concepto. El carrier y su
-- clave se leen de la línea financiera; el resto de los ids naturales y las
-- claves de las dimensiones se leen del hecho de envíos. La unión es interna a
-- la línea y al envío: una línea huérfana queda fuera del hecho hasta que
-- llegue su padre. Sin importes en el hecho de envíos.

with settlements as (

    select * from {{ ref('stg_erp__carrier_settlements_valid') }}

),

shipments as (

    select * from {{ ref('fact_shipments') }}

),

carriers as (

    select * from {{ ref('dim_carrier') }}

),

joined as (

    select
        settlements.settlement_line_id,
        settlements.settlement_id,
        settlements.shipment_id,
        settlements.carrier_id,
        carriers.carrier_key,
        shipments.customer_id,
        shipments.customer_key,
        shipments.route_id,
        shipments.route_key,
        shipments.service_type_id,
        shipments.service_type_key,
        settlements.concept,
        settlements.settled_date,
        settlements.payable_amount,
        settlements.deduction_amount,
        settlements.fuel_surcharge_amount,
        settlements.settlement_status,
        settlements.currency_code
    from settlements
    inner join shipments
        on settlements.shipment_id = shipments.shipment_id
    left join carriers
        on settlements.carrier_id = carriers.carrier_id

)

select
    settlement_line_id,
    settlement_id,
    shipment_id,
    carrier_id,
    carrier_key,
    customer_id,
    customer_key,
    route_id,
    route_key,
    service_type_id,
    service_type_key,
    concept,
    settled_date,
    payable_amount,
    deduction_amount,
    fuel_surcharge_amount,
    settlement_status,
    currency_code
from joined

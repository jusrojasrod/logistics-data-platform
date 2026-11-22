-- Hecho de costos de flota propia: una fila por costo válido
-- (shipment_cost_id), es decir, envío × componente de costo. El carrier y su
-- clave se leen de la fila de costo; el resto de los ids naturales y las claves
-- de las dimensiones se leen del hecho de envíos. La unión es interna a la
-- fila de costo y al envío: un costo huérfano queda fuera del hecho hasta que
-- llegue su padre. Sin importes en el hecho de envíos.

with costs as (

    select * from {{ ref('stg_erp__shipment_costs_valid') }}

),

shipments as (

    select * from {{ ref('fact_shipments') }}

),

carriers as (

    select * from {{ ref('dim_carrier') }}

),

joined as (

    select
        costs.shipment_cost_id,
        costs.shipment_id,
        costs.carrier_id,
        carriers.carrier_key,
        shipments.customer_id,
        shipments.customer_key,
        shipments.route_id,
        shipments.route_key,
        shipments.service_type_id,
        shipments.service_type_key,
        costs.cost_component,
        costs.cost_date,
        costs.cost_amount,
        costs.cost_center,
        costs.currency_code
    from costs
    inner join shipments
        on costs.shipment_id = shipments.shipment_id
    left join carriers
        on costs.carrier_id = carriers.carrier_id

)

select
    shipment_cost_id,
    shipment_id,
    carrier_id,
    carrier_key,
    customer_id,
    customer_key,
    route_id,
    route_key,
    service_type_id,
    service_type_key,
    cost_component,
    cost_date,
    cost_amount,
    cost_center,
    currency_code
from joined

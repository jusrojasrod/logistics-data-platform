-- Un envío no debe llevar a la vez liquidación de carrier y costo de flota
-- propia: sumar ambos hechos contaría dos veces la ejecución del mismo envío.
--
-- Se emite una fila por shipment_id presente en los dos hechos. También se
-- emite una fila si alguno de los dos hechos está vacío, para que la aserción
-- no pase por ausencia de datos. Devuelve cero filas cuando los dos modelos
-- están poblados y sus envíos son disjuntos.

with settlement_shipments as (

    select distinct settlements.shipment_id
    from {{ ref('fact_carrier_settlements') }} as settlements

),

cost_shipments as (

    select distinct ship_costs.shipment_id
    from {{ ref('fact_shipment_costs') }} as ship_costs

),

duplicated as (

    select settlement_shipments.shipment_id
    from settlement_shipments
    inner join cost_shipments
        on settlement_shipments.shipment_id = cost_shipments.shipment_id

),

empty_facts as (

    select 'fact_carrier_settlements' as model_name
    where not exists (select 1 from settlement_shipments)

    union all

    select 'fact_shipment_costs' as model_name
    where not exists (select 1 from cost_shipments)

)

select
    'duplicate_cost' as failure_type,
    duplicated.shipment_id,
    cast(null as text) as model_name
from duplicated

union all

select
    'empty_fact' as failure_type,
    cast(null as text) as shipment_id,
    empty_facts.model_name
from empty_facts

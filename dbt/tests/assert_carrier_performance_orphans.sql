-- Huérfanos de envío y transportista en los hechos de incidencias y ofertas
-- de transportistas.
--
-- Ningún shipment_id de los hechos puede carecer de padre en fact_shipments y
-- ningún carrier_key no nulo puede carecer de padre en dim_carrier. Los
-- huérfanos de envío se descartan en los hechos por unión interna y solo se
-- avisan en staging; aquí se exige su ausencia con los hechos poblados. Una
-- clave de transportista nula es esperada cuando el id no existe en la
-- dimensión y no se cuenta como huérfana.
-- Devuelve una fila por clave huérfana o hecho vacío; sin incumplimientos,
-- cero filas.

with fact_keys as (

    select
        'fact_carrier_incidents' as model_name,
        incidents.shipment_id,
        incidents.carrier_key
    from {{ ref('fact_carrier_incidents') }} as incidents

    union all

    select
        'fact_carrier_tenders' as model_name,
        tenders.shipment_id,
        tenders.carrier_key
    from {{ ref('fact_carrier_tenders') }} as tenders

),

shipment_parents as (

    select distinct shipments.shipment_id
    from {{ ref('fact_shipments') }} as shipments

),

carrier_parents as (

    select distinct carriers.carrier_key
    from {{ ref('dim_carrier') }} as carriers

),

orphan_shipments as (

    select
        fact_keys.model_name,
        fact_keys.shipment_id as key_value,
        count(*) as row_count
    from fact_keys
    left join shipment_parents
        on fact_keys.shipment_id = shipment_parents.shipment_id
    where shipment_parents.shipment_id is null
    group by fact_keys.model_name, fact_keys.shipment_id

),

orphan_carriers as (

    select
        fact_keys.model_name,
        fact_keys.carrier_key as key_value,
        count(*) as row_count
    from fact_keys
    left join carrier_parents
        on fact_keys.carrier_key = carrier_parents.carrier_key
    where
        fact_keys.carrier_key is not null
        and carrier_parents.carrier_key is null
    group by fact_keys.model_name, fact_keys.carrier_key

),

fact_counts as (

    select
        fact_keys.model_name,
        count(*) as row_count
    from fact_keys
    group by fact_keys.model_name

),

expected_models as (

    select model_name
    from (
        values
        ('fact_carrier_incidents'),
        ('fact_carrier_tenders')
    ) as expected (model_name)

),

empty_facts as (

    select
        expected_models.model_name,
        0::bigint as row_count
    from expected_models
    left join fact_counts
        on expected_models.model_name = fact_counts.model_name
    where coalesce(fact_counts.row_count, 0) = 0

)

select
    'orphan_shipment' as failure_type,
    orphan_shipments.model_name,
    orphan_shipments.key_value,
    orphan_shipments.row_count
from orphan_shipments

union all

select
    'orphan_carrier' as failure_type,
    orphan_carriers.model_name,
    orphan_carriers.key_value,
    orphan_carriers.row_count
from orphan_carriers

union all

select
    'empty_fact' as failure_type,
    empty_facts.model_name,
    null::text as key_value,
    empty_facts.row_count
from empty_facts

-- DQ-004 y DQ-005 (alerta): claves de dimensión resueltas en fact_shipments.
--
-- Devuelve una fila por envío con alguna de las cuatro claves de dimensión
-- nula. DQ-004 cubre carrier_key y DQ-005 customer_key; route_key y
-- service_type_key se vigilan con el mismo criterio. Una clave nula puede
-- venir de un id natural nulo o de un id que no existe en la dimensión;
-- natural_id_null distingue ambos casos. No hay cuarentena ni miembro
-- inferido: con severity warn avisa sin romper el run.

{{ config(severity='warn') }}

with facts as (

    select * from {{ ref('fact_shipments') }}

),

missing_keys as (

    select
        facts.shipment_id,
        'DQ-005' as rule_id,
        'customer' as dimension_name,
        facts.customer_id as natural_id,
        facts.customer_id is null as natural_id_null
    from facts
    where facts.customer_key is null

    union all

    select
        facts.shipment_id,
        'DQ-004' as rule_id,
        'carrier' as dimension_name,
        facts.carrier_id as natural_id,
        facts.carrier_id is null as natural_id_null
    from facts
    where facts.carrier_key is null

    union all

    select
        facts.shipment_id,
        null as rule_id,
        'route' as dimension_name,
        facts.route_id as natural_id,
        facts.route_id is null as natural_id_null
    from facts
    where facts.route_key is null

    union all

    select
        facts.shipment_id,
        null as rule_id,
        'service_type' as dimension_name,
        facts.service_type_id as natural_id,
        facts.service_type_id is null as natural_id_null
    from facts
    where facts.service_type_key is null

),

shipments_with_missing_keys as (

    select
        missing_keys.shipment_id,
        array_agg(
            missing_keys.dimension_name
            order by missing_keys.dimension_name
        ) as missing_dimensions,
        array_remove(
            array_agg(missing_keys.rule_id order by missing_keys.rule_id),
            null
        ) as rule_ids,
        bool_or(not missing_keys.natural_id_null) as has_unknown_natural_id
    from missing_keys
    group by missing_keys.shipment_id

)

select * from shipments_with_missing_keys

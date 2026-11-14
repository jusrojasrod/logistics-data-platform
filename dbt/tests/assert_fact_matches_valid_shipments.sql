-- Correspondencia uno a uno entre envíos válidos y fact_shipments.
--
-- Cada shipment_id de stg_tms__shipments_valid aparece una sola vez en la
-- vista de válidos y exactamente una vez en el hecho, y el hecho no contiene
-- ids ausentes en la vista de válidos. El cruce completo cubre ambos
-- sentidos. Devuelve una fila por id incoherente; si no, cero filas.

with valid_counts as (

    select
        valid_shipments.shipment_id,
        count(*) as valid_count
    from {{ ref('stg_tms__shipments_valid') }} as valid_shipments
    group by valid_shipments.shipment_id

),

fact_counts as (

    select
        fact.shipment_id,
        count(*) as fact_count
    from {{ ref('fact_shipments') }} as fact
    group by fact.shipment_id

),

key_counts as (

    select
        coalesce(
            valid_counts.shipment_id, fact_counts.shipment_id
        ) as shipment_id,
        coalesce(valid_counts.valid_count, 0) as valid_count,
        coalesce(fact_counts.fact_count, 0) as fact_count
    from valid_counts
    full outer join fact_counts
        on valid_counts.shipment_id = fact_counts.shipment_id

)

select
    key_counts.shipment_id,
    key_counts.valid_count,
    key_counts.fact_count,
    case
        when key_counts.valid_count = 0 then 'fact_id_not_in_valid'
        when key_counts.valid_count > 1 then 'multiple_valid_rows'
        when key_counts.fact_count = 0 then 'valid_id_missing_in_fact'
        else 'multiple_fact_rows'
    end as failure_type
from key_counts
where
    key_counts.valid_count <> 1
    or key_counts.fact_count <> 1

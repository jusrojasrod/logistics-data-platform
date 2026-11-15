-- Agregado diario de operación: una fila por combinación observada de
-- pickup_date × customer_key × route_key en fact_shipments, tenga o no envíos
-- cerrados; no se genera rejilla completa. Solo guarda conteos aditivos: el
-- porcentaje se calcula al consultar, Σ(otif_shipments) / Σ(closed_shipments),
-- con NULL si el denominador es 0. Las claves de dimensión nulas se conservan
-- como su propio grupo para que las sumas cuadren con el hecho.

with shipments as (

    select * from {{ ref('fact_shipments') }}

),

aggregated as (

    select
        pickup_date,
        customer_key,
        route_key,
        -- Las banderas nulas de envíos abiertos no suman.
        count(*) filter (where closed_flag) as closed_shipments,
        count(*) filter (where otif_flag) as otif_shipments,
        count(*) filter (where on_time_flag) as on_time_shipments,
        count(*) filter (where in_full_flag) as in_full_shipments
    from shipments
    group by
        pickup_date,
        customer_key,
        route_key

)

select
    pickup_date,
    customer_key,
    route_key,
    closed_shipments,
    otif_shipments,
    on_time_shipments,
    in_full_shipments
from aggregated

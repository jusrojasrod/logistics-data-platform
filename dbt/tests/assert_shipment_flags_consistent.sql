-- Coherencia de las banderas de fact_shipments.
--
-- otif_flag debe ser exactamente on_time_flag and in_full_flag en cada fila.
-- La comparación usa is not distinct from para que los nulos de envíos sin
-- entrega o sin promesa cuenten como iguales y no como incumplimiento.
-- Devuelve una fila por envío incoherente; sin incumplimientos, cero filas.

with fact as (

    select * from {{ ref('fact_shipments') }}

)

select
    fact.shipment_id,
    fact.on_time_flag,
    fact.in_full_flag,
    fact.otif_flag
from fact
where
    not (
        fact.otif_flag
        is not distinct from (fact.on_time_flag and fact.in_full_flag)
    )

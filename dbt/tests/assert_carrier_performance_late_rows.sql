-- Las filas publicadas después de su fecha de negocio llegan a los hechos de
-- incidencias y ofertas de transportistas.
--
-- Se considera tardía una fila cuya fecha de ingesta es posterior a la fecha
-- de negocio: reported_ts en incidencias y offered_ts en ofertas. No se filtra
-- por archivo de origen ni por ejecución, porque la vía por lotes no genera
-- archivos tardíos. Por entidad se comparan dos recuentos: las filas tardías
-- que sobreviven en el modelo válido y las que además están en el hecho
-- publicado, emparejadas por clave natural. Cada entidad debe aportar al menos
-- una fila tardía en el hecho; se emite una fila de fallo cuando el recuento
-- del lado del hecho es cero, de modo que la aserción no pase sin tardíos ni
-- con todas las claves tardías descartadas. La aserción no exige que todas las
-- filas tardías lleguen al hecho: las huérfanas de envío se descartan aguas
-- arriba por unión interna y no rompen el test.
-- Devuelve cero filas cuando las dos entidades tienen tardíos en el hecho.

with late_incidents as (

    select incident_id
    from {{ ref('stg_tms__carrier_incidents_valid') }}
    where date(ingested_at) > date(reported_ts)

),

late_tenders as (

    select tender_id
    from {{ ref('stg_tms__carrier_tenders_valid') }}
    where date(ingested_at) > date(offered_ts)

),

late_counts as (

    select
        'carrier_incidents' as entity,
        (
            select count(*)
            from late_incidents
        ) as valid_late_count,
        (
            select count(*)
            from late_incidents
            inner join {{ ref('fact_carrier_incidents') }} as facts
                on late_incidents.incident_id = facts.incident_id
        ) as fact_late_count

    union all

    select
        'carrier_tenders' as entity,
        (
            select count(*)
            from late_tenders
        ) as valid_late_count,
        (
            select count(*)
            from late_tenders
            inner join {{ ref('fact_carrier_tenders') }} as facts
                on late_tenders.tender_id = facts.tender_id
        ) as fact_late_count

)

select
    late_counts.entity,
    late_counts.valid_late_count,
    late_counts.fact_late_count
from late_counts
where late_counts.fact_late_count = 0

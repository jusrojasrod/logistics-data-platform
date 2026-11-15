-- DQ-003 (alerta): delivered_weight_kg entre 0.1 y 40 000 kg.
-- Se evalúa sobre delivered_weight_kg, que es la weight_kg del catálogo de
-- reglas. Los nulos de envíos sin entrega no cuentan. Devuelve una fila por
-- envío fuera de rango; con severity warn avisa sin rechazar filas.

{{ config(severity='warn') }}

with validated as (

    select * from {{ ref('stg_tms__shipments_validated') }}

),

out_of_range as (

    select
        validated.shipment_id,
        validated.run_id,
        validated.copy_rank,
        validated.delivered_weight_kg
    from validated
    where
        validated.delivered_weight_kg is not null
        and (
            validated.delivered_weight_kg < 0.1
            or validated.delivered_weight_kg > 40000
        )

)

select * from out_of_range

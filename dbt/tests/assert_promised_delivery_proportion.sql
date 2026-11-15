-- DQ-011 (alerta): proporción de promised_delivery_ts no nulo >= 0.99.
-- Devuelve una fila si la proporción cae por debajo del umbral; con severity
-- warn avisa sin rechazar filas. Los envíos con promised_delivery_ts nulo se
-- conservan y sus banderas de puntualidad quedan nulas.

{{ config(severity='warn') }}

with validated as (

    select * from {{ ref('stg_tms__shipments_validated') }}

),

proportion as (

    select
        count(*) as total_rows,
        count(promised_delivery_ts) as non_null_rows,
        count(promised_delivery_ts)::numeric / nullif(count(*), 0) as non_null_ratio
    from validated

)

select * from proportion
where non_null_ratio < 0.99

-- Conductores clasificados por reglas de calidad. Cada fila de
-- stg_tms__drivers aparece una sola vez con su lista de reglas incumplidas.
-- No hay reglas aplicables en esta entidad: dq_errors queda vacío,
-- dq_primary_rule es nulo e is_valid es verdadero para toda fila. Las copias
-- repetidas por clave natural se descartan en el modelo validado, no aquí.

with drivers as (

    select * from {{ ref('stg_tms__drivers') }}

),

checked as (

    select
        drivers.*,
        array[]::text [] as dq_errors
    from drivers

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

-- Paradas de envío clasificadas por reglas de calidad. Cada fila de
-- stg_tms__shipment_stops aparece una sola vez con su lista de reglas
-- incumplidas. No hay reglas aplicables en esta entidad: dq_errors queda
-- vacío, dq_primary_rule es nulo e is_valid es verdadero para toda fila. Las
-- copias repetidas por clave natural se descartan en el modelo validado.

with shipment_stops as (

    select * from {{ ref('stg_tms__shipment_stops') }}

),

checked as (

    select
        shipment_stops.*,
        array[]::text [] as dq_errors
    from shipment_stops

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

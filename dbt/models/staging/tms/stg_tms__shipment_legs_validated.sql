-- Tramos de envío clasificados por reglas de calidad. Cada fila de
-- stg_tms__shipment_legs aparece una sola vez con su lista de reglas
-- incumplidas. No hay reglas aplicables en esta entidad: dq_errors queda
-- vacío, dq_primary_rule es nulo e is_valid es verdadero para toda fila. Las
-- copias repetidas por clave natural se descartan en el modelo validado.

with shipment_legs as (

    select * from {{ ref('stg_tms__shipment_legs') }}

),

checked as (

    select
        shipment_legs.*,
        array[]::text [] as dq_errors
    from shipment_legs

),

classified as (

    select
        checked.*,
        checked.dq_errors[1] as dq_primary_rule,
        cardinality(checked.dq_errors) = 0 as is_valid
    from checked

)

select * from classified

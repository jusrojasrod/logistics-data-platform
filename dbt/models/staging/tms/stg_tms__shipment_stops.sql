-- Paradas de envío tipadas desde raw.shipment_stops. Se lee todo raw sin
-- filtrar por run_id ni por fecha: las publicaciones posteriores a la fecha de
-- negocio llegan en la siguiente carga. No se borra ninguna fila; las copias
-- repetidas por clave natural se marcan con copy_rank para clasificarlas en el
-- modelo validado.

with source as (

    select * from {{ source('tms_operations', 'shipment_stops') }}

),

typed as (

    select
        cast(shipment_id as text) as shipment_id,
        cast(stop_sequence as integer) as stop_sequence,
        cast(stop_type as text) as stop_type,
        cast(city as text) as city,
        cast(planned_ts as timestamptz) as planned_ts,
        cast(actual_ts as timestamptz) as actual_ts,
        cast(run_id as text) as run_id,
        cast(source_system as text) as source_system,
        cast(source_file as text) as source_file,
        cast(source_file_hash as text) as source_file_hash,
        cast(ingested_at as timestamptz) as ingested_at
    from source

),

ranked as (

    select
        typed.*,
        row_number() over (
            partition by typed.shipment_id, typed.stop_sequence
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

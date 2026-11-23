-- Eventos de envío tipados desde raw.shipment_events. Se lee todo raw sin
-- filtrar por run_id ni por fecha: las publicaciones posteriores a la fecha de
-- negocio llegan en la siguiente carga. No se borra ninguna fila; las copias
-- repetidas por clave natural se marcan con copy_rank para clasificarlas en el
-- modelo validado.

with source as (

    select * from {{ source('tms_operations', 'shipment_events') }}

),

typed as (

    select
        cast(event_id as text) as event_id,
        cast(shipment_id as text) as shipment_id,
        cast(event_ts as timestamptz) as event_ts,
        cast(event_type as text) as event_type,
        cast(location_city as text) as location_city,
        cast(latitude as numeric) as latitude,
        cast(longitude as numeric) as longitude,
        cast(severity as text) as severity,
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
            partition by typed.event_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

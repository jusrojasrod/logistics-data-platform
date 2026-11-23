-- Incidencias de transportistas tipadas desde raw.carrier_incidents. Se lee
-- todo raw sin filtrar por run_id ni por fecha: las publicaciones posteriores
-- a la fecha de negocio llegan en la siguiente carga. No se borra ninguna
-- fila; las copias repetidas por clave natural se marcan con copy_rank para
-- clasificarlas en el modelo validado.

with source as (

    select * from {{ source('tms_carrier_performance', 'carrier_incidents') }}

),

typed as (

    select
        cast(incident_id as text) as incident_id,
        cast(shipment_id as text) as shipment_id,
        cast(carrier_id as text) as carrier_id,
        cast(incident_type as text) as incident_type,
        cast(severity as text) as severity,
        cast(damage_flag as boolean) as damage_flag,
        cast(claim_amount as numeric) as claim_amount,
        cast(currency_code as text) as currency_code,
        cast(reported_ts as timestamptz) as reported_ts,
        cast(responded_ts as timestamptz) as responded_ts,
        cast(resolved_ts as timestamptz) as resolved_ts,
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
            partition by typed.incident_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

-- Ofertas de transportistas tipadas desde raw.carrier_tenders. Se lee todo
-- raw sin filtrar por run_id ni por fecha: las publicaciones posteriores a la
-- fecha de negocio llegan en la siguiente carga. No se borra ninguna fila;
-- las copias repetidas por clave natural se marcan con copy_rank para
-- clasificarlas en el modelo validado.

with source as (

    select * from {{ source('tms_carrier_performance', 'carrier_tenders') }}

),

typed as (

    select
        cast(tender_id as text) as tender_id,
        cast(shipment_id as text) as shipment_id,
        cast(carrier_id as text) as carrier_id,
        cast(offered_ts as timestamptz) as offered_ts,
        cast(responded_ts as timestamptz) as responded_ts,
        cast(response_status as text) as response_status,
        cast(offered_rate_amount as numeric) as offered_rate_amount,
        cast(accepted_rate_amount as numeric) as accepted_rate_amount,
        cast(currency_code as text) as currency_code,
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
            partition by typed.tender_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

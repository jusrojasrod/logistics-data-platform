-- Transportistas tipados desde raw.carriers. Una fila por carrier_id: se
-- conserva la carga más reciente según ingested_at.

with source as (

    select * from {{ source('tms', 'carriers') }}

),

typed as (

    select
        cast(carrier_id as text) as carrier_id,
        cast(carrier_name as text) as carrier_name,
        cast(carrier_type as text) as carrier_type,
        cast(service_level as text) as service_level,
        cast(rate_index as numeric) as rate_index,
        cast(run_id as text) as run_id,
        cast(source_system as text) as source_system,
        cast(source_file_hash as text) as source_file_hash,
        cast(ingested_at as timestamptz) as ingested_at
    from source

),

ranked as (

    select
        typed.*,
        row_number() over (
            partition by typed.carrier_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as load_rank
    from typed

),

latest as (

    select
        carrier_id,
        carrier_name,
        carrier_type,
        service_level,
        rate_index,
        run_id,
        source_system,
        source_file_hash,
        ingested_at
    from ranked
    where load_rank = 1

)

select * from latest

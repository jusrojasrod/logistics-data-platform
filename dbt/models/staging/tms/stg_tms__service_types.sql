-- Tipos de servicio tipados desde raw.service_types. Una fila por
-- service_type_id: se conserva la carga más reciente según ingested_at.

with source as (

    select * from {{ source('tms', 'service_types') }}

),

typed as (

    select
        cast(service_type_id as text) as service_type_id,
        cast(service_type_name as text) as service_type_name,
        cast(sla_hours as integer) as sla_hours,
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
            partition by typed.service_type_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as load_rank
    from typed

),

latest as (

    select
        service_type_id,
        service_type_name,
        sla_hours,
        run_id,
        source_system,
        source_file_hash,
        ingested_at
    from ranked
    where load_rank = 1

)

select * from latest

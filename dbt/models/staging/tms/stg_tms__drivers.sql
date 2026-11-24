-- Conductores tipados desde raw.drivers. Se lee todo raw sin filtrar por run_id
-- ni por fecha: las publicaciones posteriores a la fecha de negocio llegan en
-- la siguiente carga. No se borra ninguna fila; las copias repetidas por clave
-- natural se marcan con copy_rank para clasificarlas en el modelo validado.

with source as (

    select * from {{ source('tms_operations', 'drivers') }}

),

typed as (

    select
        cast(driver_id as text) as driver_id,
        cast(carrier_id as text) as carrier_id,
        cast(driver_name as text) as driver_name,
        cast(driver_document as text) as driver_document,
        cast(driver_phone as text) as driver_phone,
        cast(driver_email as text) as driver_email,
        cast(license_type as text) as license_type,
        cast(hire_date as date) as hire_date,
        cast(base_city as text) as base_city,
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
            partition by typed.driver_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

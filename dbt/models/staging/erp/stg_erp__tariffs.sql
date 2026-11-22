-- Tarifas ERP tipadas desde raw.tariffs. Se lee todo raw sin filtrar por
-- run_id: principal y tardío pueden venir en cargas distintas. No se borra
-- ninguna fila; las copias repetidas por clave natural se marcan con
-- copy_rank para clasificarlas en el modelo validado.

with source as (

    select * from {{ source('erp_financial', 'tariffs') }}

),

typed as (

    select
        cast(tariff_id as text) as tariff_id,
        cast(customer_id as text) as customer_id,
        cast(route_id as text) as route_id,
        cast(service_type_id as text) as service_type_id,
        cast(valid_from as date) as valid_from,
        cast(valid_to as date) as valid_to,
        cast(base_amount as numeric) as base_amount,
        cast(per_km_amount as numeric) as per_km_amount,
        cast(fuel_surcharge_pct as numeric) as fuel_surcharge_pct,
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
            partition by typed.tariff_id, typed.valid_from
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

-- Clientes tipados desde raw.customers. Una fila por customer_id: se conserva
-- la carga más reciente según ingested_at.

with source as (

    select * from {{ source('erp', 'customers') }}

),

typed as (

    select
        cast(customer_id as text) as customer_id,
        cast(customer_name as text) as customer_name,
        cast(segment as text) as segment,
        cast(industry as text) as industry,
        cast(region as text) as region,
        cast(city as text) as city,
        cast(payment_term_days as integer) as payment_term_days,
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
            partition by typed.customer_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as load_rank
    from typed

),

latest as (

    select
        customer_id,
        customer_name,
        segment,
        industry,
        region,
        city,
        payment_term_days,
        run_id,
        source_system,
        source_file_hash,
        ingested_at
    from ranked
    where load_rank = 1

)

select * from latest

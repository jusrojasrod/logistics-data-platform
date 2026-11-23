-- Cabeceras de factura ERP tipadas desde raw.invoices. Se lee todo raw sin
-- filtrar por run_id: principal y tardío pueden venir en cargas distintas. No
-- se borra ninguna fila; las copias repetidas por clave natural se marcan con
-- copy_rank para clasificarlas en el modelo validado.

with source as (

    select * from {{ source('erp_financial', 'invoices') }}

),

typed as (

    select
        cast(invoice_id as text) as invoice_id,
        cast(customer_id as text) as customer_id,
        cast(shipment_id as text) as shipment_id,
        cast(invoice_date as date) as invoice_date,
        cast(due_date as date) as due_date,
        cast(invoice_status as text) as invoice_status,
        cast(total_amount as numeric) as total_amount,
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
            partition by typed.invoice_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

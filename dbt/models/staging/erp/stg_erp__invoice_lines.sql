-- Líneas de factura ERP tipadas desde raw.invoice_lines. Se lee todo raw sin
-- filtrar por run_id: principal y tardío pueden venir en cargas distintas. No
-- se borra ninguna fila; las copias repetidas por clave natural se marcan con
-- copy_rank para clasificarlas en el modelo validado.

with source as (

    select * from {{ source('erp_financial', 'invoice_lines') }}

),

typed as (

    select
        cast(invoice_line_id as text) as invoice_line_id,
        cast(invoice_id as text) as invoice_id,
        cast(shipment_id as text) as shipment_id,
        cast(line_number as integer) as line_number,
        cast(concept as text) as concept,
        cast(tariff_amount as numeric) as tariff_amount,
        cast(billed_revenue_amount as numeric) as billed_revenue_amount,
        cast(tax_amount as numeric) as tax_amount,
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
            partition by typed.invoice_line_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

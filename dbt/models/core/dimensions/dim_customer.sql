-- Dimensión de clientes de una sola versión: una fila por customer_id con
-- clave sustituta estable md5(customer_id). Sin historia (sin SCD2).

with source as (

    select * from {{ ref('stg_erp__customers') }}

),

keyed as (

    select
        source.customer_id,
        source.customer_name,
        source.segment,
        source.industry,
        source.region,
        source.city,
        source.payment_term_days,
        md5(source.customer_id) as customer_key
    from source

)

select
    customer_key,
    customer_id,
    customer_name,
    segment,
    industry,
    region,
    city,
    payment_term_days
from keyed

-- Costos de flota propia ERP tipados desde raw.shipment_costs. Se lee todo
-- raw sin filtrar por run_id: principal y tardío pueden venir en cargas
-- distintas. No se borra ninguna fila; las copias repetidas por clave natural
-- se marcan con copy_rank para clasificarlas en el modelo validado.

with source as (

    select * from {{ source('erp_financial', 'shipment_costs') }}

),

typed as (

    select
        cast(shipment_cost_id as text) as shipment_cost_id,
        cast(shipment_id as text) as shipment_id,
        cast(carrier_id as text) as carrier_id,
        cast(cost_component as text) as cost_component,
        cast(cost_date as date) as cost_date,
        cast(cost_amount as numeric) as cost_amount,
        cast(cost_center as text) as cost_center,
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
            partition by typed.shipment_cost_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

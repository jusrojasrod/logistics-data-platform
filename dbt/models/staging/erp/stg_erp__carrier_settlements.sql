-- Liquidaciones a carrier ERP tipadas desde raw.carrier_settlements. Se lee
-- todo raw sin filtrar por run_id: principal y tardío pueden venir en cargas
-- distintas. No se borra ninguna fila; las copias repetidas por clave natural
-- se marcan con copy_rank para clasificarlas en el modelo validado.

with source as (

    select * from {{ source('erp_financial', 'carrier_settlements') }}

),

typed as (

    select
        cast(settlement_line_id as text) as settlement_line_id,
        cast(settlement_id as text) as settlement_id,
        cast(shipment_id as text) as shipment_id,
        cast(carrier_id as text) as carrier_id,
        cast(concept as text) as concept,
        cast(settled_date as date) as settled_date,
        cast(payable_amount as numeric) as payable_amount,
        cast(deduction_amount as numeric) as deduction_amount,
        cast(fuel_surcharge_amount as numeric) as fuel_surcharge_amount,
        cast(settlement_status as text) as settlement_status,
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
            partition by typed.settlement_line_id
            order by typed.ingested_at desc, typed.source_file_hash desc
        ) as copy_rank
    from typed

)

select * from ranked

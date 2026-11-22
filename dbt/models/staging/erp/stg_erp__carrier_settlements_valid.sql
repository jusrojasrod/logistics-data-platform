-- Liquidaciones a carrier ERP válidas: filas de
-- stg_erp__carrier_settlements_validated sin reglas incumplidas. Una fila por
-- settlement_line_id con sus columnas de negocio, los cinco metadatos y
-- copy_rank. Sin descartar filas por huérfanos ni por origen tardío: solo se
-- excluyen las rechazadas por calidad.

with validated as (

    select * from {{ ref('stg_erp__carrier_settlements_validated') }}

)

select
    validated.settlement_line_id,
    validated.settlement_id,
    validated.shipment_id,
    validated.carrier_id,
    validated.concept,
    validated.settled_date,
    validated.payable_amount,
    validated.deduction_amount,
    validated.fuel_surcharge_amount,
    validated.settlement_status,
    validated.currency_code,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid

-- Costos de flota propia ERP válidos: filas de
-- stg_erp__shipment_costs_validated sin reglas incumplidas. Una fila por
-- shipment_cost_id con sus columnas de negocio, los cinco metadatos y
-- copy_rank. Sin descartar filas por huérfanos ni por origen tardío: solo se
-- excluyen las rechazadas por calidad.

with validated as (

    select * from {{ ref('stg_erp__shipment_costs_validated') }}

)

select
    validated.shipment_cost_id,
    validated.shipment_id,
    validated.carrier_id,
    validated.cost_component,
    validated.cost_date,
    validated.cost_amount,
    validated.cost_center,
    validated.currency_code,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid

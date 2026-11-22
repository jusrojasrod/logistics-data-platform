-- Tarifas ERP válidas: filas de stg_erp__tariffs_validated sin reglas
-- incumplidas. Una fila por (tariff_id, valid_from) con sus columnas de
-- negocio, los cinco metadatos y copy_rank. Sin descartar filas por huérfanos
-- ni por origen tardío: solo se excluyen las rechazadas por calidad.

with validated as (

    select * from {{ ref('stg_erp__tariffs_validated') }}

)

select
    validated.tariff_id,
    validated.customer_id,
    validated.route_id,
    validated.service_type_id,
    validated.valid_from,
    validated.valid_to,
    validated.base_amount,
    validated.per_km_amount,
    validated.fuel_surcharge_pct,
    validated.currency_code,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid

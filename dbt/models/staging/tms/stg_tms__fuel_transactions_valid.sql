-- Compras de combustible válidas: filas de stg_tms__fuel_transactions_validated
-- sin reglas incumplidas y primera copia por clave natural. Se conservan la
-- clave, las columnas de negocio, los cinco metadatos y copy_rank para linaje.
-- Con DQ-010 sobre currency_code, solo pasan las compras en COP. No se descartan
-- filas por envío huérfano ni por fecha de publicación.

with validated as (

    select * from {{ ref('stg_tms__fuel_transactions_validated') }}

)

select
    validated.fuel_txn_id,
    validated.trip_id,
    validated.vehicle_id,
    validated.driver_id,
    validated.txn_ts,
    validated.station_city,
    validated.gallons,
    validated.price_per_gallon_amount,
    validated.total_amount,
    validated.currency_code,
    validated.odometer_km,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid and validated.copy_rank = 1

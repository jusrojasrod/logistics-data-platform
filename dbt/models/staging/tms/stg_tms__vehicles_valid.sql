-- Vehículos válidos: filas de stg_tms__vehicles_validated sin reglas
-- incumplidas y primera copia por clave natural. Se conservan la clave, las
-- columnas de negocio, los cinco metadatos y copy_rank para linaje. No se
-- descartan filas por envío huérfano ni por fecha de publicación.

with validated as (

    select * from {{ ref('stg_tms__vehicles_validated') }}

)

select
    validated.vehicle_id,
    validated.carrier_id,
    validated.plate,
    validated.vehicle_type,
    validated.capacity_kg,
    validated.capacity_m3,
    validated.fuel_type,
    validated.km_per_gallon,
    validated.purchase_date,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid and validated.copy_rank = 1

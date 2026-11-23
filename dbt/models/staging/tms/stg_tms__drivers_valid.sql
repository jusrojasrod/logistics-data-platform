-- Conductores válidos: filas de stg_tms__drivers_validated sin reglas
-- incumplidas y primera copia por clave natural. Se conservan la clave, las
-- columnas de negocio, los cinco metadatos y copy_rank para linaje. No se
-- descartan filas por envío huérfano ni por fecha de publicación.

with validated as (

    select * from {{ ref('stg_tms__drivers_validated') }}

)

select
    validated.driver_id,
    validated.carrier_id,
    validated.driver_name,
    validated.driver_document,
    validated.driver_phone,
    validated.driver_email,
    validated.license_type,
    validated.hire_date,
    validated.base_city,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid and validated.copy_rank = 1

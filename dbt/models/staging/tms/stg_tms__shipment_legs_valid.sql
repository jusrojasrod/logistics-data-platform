-- Tramos de envío válidos: filas de stg_tms__shipment_legs_validated sin reglas
-- incumplidas y primera copia por clave natural. Se conservan la clave, las
-- columnas de negocio, los cinco metadatos y copy_rank para linaje. No se
-- descartan filas por envío huérfano ni por fecha de publicación. Fuente
-- exclusiva del puente envío-viaje.

with validated as (

    select * from {{ ref('stg_tms__shipment_legs_validated') }}

)

select
    validated.shipment_id,
    validated.trip_id,
    validated.leg_sequence,
    validated.loaded_km,
    validated.weight_kg,
    validated.volume_m3,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid and validated.copy_rank = 1

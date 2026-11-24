-- Paradas de envío válidas: filas de stg_tms__shipment_stops_validated sin
-- reglas incumplidas y primera copia por clave natural. Se conservan la clave,
-- las columnas de negocio, los cinco metadatos y copy_rank para linaje. No se
-- descartan filas por envío huérfano ni por fecha de publicación. Sin hecho
-- propio en este corte: la parada queda solo en staging.

with validated as (

    select * from {{ ref('stg_tms__shipment_stops_validated') }}

)

select
    validated.shipment_id,
    validated.stop_sequence,
    validated.stop_type,
    validated.city,
    validated.planned_ts,
    validated.actual_ts,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid and validated.copy_rank = 1

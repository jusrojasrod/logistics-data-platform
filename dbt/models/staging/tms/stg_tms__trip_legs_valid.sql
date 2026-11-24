-- Tramos de viaje válidos: filas de stg_tms__trip_legs_validated sin reglas
-- incumplidas y primera copia por clave natural. Se conservan la clave, las
-- columnas de negocio, los cinco metadatos y copy_rank para linaje. No se
-- descartan filas por envío huérfano ni por fecha de publicación. Sin hecho
-- propio en este corte: el tramo queda solo en staging.

with validated as (

    select * from {{ ref('stg_tms__trip_legs_validated') }}

)

select
    validated.trip_id,
    validated.leg_sequence,
    validated.origin_city,
    validated.destination_city,
    validated.distance_km,
    validated.departure_ts,
    validated.arrival_ts,
    validated.loaded_flag,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid and validated.copy_rank = 1

-- Viajes válidos: filas de stg_tms__trips_validated sin reglas incumplidas y
-- primera copia por clave natural. Se conservan la clave, las columnas de
-- negocio, los cinco metadatos y copy_rank para linaje. No se descartan filas
-- por envío huérfano ni por fecha de publicación.

with validated as (

    select * from {{ ref('stg_tms__trips_validated') }}

)

select
    validated.trip_id,
    validated.carrier_id,
    validated.vehicle_id,
    validated.driver_id,
    validated.trip_type,
    validated.origin_city,
    validated.start_ts,
    validated.end_ts,
    validated.distance_km,
    validated.empty_km,
    validated.stopped_minutes,
    validated.fuel_gallons,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid and validated.copy_rank = 1

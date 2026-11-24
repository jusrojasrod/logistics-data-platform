-- Eventos de envío válidos: filas de stg_tms__shipment_events_validated sin
-- reglas incumplidas y primera copia por clave natural. Una fila por event_id
-- con su event_ts y medidas, los cinco metadatos y copy_rank para linaje. No se
-- descartan filas por envío huérfano ni por fecha de publicación.

with validated as (

    select * from {{ ref('stg_tms__shipment_events_validated') }}

)

select
    validated.event_id,
    validated.shipment_id,
    validated.event_ts,
    validated.event_type,
    validated.location_city,
    validated.latitude,
    validated.longitude,
    validated.severity,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid and validated.copy_rank = 1

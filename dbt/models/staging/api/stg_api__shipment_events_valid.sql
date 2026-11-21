-- Eventos de envío válidos de la vía API: filas de
-- stg_api__shipment_events_validated sin reglas incumplidas. Una fila por
-- event_id dentro de run_id + dataset; las copias repetidas y los elementos
-- con drift van a quarantine.api_items.

with validated as (

    select * from {{ ref('stg_api__shipment_events_validated') }}
    where is_valid

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
    validated.endpoint,
    validated.page,
    validated.payload_sha256,
    validated.ingested_at,
    validated.dataset_name,
    validated.source_key,
    validated.item_index,
    validated.payload,
    validated.has_drift_flag,
    validated.drift_fields,
    validated.exposed_at,
    validated.copy_rank
from validated

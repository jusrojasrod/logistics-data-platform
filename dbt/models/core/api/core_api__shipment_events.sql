-- Eventos de envío válidos de la vía API publicados en core. Una fila por
-- elemento válido con grano (run_id, endpoint, page, item_index); sin drift,
-- sin claves sustitutas, sin banderas de cálculo y sin importes.

with valid_rows as (

    select * from {{ ref('stg_api__shipment_events_valid') }}
    where not has_drift_flag

)

select
    valid_rows.event_id,
    valid_rows.shipment_id,
    valid_rows.event_ts,
    valid_rows.event_type,
    valid_rows.location_city,
    valid_rows.latitude,
    valid_rows.longitude,
    valid_rows.severity,
    valid_rows.run_id,
    valid_rows.source_system,
    valid_rows.endpoint,
    valid_rows.page,
    valid_rows.payload_sha256,
    valid_rows.ingested_at,
    valid_rows.dataset_name,
    valid_rows.source_key,
    valid_rows.item_index
from valid_rows

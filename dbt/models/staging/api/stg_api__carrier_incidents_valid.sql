-- Incidencias de transportista válidas de la vía API: filas de
-- stg_api__carrier_incidents_validated sin reglas incumplidas. Una fila por
-- incident_id dentro de run_id + dataset; las copias repetidas y los elementos
-- con drift van a quarantine.api_items.

with validated as (

    select * from {{ ref('stg_api__carrier_incidents_validated') }}
    where is_valid

)

select
    validated.incident_id,
    validated.shipment_id,
    validated.carrier_id,
    validated.incident_type,
    validated.severity,
    validated.damage_flag,
    validated.claim_amount,
    validated.currency_code,
    validated.reported_ts,
    validated.responded_ts,
    validated.resolved_ts,
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

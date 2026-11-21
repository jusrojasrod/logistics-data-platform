-- Envíos válidos de la vía API: filas de stg_api__shipments_validated sin
-- reglas incumplidas. Una fila por shipment_id dentro de run_id + dataset;
-- las copias repetidas y los elementos con drift van a quarantine.api_items.

with validated as (

    select * from {{ ref('stg_api__shipments_validated') }}
    where is_valid

)

select
    validated.shipment_id,
    validated.customer_id,
    validated.carrier_id,
    validated.route_id,
    validated.service_type_id,
    validated.pickup_ts,
    validated.pickup_date,
    validated.promised_delivery_ts,
    validated.picked_up_at,
    validated.delivered_at,
    validated.ordered_qty,
    validated.delivered_qty,
    validated.ordered_weight_kg,
    validated.delivered_weight_kg,
    validated.definitive_failure_flag,
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

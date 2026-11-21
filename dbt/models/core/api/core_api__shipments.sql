-- Envíos válidos de la vía API publicados en core. Una fila por elemento válido
-- con grano (run_id, endpoint, page, item_index); sin drift, sin claves
-- sustitutas, sin banderas de cálculo y sin importes. No redefine el hecho
-- canónico de la vía por lotes.

with valid_rows as (

    select * from {{ ref('stg_api__shipments_valid') }}
    where not has_drift_flag

)

select
    valid_rows.shipment_id,
    valid_rows.customer_id,
    valid_rows.carrier_id,
    valid_rows.route_id,
    valid_rows.service_type_id,
    valid_rows.pickup_ts,
    valid_rows.pickup_date,
    valid_rows.promised_delivery_ts,
    valid_rows.picked_up_at,
    valid_rows.delivered_at,
    valid_rows.ordered_qty,
    valid_rows.delivered_qty,
    valid_rows.ordered_weight_kg,
    valid_rows.delivered_weight_kg,
    valid_rows.definitive_failure_flag,
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

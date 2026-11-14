-- Envíos válidos: filas de stg_tms__shipments_validated sin reglas incumplidas.
-- Una fila por shipment_id; las copias repetidas y las filas rechazadas van a
-- quarantine.shipments.

with validated as (

    select * from {{ ref('stg_tms__shipments_validated') }}

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
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid

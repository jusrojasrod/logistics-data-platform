-- Hecho de eventos de envío: una fila por evento válido (event_id), con fecha
-- de negocio event_ts y sin partición ni versión. shipment_id se une con left
-- join a fact_shipments para conservar los eventos cuyo envío aún no llegó; la
-- integridad se avisa en el esquema, sin rechazar filas aquí. Sin KPIs, montos
-- ni conversión de moneda.

with events as (

    select * from {{ ref('stg_tms__shipment_events_valid') }}

),

shipments as (

    select shipment_id from {{ ref('fact_shipments') }}

)

select
    events.event_id,
    events.shipment_id,
    events.event_ts,
    events.event_type,
    events.location_city,
    events.latitude,
    events.longitude,
    events.severity,
    events.run_id,
    events.source_system,
    events.source_file,
    events.source_file_hash,
    events.ingested_at
from events
left join shipments
    on events.shipment_id = shipments.shipment_id

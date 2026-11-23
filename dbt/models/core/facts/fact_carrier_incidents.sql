-- Hecho de incidencias de transportistas: una fila por incidencia válida
-- (incident_id), con fecha de negocio reported_ts y sin partición ni versión.
-- El estado y el cierre son atributos de esa fila, no eventos ni versiones.
-- El envío se une de forma interna a fact_shipments: una incidencia huérfana
-- queda fuera del hecho hasta que llegue su padre (DQ-004 avisa en el staging
-- _valid, sin rechazarla allí). El transportista se une con left join a
-- dim_carrier, de una sola versión, con carrier_key = md5(carrier_id): la clave
-- queda nula si el id no existe, sin inferidos ni SCD2. Los nulos de respuesta
-- y cierre permanecen en el hecho (DQ-014 es aviso, no cuarentena). Sin KPIs,
-- umbrales ni conversión de moneda (todo COP).

with incidents as (

    select * from {{ ref('stg_tms__carrier_incidents_valid') }}

),

shipments as (

    select * from {{ ref('fact_shipments') }}

),

carriers as (

    select
        carrier_id,
        md5(carrier_id) as carrier_key
    from {{ ref('dim_carrier') }}

),

joined as (

    select
        incidents.incident_id,
        incidents.shipment_id,
        incidents.carrier_id,
        carriers.carrier_key,
        incidents.incident_type,
        incidents.severity,
        incidents.damage_flag,
        incidents.claim_amount,
        incidents.currency_code,
        incidents.reported_ts,
        incidents.responded_ts,
        incidents.resolved_ts,
        incidents.run_id,
        incidents.source_system,
        incidents.source_file,
        incidents.source_file_hash,
        incidents.ingested_at
    from incidents
    inner join shipments
        on incidents.shipment_id = shipments.shipment_id
    left join carriers
        on incidents.carrier_id = carriers.carrier_id

),

measured as (

    select
        joined.*,
        extract(epoch from (joined.responded_ts - joined.reported_ts)) / 60 as response_minutes,
        extract(epoch from (joined.resolved_ts - joined.reported_ts)) / 60 as resolution_minutes
    from joined

)

select
    incident_id,
    shipment_id,
    carrier_id,
    carrier_key,
    incident_type,
    severity,
    damage_flag,
    claim_amount,
    currency_code,
    reported_ts,
    responded_ts,
    resolved_ts,
    response_minutes,
    resolution_minutes,
    run_id,
    source_system,
    source_file,
    source_file_hash,
    ingested_at
from measured

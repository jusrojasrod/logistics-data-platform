-- Hecho de ofertas de transportistas: una fila por oferta válida
-- (tender_id), con fecha de negocio offered_ts y sin partición ni versión.
-- El estado de respuesta y la tarifa aceptada son atributos de esa fila,
-- no eventos ni versiones. El envío se une de forma interna a fact_shipments:
-- una oferta huérfana queda fuera del hecho hasta que llegue su padre
-- (DQ-004 avisa en el staging _valid, sin rechazarla allí). El transportista
-- se une con left join a dim_carrier, de una sola versión, con
-- carrier_key = md5(carrier_id): la clave queda nula si el id no existe, sin
-- inferidos ni SCD2. Las ofertas sin respuesta conservan responded_ts,
-- response_status y accepted_rate_amount nulos (DQ-014 es aviso, no
-- cuarentena). Sin KPIs, umbrales ni conversión de moneda (todo COP).

with tenders as (

    select * from {{ ref('stg_tms__carrier_tenders_valid') }}

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
        tenders.tender_id,
        tenders.shipment_id,
        tenders.carrier_id,
        carriers.carrier_key,
        tenders.offered_ts,
        tenders.responded_ts,
        tenders.response_status,
        tenders.offered_rate_amount,
        tenders.accepted_rate_amount,
        tenders.currency_code,
        tenders.run_id,
        tenders.source_system,
        tenders.source_file,
        tenders.source_file_hash,
        tenders.ingested_at
    from tenders
    inner join shipments
        on tenders.shipment_id = shipments.shipment_id
    left join carriers
        on tenders.carrier_id = carriers.carrier_id

),

measured as (

    select
        joined.*,
        joined.response_status = 'accepted' as accepted_flag,
        extract(epoch from (joined.responded_ts - joined.offered_ts)) / 60 as response_minutes
    from joined

)

select
    tender_id,
    shipment_id,
    carrier_id,
    carrier_key,
    offered_ts,
    responded_ts,
    response_status,
    offered_rate_amount,
    accepted_rate_amount,
    currency_code,
    accepted_flag,
    response_minutes,
    run_id,
    source_system,
    source_file,
    source_file_hash,
    ingested_at
from measured

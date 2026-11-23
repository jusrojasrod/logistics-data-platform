-- DQ-014 (alerta): todo tender válido debe tener respuesta o marca de
-- expiración.
--
-- Se considera conforme una oferta con responded_ts (respuesta) o con
-- response_status = 'expired' (marca de expiración). Se emite una fila por
-- oferta válida sin ninguna de las dos. Las ofertas sin respuesta conservan
-- response_status y responded_ts nulos en el staging y en el hecho; con
-- severity warn el aviso no detiene la ejecución ni envía la oferta a
-- cuarentena.

{{ config(severity='warn') }}

select
    'DQ-014' as rule_id,
    tender_id,
    shipment_id,
    offered_ts,
    response_status,
    responded_ts
from {{ ref('stg_tms__carrier_tenders_valid') }}
where
    responded_ts is null
    and (response_status is null or response_status <> 'expired')

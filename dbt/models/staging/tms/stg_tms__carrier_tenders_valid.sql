-- Ofertas de transportistas válidas: filas del modelo validado sin reglas
-- incumplidas. Se conserva una fila por oferta con sus columnas de negocio,
-- los cinco metadatos y copy_rank para linaje. No se descartan filas por envío
-- huérfano ni por fecha de publicación: esas decisiones se toman aguas abajo.

with validated as (

    select * from {{ ref('stg_tms__carrier_tenders_validated') }}

)

select
    validated.tender_id,
    validated.shipment_id,
    validated.carrier_id,
    validated.offered_ts,
    validated.responded_ts,
    validated.response_status,
    validated.offered_rate_amount,
    validated.accepted_rate_amount,
    validated.currency_code,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid

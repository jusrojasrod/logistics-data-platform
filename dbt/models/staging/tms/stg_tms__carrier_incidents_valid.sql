-- Incidencias de transportistas válidas: filas del modelo validado sin reglas
-- incumplidas. Se conserva una fila por incidencia con sus columnas de negocio,
-- los cinco metadatos y copy_rank para linaje. No se descartan filas por envío
-- huérfano ni por fecha de publicación: esas decisiones se toman aguas abajo.

with validated as (

    select * from {{ ref('stg_tms__carrier_incidents_validated') }}

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
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid

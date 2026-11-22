-- Incidencias de transportista tipadas desde la vía API (raw.api_items,
-- dataset tms_carrier_incidents). Una fila por elemento de data[]; el payload
-- crudo se conserva íntegro. Los duplicados de la clave natural dentro de un
-- run_id se marcan con copy_rank para clasificarlos después. No se borra fila.
-- Los valores no parseables en campos tipados pasan a NULL; el payload y
-- has_drift_flag se conservan para que DQ-008 clasifique la fila después.

with source as (

    select * from {{ source('api', 'api_items') }}
    where dataset_name = 'tms_carrier_incidents'

),

typed as (

    -- Columnas simples primero y cálculos después; los valores no parseables
    -- pasan a NULL sin perder el payload ni la bandera de drift.
    select
        cast(payload ->> 'incident_id' as text) as incident_id,
        cast(payload ->> 'shipment_id' as text) as shipment_id,
        cast(payload ->> 'carrier_id' as text) as carrier_id,
        cast(payload ->> 'incident_type' as text) as incident_type,
        cast(payload ->> 'severity' as text) as severity,
        cast(payload ->> 'currency_code' as text) as currency_code,
        cast(run_id as text) as run_id,
        cast(source_system as text) as source_system,
        cast(endpoint as text) as endpoint,
        cast(page as integer) as page,
        cast(payload_sha256 as text) as payload_sha256,
        cast(ingested_at as timestamptz) as ingested_at,
        cast(dataset_name as text) as dataset_name,
        cast(source_key as text) as source_key,
        cast(item_index as integer) as item_index,
        payload,
        cast(has_drift_flag as boolean) as has_drift_flag,
        cast(drift_fields as jsonb) as drift_fields,
        cast(exposed_at as timestamptz) as exposed_at,
        case
            when pg_input_is_valid(payload ->> 'reported_ts', 'timestamptz')
                then cast(payload ->> 'reported_ts' as timestamptz)
        end as reported_ts,
        case
            when pg_input_is_valid(payload ->> 'responded_ts', 'timestamptz')
                then cast(payload ->> 'responded_ts' as timestamptz)
        end as responded_ts,
        case
            when pg_input_is_valid(payload ->> 'resolved_ts', 'timestamptz')
                then cast(payload ->> 'resolved_ts' as timestamptz)
        end as resolved_ts,
        case
            when pg_input_is_valid(payload ->> 'damage_flag', 'boolean')
                then cast(payload ->> 'damage_flag' as boolean)
        end as damage_flag,
        case
            when pg_input_is_valid(payload ->> 'claim_amount', 'numeric')
                then cast(payload ->> 'claim_amount' as numeric)
        end as claim_amount
    from source

),

ranked as (

    select
        typed.*,
        row_number() over (
            partition by typed.run_id, typed.dataset_name, typed.source_key
            order by typed.ingested_at, typed.item_index
        ) as copy_rank
    from typed

)

select * from ranked

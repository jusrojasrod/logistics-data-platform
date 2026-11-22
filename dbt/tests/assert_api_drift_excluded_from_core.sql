-- Exclusión del drift de core y trazabilidad en staging y cuarentena.
--
-- La bandera has_drift_flag de la exposición es la proyección consultable de
-- meta.api_schema_drift: ninguna de esas claves (run_id, endpoint, page,
-- item_index) debe llegar a los modelos core_api__*, y todas deben aparecer en
-- staging con la bandera y en cuarentena con DQ-008 (como regla principal o
-- entre las reglas incumplidas). Sin claves con drift el test devuelve cero
-- filas; la existencia de esas claves se comprueba antes con recuentos > 0.
-- Devuelve una fila por incumplimiento; sin incumplimientos, cero filas.

with drifted_exposed as (

    select distinct
        items.run_id,
        items.endpoint,
        items.page,
        items.item_index
    from {{ source('api', 'api_items') }} as items
    where items.has_drift_flag

),

flagged_staging as (

    select distinct
        shipments.run_id,
        shipments.endpoint,
        shipments.page,
        shipments.item_index
    from {{ ref('stg_api__shipments') }} as shipments
    where shipments.has_drift_flag

    union

    select distinct
        events.run_id,
        events.endpoint,
        events.page,
        events.item_index
    from {{ ref('stg_api__shipment_events') }} as events
    where events.has_drift_flag

    union

    select distinct
        incidents.run_id,
        incidents.endpoint,
        incidents.page,
        incidents.item_index
    from {{ ref('stg_api__carrier_incidents') }} as incidents
    where incidents.has_drift_flag

),

quarantine_drift as (

    select distinct
        quarantined.run_id,
        cast(quarantined.payload ->> 'endpoint' as text) as endpoint,
        cast(quarantined.payload ->> 'page' as integer) as page,
        cast(quarantined.payload ->> 'item_index' as integer) as item_index
    from {{ ref('api_items') }} as quarantined
    where
        quarantined.rule_id = 'DQ-008'
        or (quarantined.payload -> 'dq_errors') ? 'DQ-008'

),

core_keys as (

    select distinct
        shipments.run_id,
        shipments.endpoint,
        shipments.page,
        shipments.item_index
    from {{ ref('core_api__shipments') }} as shipments

    union

    select distinct
        events.run_id,
        events.endpoint,
        events.page,
        events.item_index
    from {{ ref('core_api__shipment_events') }} as events

    union

    select distinct
        incidents.run_id,
        incidents.endpoint,
        incidents.page,
        incidents.item_index
    from {{ ref('core_api__carrier_incidents') }} as incidents

),

drift_in_core as (

    select
        'drift_key_in_core' as failure_type,
        core_keys.run_id,
        core_keys.endpoint,
        core_keys.page,
        core_keys.item_index
    from core_keys
    inner join drifted_exposed
        on
            core_keys.run_id = drifted_exposed.run_id
            and core_keys.endpoint = drifted_exposed.endpoint
            and core_keys.page = drifted_exposed.page
            and core_keys.item_index = drifted_exposed.item_index

),

missing_in_staging as (

    select
        'drift_key_not_flagged_in_staging' as failure_type,
        drifted_exposed.run_id,
        drifted_exposed.endpoint,
        drifted_exposed.page,
        drifted_exposed.item_index
    from drifted_exposed
    where not exists (
        select 1
        from flagged_staging
        where
            flagged_staging.run_id = drifted_exposed.run_id
            and flagged_staging.endpoint = drifted_exposed.endpoint
            and flagged_staging.page = drifted_exposed.page
            and flagged_staging.item_index = drifted_exposed.item_index
    )

),

missing_in_quarantine as (

    select
        'drift_key_without_dq008' as failure_type,
        drifted_exposed.run_id,
        drifted_exposed.endpoint,
        drifted_exposed.page,
        drifted_exposed.item_index
    from drifted_exposed
    where not exists (
        select 1
        from quarantine_drift
        where
            quarantine_drift.run_id = drifted_exposed.run_id
            and quarantine_drift.endpoint = drifted_exposed.endpoint
            and quarantine_drift.page = drifted_exposed.page
            and quarantine_drift.item_index = drifted_exposed.item_index
    )

)

select
    failure_type,
    run_id,
    endpoint,
    page,
    item_index
from drift_in_core

union all

select
    failure_type,
    run_id,
    endpoint,
    page,
    item_index
from missing_in_staging

union all

select
    failure_type,
    run_id,
    endpoint,
    page,
    item_index
from missing_in_quarantine

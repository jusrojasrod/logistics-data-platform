-- Partición exacta de raw.carrier_incidents y raw.carrier_tenders entre filas
-- válidas de staging y cuarentena de desempeño de transportistas.
--
-- Por entidad, ejecución (run_id) y clave natural (incident_id o tender_id):
--   las claves de raw = claves válidas ∪ claves en cuarentena, sin solape y sin
--   claves huérfanas. Las claves se comparan como conjuntos distinct para que
--   las copias repetidas por clave natural colapsen en una sola clave: la
--   deduplicación la resuelve copy_rank en staging, sin una regla DQ nueva.
--   El lado raw se lee del modelo base tipado, vista 1:1 de raw sin filtros.
--   La cuarentena puede estar vacía con el perfil demo (todo COP): no se exigen
--   rechazos, solo la identidad sobre las claves existentes y que ninguna
--   entidad esté vacía, para que el test no pase en vacío.
-- Devuelve una fila por incumplimiento; sin incumplimientos, cero filas.

with raw_keys as (

    select distinct
        'carrier_incidents' as entity,
        incidents.run_id,
        incidents.incident_id as natural_key
    from {{ ref('stg_tms__carrier_incidents') }} as incidents

    union all

    select distinct
        'carrier_tenders' as entity,
        tenders.run_id,
        tenders.tender_id as natural_key
    from {{ ref('stg_tms__carrier_tenders') }} as tenders

),

valid_keys as (

    select distinct
        'carrier_incidents' as entity,
        incidents.run_id,
        incidents.incident_id as natural_key
    from {{ ref('stg_tms__carrier_incidents_valid') }} as incidents

    union all

    select distinct
        'carrier_tenders' as entity,
        tenders.run_id,
        tenders.tender_id as natural_key
    from {{ ref('stg_tms__carrier_tenders_valid') }} as tenders

),

quarantine_keys as (

    select distinct
        quarantined.run_id,
        replace(quarantined.source_table, 'raw.', '') as entity,
        quarantined.payload ->> 'natural_key' as natural_key
    from {{ ref('carrier_performance_items') }} as quarantined
    where quarantined.source_table in (
        'raw.carrier_incidents',
        'raw.carrier_tenders'
    )

),

keyed_rows as (

    select
        raw_keys.entity,
        raw_keys.run_id,
        raw_keys.natural_key,
        'raw' as set_name
    from raw_keys

    union all

    select
        valid_keys.entity,
        valid_keys.run_id,
        valid_keys.natural_key,
        'valid' as set_name
    from valid_keys

    union all

    select
        quarantine_keys.entity,
        quarantine_keys.run_id,
        quarantine_keys.natural_key,
        'quarantine' as set_name
    from quarantine_keys

),

key_flags as (

    select
        keyed_rows.entity,
        keyed_rows.run_id,
        keyed_rows.natural_key,
        max(case when keyed_rows.set_name = 'raw' then 1 else 0 end) as in_raw,
        max(case when keyed_rows.set_name = 'valid' then 1 else 0 end) as in_valid,
        max(
            case when keyed_rows.set_name = 'quarantine' then 1 else 0 end
        ) as in_quarantine
    from keyed_rows
    group by keyed_rows.entity, keyed_rows.run_id, keyed_rows.natural_key

),

raw_entity_counts as (

    select
        raw_keys.entity,
        count(*) as raw_count
    from raw_keys
    group by raw_keys.entity

),

expected_entities as (

    select entity
    from (
        values
        ('carrier_incidents'),
        ('carrier_tenders')
    ) as expected (entity)

),

empty_failures as (

    select
        'empty_entity' as failure_type,
        expected_entities.entity,
        null::text as run_id,
        null::text as natural_key
    from expected_entities
    left join raw_entity_counts
        on expected_entities.entity = raw_entity_counts.entity
    where coalesce(raw_entity_counts.raw_count, 0) = 0

),

key_failures as (

    select
        key_flags.entity,
        key_flags.run_id,
        key_flags.natural_key,
        case
            when key_flags.in_raw = 0 and key_flags.in_valid = 1
                then 'valid_key_absent_from_raw'
            when key_flags.in_raw = 0 and key_flags.in_quarantine = 1
                then 'quarantine_key_absent_from_raw'
            when key_flags.in_valid = 1 and key_flags.in_quarantine = 1
                then 'valid_and_quarantine_overlap'
            else 'raw_key_unclassified'
        end as failure_type
    from key_flags
    where
        key_flags.in_raw = 0
        or (key_flags.in_valid = 1 and key_flags.in_quarantine = 1)
        or (
            key_flags.in_raw = 1
            and key_flags.in_valid = 0
            and key_flags.in_quarantine = 0
        )

)

select
    key_failures.failure_type,
    key_failures.entity,
    key_failures.run_id,
    key_failures.natural_key
from key_failures
union all
select
    empty_failures.failure_type,
    empty_failures.entity,
    empty_failures.run_id,
    empty_failures.natural_key
from empty_failures

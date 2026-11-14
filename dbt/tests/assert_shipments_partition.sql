-- Partición exacta de raw.shipments entre envíos válidos y cuarentena.
--
-- Por run_id y por shipment_id, contando multiplicidad:
--   raw_count = valid_count + quarantine_count y valid_count <= 1.
-- El cruce completo también detecta filas válidas o en cuarentena cuyo
-- run_id o shipment_id no existe en raw. Además, ningún rechazo con
-- rule_id distinto de DQ-001 puede tener una copia válida del mismo envío.
-- En cuarentena el shipment_id se lee de payload->>'shipment_id'.
-- Devuelve una fila por incumplimiento; sin incumplimientos, cero filas.

with raw_counts as (

    select
        raw_shipments.run_id,
        raw_shipments.shipment_id,
        count(*) as raw_count
    from {{ source('tms', 'shipments') }} as raw_shipments
    group by raw_shipments.run_id, raw_shipments.shipment_id

),

valid_counts as (

    select
        valid_shipments.run_id,
        valid_shipments.shipment_id,
        count(*) as valid_count
    from {{ ref('stg_tms__shipments_valid') }} as valid_shipments
    group by valid_shipments.run_id, valid_shipments.shipment_id

),

quarantine_rows as (

    select
        quarantined.run_id,
        quarantined.rule_id,
        quarantined.payload ->> 'shipment_id' as shipment_id
    from {{ ref('shipments') }} as quarantined

),

quarantine_counts as (

    select
        quarantine_rows.run_id,
        quarantine_rows.shipment_id,
        count(*) as quarantine_count
    from quarantine_rows
    group by quarantine_rows.run_id, quarantine_rows.shipment_id

),

key_counts as (

    select
        coalesce(
            raw_counts.run_id, valid_counts.run_id, quarantine_counts.run_id
        ) as run_id,
        coalesce(
            raw_counts.shipment_id,
            valid_counts.shipment_id,
            quarantine_counts.shipment_id
        ) as shipment_id,
        coalesce(raw_counts.raw_count, 0) as raw_count,
        coalesce(valid_counts.valid_count, 0) as valid_count,
        coalesce(quarantine_counts.quarantine_count, 0) as quarantine_count
    from raw_counts
    full outer join valid_counts
        on
            raw_counts.run_id = valid_counts.run_id
            and raw_counts.shipment_id = valid_counts.shipment_id
    full outer join quarantine_counts
        on
            coalesce(raw_counts.run_id, valid_counts.run_id)
            = quarantine_counts.run_id
            and coalesce(raw_counts.shipment_id, valid_counts.shipment_id)
            = quarantine_counts.shipment_id

),

run_counts as (

    select
        key_counts.run_id,
        sum(key_counts.raw_count) as raw_count,
        sum(key_counts.valid_count) as valid_count,
        sum(key_counts.quarantine_count) as quarantine_count
    from key_counts
    group by key_counts.run_id

),

run_failures as (

    select
        run_counts.run_id,
        run_counts.raw_count,
        run_counts.valid_count,
        run_counts.quarantine_count,
        null::text as shipment_id,
        null::text as rule_id,
        'run_count_mismatch' as failure_type
    from run_counts
    where
        run_counts.raw_count
        <> run_counts.valid_count + run_counts.quarantine_count

),

key_failures as (

    select
        key_counts.run_id,
        key_counts.shipment_id,
        key_counts.raw_count,
        key_counts.valid_count,
        key_counts.quarantine_count,
        null::text as rule_id,
        case
            when key_counts.valid_count > 1 then 'multiple_valid_copies'
            else 'key_count_mismatch'
        end as failure_type
    from key_counts
    where
        key_counts.raw_count
        <> key_counts.valid_count + key_counts.quarantine_count
        or key_counts.valid_count > 1

),

overlap_failures as (

    select
        quarantine_rows.run_id,
        quarantine_rows.shipment_id,
        quarantine_rows.rule_id,
        valid_counts.valid_count,
        null::bigint as raw_count,
        null::bigint as quarantine_count,
        'rejected_with_valid_copy' as failure_type
    from quarantine_rows
    inner join valid_counts
        on
            quarantine_rows.run_id = valid_counts.run_id
            and quarantine_rows.shipment_id = valid_counts.shipment_id
    where quarantine_rows.rule_id <> 'DQ-001'

)

select
    failure_type,
    run_id,
    shipment_id,
    rule_id,
    raw_count,
    valid_count,
    quarantine_count
from run_failures
union all
select
    failure_type,
    run_id,
    shipment_id,
    rule_id,
    raw_count,
    valid_count,
    quarantine_count
from key_failures
union all
select
    failure_type,
    run_id,
    shipment_id,
    rule_id,
    raw_count,
    valid_count,
    quarantine_count
from overlap_failures

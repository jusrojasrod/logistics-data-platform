-- Partición exacta de las seis entidades financieras ERP entre filas válidas
-- de staging y cuarentena financiera.
--
-- Por entidad, ejecución (run_id) y clave natural:
--   raw_count = valid_count + quarantine_count, sin copias válidas duplicadas
--   y sin claves en válidos o cuarentena ausentes de raw.
-- El lado raw se lee del modelo base tipado, vista 1:1 de raw sin filtros,
-- para construir la clave natural con el mismo tipado que válidos y cuarentena.
-- La cuarentena puede estar vacía con el perfil demo: no se exigen rechazos
-- ERP, solo que la identidad se cumpla sobre los datos existentes y que
-- ninguna entidad esté vacía, para que el test no pase en vacío.
-- Devuelve una fila por incumplimiento; sin incumplimientos, cero filas.

with raw_keys as (

    select
        'tariffs' as entity,
        tariffs.run_id,
        concat_ws('|', tariffs.tariff_id, tariffs.valid_from) as natural_key
    from {{ ref('stg_erp__tariffs') }} as tariffs

    union all

    select
        'invoices' as entity,
        invoices.run_id,
        invoices.invoice_id as natural_key
    from {{ ref('stg_erp__invoices') }} as invoices

    union all

    select
        'invoice_lines' as entity,
        invoice_lines.run_id,
        invoice_lines.invoice_line_id as natural_key
    from {{ ref('stg_erp__invoice_lines') }} as invoice_lines

    union all

    select
        'payments' as entity,
        payments.run_id,
        payments.payment_id as natural_key
    from {{ ref('stg_erp__payments') }} as payments

    union all

    select
        'carrier_settlements' as entity,
        settlements.run_id,
        settlements.settlement_line_id as natural_key
    from {{ ref('stg_erp__carrier_settlements') }} as settlements

    union all

    select
        'shipment_costs' as entity,
        ship_costs.run_id,
        ship_costs.shipment_cost_id as natural_key
    from {{ ref('stg_erp__shipment_costs') }} as ship_costs

),

valid_keys as (

    select
        'tariffs' as entity,
        tariffs.run_id,
        concat_ws('|', tariffs.tariff_id, tariffs.valid_from) as natural_key
    from {{ ref('stg_erp__tariffs_valid') }} as tariffs

    union all

    select
        'invoices' as entity,
        invoices.run_id,
        invoices.invoice_id as natural_key
    from {{ ref('stg_erp__invoices_valid') }} as invoices

    union all

    select
        'invoice_lines' as entity,
        invoice_lines.run_id,
        invoice_lines.invoice_line_id as natural_key
    from {{ ref('stg_erp__invoice_lines_valid') }} as invoice_lines

    union all

    select
        'payments' as entity,
        payments.run_id,
        payments.payment_id as natural_key
    from {{ ref('stg_erp__payments_valid') }} as payments

    union all

    select
        'carrier_settlements' as entity,
        settlements.run_id,
        settlements.settlement_line_id as natural_key
    from {{ ref('stg_erp__carrier_settlements_valid') }} as settlements

    union all

    select
        'shipment_costs' as entity,
        ship_costs.run_id,
        ship_costs.shipment_cost_id as natural_key
    from {{ ref('stg_erp__shipment_costs_valid') }} as ship_costs

),

quarantine_keys as (

    select
        quarantined.run_id,
        replace(quarantined.source_table, 'raw.', '') as entity,
        quarantined.payload ->> 'natural_key' as natural_key
    from {{ ref('financial_items') }} as quarantined
    where quarantined.source_table in (
        'raw.tariffs',
        'raw.invoices',
        'raw.invoice_lines',
        'raw.payments',
        'raw.carrier_settlements',
        'raw.shipment_costs'
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

key_counts as (

    select
        keyed_rows.entity,
        keyed_rows.run_id,
        keyed_rows.natural_key,
        sum(case when keyed_rows.set_name = 'raw' then 1 else 0 end) as raw_count,
        sum(case when keyed_rows.set_name = 'valid' then 1 else 0 end) as valid_count,
        sum(
            case when keyed_rows.set_name = 'quarantine' then 1 else 0 end
        ) as quarantine_count
    from keyed_rows
    group by keyed_rows.entity, keyed_rows.run_id, keyed_rows.natural_key

),

raw_entity_totals as (

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
        ('tariffs'),
        ('invoices'),
        ('invoice_lines'),
        ('payments'),
        ('carrier_settlements'),
        ('shipment_costs')
    ) as expected (entity)

),

empty_failures as (

    select
        'empty_entity' as failure_type,
        expected_entities.entity,
        null::text as run_id,
        null::text as natural_key,
        0::bigint as raw_count,
        0::bigint as valid_count,
        0::bigint as quarantine_count
    from expected_entities
    left join raw_entity_totals
        on expected_entities.entity = raw_entity_totals.entity
    where coalesce(raw_entity_totals.raw_count, 0) = 0

),

key_failures as (

    select
        key_counts.entity,
        key_counts.run_id,
        key_counts.natural_key,
        key_counts.raw_count,
        key_counts.valid_count,
        key_counts.quarantine_count,
        case
            when key_counts.valid_count > 1 then 'multiple_valid_copies'
            else 'partition_mismatch'
        end as failure_type
    from key_counts
    where
        key_counts.raw_count
        <> key_counts.valid_count + key_counts.quarantine_count
        or key_counts.valid_count > 1

)

select
    key_failures.failure_type,
    key_failures.entity,
    key_failures.run_id,
    key_failures.natural_key,
    key_failures.raw_count,
    key_failures.valid_count,
    key_failures.quarantine_count
from key_failures
union all
select
    empty_failures.failure_type,
    empty_failures.entity,
    empty_failures.run_id,
    empty_failures.natural_key,
    empty_failures.raw_count,
    empty_failures.valid_count,
    empty_failures.quarantine_count
from empty_failures

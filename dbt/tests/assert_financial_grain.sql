-- Grano y población de los tres hechos financieros y la dimensión de tarifa.
--
-- Cada modelo debe tener la clave de grano única y al menos una fila: si
-- alguna tabla está vacía se emite una fila de fallo, para que el test no pase
-- en vacío. Las claves comprobadas son invoice_line_id, settlement_line_id,
-- shipment_cost_id y tariff_key son las claves de grano de los hechos y la dimensión.
-- Devuelve una fila por clave repetida o tabla vacía; sin incumplimientos,
-- cero filas.

with grain_keys as (

    select
        'fact_invoices' as model_name,
        invoices.invoice_line_id as grain_key
    from {{ ref('fact_invoices') }} as invoices

    union all

    select
        'fact_carrier_settlements' as model_name,
        settlements.settlement_line_id as grain_key
    from {{ ref('fact_carrier_settlements') }} as settlements

    union all

    select
        'fact_shipment_costs' as model_name,
        ship_costs.shipment_cost_id as grain_key
    from {{ ref('fact_shipment_costs') }} as ship_costs

    union all

    select
        'dim_tariff' as model_name,
        tariffs.tariff_key as grain_key
    from {{ ref('dim_tariff') }} as tariffs

),

duplicates as (

    select
        grain_keys.model_name,
        grain_keys.grain_key,
        count(*) as row_count
    from grain_keys
    group by grain_keys.model_name, grain_keys.grain_key
    having count(*) > 1

),

model_counts as (

    select
        grain_keys.model_name,
        count(*) as row_count
    from grain_keys
    group by grain_keys.model_name

),

expected_models as (

    select model_name
    from (
        values
        ('fact_invoices'),
        ('fact_carrier_settlements'),
        ('fact_shipment_costs'),
        ('dim_tariff')
    ) as expected (model_name)

),

empty_failures as (

    select
        'empty_model' as failure_type,
        expected_models.model_name,
        null::text as grain_key,
        0::bigint as row_count
    from expected_models
    left join model_counts on expected_models.model_name = model_counts.model_name
    where coalesce(model_counts.row_count, 0) = 0

),

duplicate_failures as (

    select
        'duplicate_grain' as failure_type,
        duplicates.model_name,
        duplicates.grain_key,
        duplicates.row_count
    from duplicates

)

select
    duplicate_failures.failure_type,
    duplicate_failures.model_name,
    duplicate_failures.grain_key,
    duplicate_failures.row_count
from duplicate_failures
union all
select
    empty_failures.failure_type,
    empty_failures.model_name,
    empty_failures.grain_key,
    empty_failures.row_count
from empty_failures

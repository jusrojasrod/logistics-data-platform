-- Grano y población de los hechos de incidencias y ofertas de transportistas.
--
-- Cada hecho debe tener la clave de grano única y no nula: incident_id en
-- fact_carrier_incidents y tender_id en fact_carrier_tenders. Además, cada
-- tabla debe tener al menos una fila: si alguna está vacía se emite una fila
-- de fallo, para que el test no pase en vacío.
-- Devuelve una fila por clave repetida, clave nula o tabla vacía; sin
-- incumplimientos, cero filas.

with grain_keys as (

    select
        'fact_carrier_incidents' as model_name,
        incidents.incident_id as grain_key
    from {{ ref('fact_carrier_incidents') }} as incidents

    union all

    select
        'fact_carrier_tenders' as model_name,
        tenders.tender_id as grain_key
    from {{ ref('fact_carrier_tenders') }} as tenders

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

null_keys as (

    select
        grain_keys.model_name,
        grain_keys.grain_key,
        count(*) as row_count
    from grain_keys
    where grain_keys.grain_key is null
    group by grain_keys.model_name, grain_keys.grain_key

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
        ('fact_carrier_incidents'),
        ('fact_carrier_tenders')
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

),

null_failures as (

    select
        'null_grain' as failure_type,
        null_keys.model_name,
        null_keys.grain_key,
        null_keys.row_count
    from null_keys

)

select
    duplicate_failures.failure_type,
    duplicate_failures.model_name,
    duplicate_failures.grain_key,
    duplicate_failures.row_count
from duplicate_failures
union all
select
    null_failures.failure_type,
    null_failures.model_name,
    null_failures.grain_key,
    null_failures.row_count
from null_failures
union all
select
    empty_failures.failure_type,
    empty_failures.model_name,
    empty_failures.grain_key,
    empty_failures.row_count
from empty_failures

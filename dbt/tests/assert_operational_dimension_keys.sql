-- Claves sustitutas y columnas de versión de las dimensiones operativas TMS.
--
-- Cada dimensión debe tener la clave sustituta única y no nula: vehicle_key en
-- dim_vehicle y driver_key en dim_driver. También deben estar presentes las
-- cinco columnas de versión e inferido (valid_from, valid_to, is_current,
-- version e is_inferred) y cada tabla debe tener al menos una fila: si falta
-- alguna columna o una dimensión está vacía se emite una fila de fallo, para
-- que el test no pase en vacío.
-- Devuelve una fila por clave repetida, clave nula, columna ausente o tabla
-- vacía; sin incumplimientos, cero filas.

with dimension_keys as (

    select
        'dim_vehicle' as model_name,
        vehicles.vehicle_key as dimension_key
    from {{ ref('dim_vehicle') }} as vehicles

    union all

    select
        'dim_driver' as model_name,
        drivers.driver_key as dimension_key
    from {{ ref('dim_driver') }} as drivers

),

duplicates as (

    select
        dimension_keys.model_name,
        dimension_keys.dimension_key,
        count(*) as row_count
    from dimension_keys
    group by dimension_keys.model_name, dimension_keys.dimension_key
    having count(*) > 1

),

null_keys as (

    select
        dimension_keys.model_name,
        dimension_keys.dimension_key,
        count(*) as row_count
    from dimension_keys
    where dimension_keys.dimension_key is null
    group by dimension_keys.model_name, dimension_keys.dimension_key

),

model_counts as (

    select
        dimension_keys.model_name,
        count(*) as row_count
    from dimension_keys
    group by dimension_keys.model_name

),

expected_models as (

    select model_name
    from (
        values
        ('dim_vehicle'),
        ('dim_driver')
    ) as expected (model_name)

),

empty_failures as (

    select
        'empty_model' as failure_type,
        expected_models.model_name,
        null::text as dimension_key,
        0::bigint as row_count
    from expected_models
    left join model_counts on expected_models.model_name = model_counts.model_name
    where coalesce(model_counts.row_count, 0) = 0

),

version_columns as (

    select
        cols.table_name as model_name,
        count(distinct cols.column_name) as column_count
    from information_schema.columns as cols
    where
        cols.table_schema = 'core'
        and cols.table_name in ('dim_vehicle', 'dim_driver')
        and cols.column_name in (
            'valid_from',
            'valid_to',
            'is_current',
            'version',
            'is_inferred'
        )
    group by cols.table_name

),

missing_columns as (

    select
        'missing_version_columns' as failure_type,
        expected_models.model_name,
        null::text as dimension_key,
        coalesce(version_columns.column_count, 0)::bigint as row_count
    from expected_models
    left join version_columns
        on expected_models.model_name = version_columns.model_name
    where coalesce(version_columns.column_count, 0) <> 5

),

duplicate_failures as (

    select
        'duplicate_key' as failure_type,
        duplicates.model_name,
        duplicates.dimension_key,
        duplicates.row_count
    from duplicates

),

null_failures as (

    select
        'null_key' as failure_type,
        null_keys.model_name,
        null_keys.dimension_key,
        null_keys.row_count
    from null_keys

)

select
    duplicate_failures.failure_type,
    duplicate_failures.model_name,
    duplicate_failures.dimension_key,
    duplicate_failures.row_count
from duplicate_failures
union all
select
    null_failures.failure_type,
    null_failures.model_name,
    null_failures.dimension_key,
    null_failures.row_count
from null_failures
union all
select
    empty_failures.failure_type,
    empty_failures.model_name,
    empty_failures.dimension_key,
    empty_failures.row_count
from empty_failures
union all
select
    missing_columns.failure_type,
    missing_columns.model_name,
    missing_columns.dimension_key,
    missing_columns.row_count
from missing_columns

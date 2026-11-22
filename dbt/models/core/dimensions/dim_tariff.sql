-- Dimensión de tarifas con historia (SCD2): una fila por versión de tarifa.
-- La clave sustituta tariff_key identifica la versión por (tariff_id,
-- valid_from). valid_to es exclusivo; la versión vigente usa el centinela
-- 9999-12-31 porque raw deja valid_to nulo en esa versión. is_current solo
-- describe la versión: la atribución histórica se resuelve por vigencia,
-- nunca con is_current.

with source as (

    select * from {{ ref('stg_erp__tariffs_valid') }}

),

versioned as (

    select
        source.tariff_id,
        source.customer_id,
        source.route_id,
        source.service_type_id,
        source.base_amount,
        source.per_km_amount,
        source.fuel_surcharge_pct,
        source.currency_code,
        source.valid_from,
        source.run_id,
        source.source_system,
        source.source_file,
        source.source_file_hash,
        source.ingested_at,
        source.copy_rank,
        coalesce(source.valid_to, date '9999-12-31') as valid_to,
        (source.valid_to is null) as is_current,
        md5(source.tariff_id || '|' || source.valid_from) as tariff_key
    from source

)

select * from versioned

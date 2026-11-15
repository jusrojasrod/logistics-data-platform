-- Dimensión de transportistas de una sola versión: una fila por carrier_id con
-- clave sustituta estable md5(carrier_id). Sin historia (sin SCD2).

with source as (

    select * from {{ ref('stg_tms__carriers') }}

),

keyed as (

    select
        source.carrier_id,
        source.carrier_name,
        source.carrier_type,
        source.service_level,
        source.rate_index,
        md5(source.carrier_id) as carrier_key
    from source

)

select
    carrier_key,
    carrier_id,
    carrier_name,
    carrier_type,
    service_level,
    rate_index
from keyed

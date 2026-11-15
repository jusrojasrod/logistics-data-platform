-- Dimensión de tipos de servicio de una sola versión: una fila por
-- service_type_id con clave sustituta estable md5(service_type_id). Sin
-- historia (sin SCD2).

with source as (

    select * from {{ ref('stg_tms__service_types') }}

),

keyed as (

    select
        source.service_type_id,
        source.service_type_name,
        source.sla_hours,
        md5(source.service_type_id) as service_type_key
    from source

)

select
    service_type_key,
    service_type_id,
    service_type_name,
    sla_hours
from keyed

-- DQ-015 (incidente): antigüedad de los miembros inferidos en las dos
-- dimensiones operativas nuevas.
--
-- Un inferido creado hace más de siete días sin que haya llegado su catálogo
-- real deja atributos 'UNKNOWN' colgando en la dimensión. Se emite una fila por
-- miembro inferido antiguo con su clave natural, su vigencia y su antigüedad.
-- No se exige que existan inferidos: si no los hay, o si todos son recientes,
-- devuelve cero filas. La severidad de incidente se declara explícitamente.

{{ config(severity='error') }}

with inferred_members as (

    select
        'DQ-015' as rule_id,
        'vehicle' as dimension_name,
        vehicles.vehicle_id as natural_id,
        vehicles.vehicle_key as dimension_key,
        vehicles.valid_from,
        now() - vehicles.valid_from as age
    from {{ ref('dim_vehicle') }} as vehicles
    where vehicles.is_inferred

    union all

    select
        'DQ-015' as rule_id,
        'driver' as dimension_name,
        drivers.driver_id as natural_id,
        drivers.driver_key as dimension_key,
        drivers.valid_from,
        now() - drivers.valid_from as age
    from {{ ref('dim_driver') }} as drivers
    where drivers.is_inferred

)

select
    inferred_members.rule_id,
    inferred_members.dimension_name,
    inferred_members.natural_id,
    inferred_members.dimension_key,
    inferred_members.valid_from,
    inferred_members.age
from inferred_members
where inferred_members.valid_from < now() - interval '7 days'

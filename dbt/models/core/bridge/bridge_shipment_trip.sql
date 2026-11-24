-- Puente envío–viaje: una fila por tramo de envío (shipment_id, trip_id,
-- leg_sequence) leído de stg_tms__shipment_legs_valid. loaded_km, weight_kg y
-- volume_m3 se conservan de la fuente para no alterar la reconciliación.
-- El reparto de §5.4 prorratea el empty_km de fact_trips con el mismo criterio:
-- un envío = 100 %, peso × loaded_km, volumen × loaded_km o igualitario. El
-- método queda en allocation_method; el viaje sin envíos no genera filas y su
-- vacío permanece como deadhead no asignado en fact_trips. Sin montos: el costo
-- ya vive en fact_shipment_costs y no se duplica en este puente.

with legs as (

    select * from {{ ref('stg_tms__shipment_legs_valid') }}

),

trips as (

    select
        trip_id,
        empty_km
    from {{ ref('fact_trips') }}

),

trip_totals as (

    select
        trip_id,
        count(distinct shipment_id) as shipment_count,
        sum(coalesce(weight_kg, 0)) as trip_weight_kg,
        sum(coalesce(volume_m3, 0)) as trip_volume_m3
    from legs
    group by trip_id

),

joined as (

    select
        legs.shipment_id,
        legs.trip_id,
        legs.leg_sequence,
        legs.loaded_km,
        legs.weight_kg,
        legs.volume_m3,
        trips.empty_km,
        trip_totals.shipment_count,
        trip_totals.trip_weight_kg,
        trip_totals.trip_volume_m3,
        legs.run_id,
        legs.source_system,
        legs.source_file,
        legs.source_file_hash,
        legs.ingested_at
    from legs
    inner join trips
        on legs.trip_id = trips.trip_id
    inner join trip_totals
        on legs.trip_id = trip_totals.trip_id

),

method as (

    select
        joined.*,
        case
            when joined.shipment_count = 1 then 'single_shipment'
            when joined.trip_weight_kg > 0 then 'weight_x_loaded_km'
            when joined.trip_volume_m3 > 0 then 'volume_x_loaded_km'
            else 'equal'
        end as allocation_method
    from joined

),

basis_inputs as (

    select
        method.*,
        coalesce(method.weight_kg, 0) * coalesce(method.loaded_km, 0) as weight_basis,
        coalesce(method.volume_m3, 0) * coalesce(method.loaded_km, 0) as volume_basis
    from method

),

basis as (

    select
        inp.*,
        case
            when inp.allocation_method = 'single_shipment' then 1
            when inp.allocation_method = 'weight_x_loaded_km' then inp.weight_basis
            when inp.allocation_method = 'volume_x_loaded_km' then inp.volume_basis
            else 1
        end as allocation_basis
    from basis_inputs as inp

),

weighted as (

    select
        basis.*,
        sum(basis.allocation_basis) over (partition by basis.trip_id) as trip_basis
    from basis

)

select
    shipment_id,
    trip_id,
    leg_sequence,
    loaded_km,
    weight_kg,
    volume_m3,
    allocation_method,
    run_id,
    source_system,
    source_file,
    source_file_hash,
    ingested_at,
    case
        when trip_basis <> 0 then coalesce(empty_km, 0) * allocation_basis / trip_basis
        else 0
    end as empty_km_allocated
from weighted

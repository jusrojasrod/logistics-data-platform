-- Hecho de envíos: una fila por envío válido (shipment_id), con fecha de
-- negocio pickup_date y sin partición. Guarda los ids naturales y las claves
-- sustitutas de las cuatro dimensiones; una clave queda nula cuando el id
-- natural llega nulo (alerta DQ-004/DQ-005, sin miembro inferido).
-- Sin columnas financieras.

with shipments as (

    select * from {{ ref('stg_tms__shipments_valid') }}

),

customers as (

    select * from {{ ref('dim_customer') }}

),

carriers as (

    select * from {{ ref('dim_carrier') }}

),

routes as (

    select * from {{ ref('dim_route') }}

),

service_types as (

    select * from {{ ref('dim_service_type') }}

),

joined as (

    select
        shipments.shipment_id,
        customers.customer_key,
        carriers.carrier_key,
        routes.route_key,
        service_types.service_type_key,
        shipments.customer_id,
        shipments.carrier_id,
        shipments.route_id,
        shipments.service_type_id,
        shipments.pickup_date,
        shipments.pickup_ts,
        shipments.promised_delivery_ts,
        shipments.picked_up_at,
        shipments.delivered_at,
        shipments.ordered_qty,
        shipments.delivered_qty,
        shipments.ordered_weight_kg,
        shipments.delivered_weight_kg,
        shipments.definitive_failure_flag
    from shipments
    left join customers
        on shipments.customer_id = customers.customer_id
    left join carriers
        on shipments.carrier_id = carriers.carrier_id
    left join routes
        on shipments.route_id = routes.route_id
    left join service_types
        on shipments.service_type_id = service_types.service_type_id

),

flagged as (

    select
        joined.*,
        -- Cerrado: entregado o con fallo definitivo; nunca nulo.
        (
            joined.delivered_at is not null
            or joined.definitive_failure_flag
        ) as closed_flag,
        -- A tiempo: una entrega anticipada cuenta como a tiempo; nulo si falta
        -- la entrega o la promesa.
        (joined.delivered_at <= joined.promised_delivery_ts) as on_time_flag,
        -- Completo: piezas y peso entregados cubren lo declarado.
        (
            joined.delivered_qty >= joined.ordered_qty
            and joined.delivered_weight_kg >= joined.ordered_weight_kg
        ) as in_full_flag
    from joined

),

final as (

    select
        flagged.*,
        (flagged.on_time_flag and flagged.in_full_flag) as otif_flag
    from flagged

)

select
    shipment_id,
    customer_key,
    carrier_key,
    route_key,
    service_type_key,
    customer_id,
    carrier_id,
    route_id,
    service_type_id,
    pickup_date,
    pickup_ts,
    promised_delivery_ts,
    picked_up_at,
    delivered_at,
    ordered_qty,
    delivered_qty,
    ordered_weight_kg,
    delivered_weight_kg,
    closed_flag,
    on_time_flag,
    in_full_flag,
    otif_flag
from final

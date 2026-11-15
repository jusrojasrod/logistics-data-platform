-- Calendario diario de una sola versión. Las cotas salen de las fechas de
-- recogida de los envíos válidos y la serie se genera con generate_series de
-- Postgres. Si no hay envíos válidos las cotas son NULL y la tabla queda vacía.

with valid_shipments as (

    select * from {{ ref('stg_tms__shipments_valid') }}

),

bounds as (

    select
        min(valid_shipments.pickup_date) as min_date,
        max(valid_shipments.pickup_date) as max_date
    from valid_shipments

),

calendar as (

    select
        cast(
            generate_series(bounds.min_date, bounds.max_date, interval '1 day')
            as date
        ) as date_key
    from bounds

),

attributes as (

    select
        calendar.date_key,
        cast(extract(year from calendar.date_key) as integer) as year_number,
        cast(extract(quarter from calendar.date_key) as integer) as quarter_number,
        cast(extract(month from calendar.date_key) as integer) as month_number,
        cast(extract(isoyear from calendar.date_key) as integer) as iso_year_number,
        cast(extract(week from calendar.date_key) as integer) as iso_week_number,
        cast(extract(isodow from calendar.date_key) as integer) as day_of_week_number
    from calendar

)

select
    date_key,
    year_number,
    quarter_number,
    month_number,
    iso_year_number,
    iso_week_number,
    day_of_week_number
from attributes

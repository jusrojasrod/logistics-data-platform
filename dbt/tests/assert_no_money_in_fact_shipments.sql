-- fact_shipments no contiene columnas financieras.
--
-- Falla si el catálogo de la base expone en la tabla del hecho una columna
-- llamada currency_code o cuyo nombre contiene un monto de ingreso o costo:
-- (billed|payable|cost|revenue|tariff|deduction|surcharge|tax)_amount.
-- No prohíbe declared_value_amount ni billable_km, que no son ingreso ni
-- costo. Devuelve una fila por columna prohibida y otra si el catálogo no
-- encuentra la tabla, para que el test no pase en vacío; si no, cero filas.

{% set fact_relation = ref('fact_shipments') %}

with fact_table as (

    select
        '{{ fact_relation.schema }}' as table_schema,
        '{{ fact_relation.identifier }}' as table_name

),

fact_columns as (

    select columns_catalog.column_name
    from information_schema.columns as columns_catalog
    inner join fact_table
        on
            columns_catalog.table_schema = fact_table.table_schema
            and columns_catalog.table_name = fact_table.table_name

),

money_columns as (

    select fact_columns.column_name
    from fact_columns
    where
        fact_columns.column_name = 'currency_code'
        or fact_columns.column_name
        ~ '(billed|payable|cost|revenue|tariff|deduction|surcharge|tax)_amount'

),

missing_table as (

    select 'fact_table_not_found' as column_name
    where not exists (select 1 from fact_columns)

)

select money_columns.column_name
from money_columns
union all
select missing_table.column_name
from missing_table

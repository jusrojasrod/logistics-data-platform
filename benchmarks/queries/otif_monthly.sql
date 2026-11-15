-- OTIF mensual desde marts.mart_operations, sobre todo el histórico.
-- Fórmula canónica: Σ(otif_shipments) / Σ(closed_shipments) por mes, nunca un
-- promedio de porcentajes diarios. otif_pct es una razón en [0, 1]; si el mes
-- no tiene envíos cerrados el denominador es 0 y el resultado es NULL, no 0.

select
    date_trunc('month', pickup_date)::date as month,  -- noqa: RF04
    sum(otif_shipments)::numeric
    / nullif(sum(closed_shipments), 0) as otif_pct
from marts.mart_operations
group by date_trunc('month', pickup_date)
order by month;

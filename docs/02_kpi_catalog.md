# Catálogo de KPIs

Cada indicador se define una vez y se calcula igual en todos los consumos.

**Estado:** todos los indicadores están **por medir** hasta que exista una ejecución completa que los respalde. La columna «Estado» solo cambia cuando hay fecha, valor y artefacto.

## 1. Operación

| ID | Indicador | Fórmula | Grano | Fuente |
|---|---|---|---|---|
| OP-01 | OTIF | `sum(otif_flag) / sum(closed_flag)` | Envío | `fact_shipments` |
| OP-02 | OTD | `sum(on_time_flag) / sum(closed_flag)` | Envío | `fact_shipments` |
| OP-03 | In Full | `sum(in_full_flag) / sum(closed_flag)` | Envío | `fact_shipments` |
| OP-04 | Tiempo de tránsito (h) | `avg(epoch(delivered_at - picked_up_at) / 3600)` | Envío | `fact_shipments` |
| OP-05 | Minutos de retraso | `avg(epoch(actual_delivery_ts - promised_delivery_ts) / 60)` | Envío | `fact_shipments` |
| OP-06 | Recogida a tiempo | `sum(pickup_on_time_flag) / count(*)` | Envío | `fact_shipments` |
| OP-07 | Deadhead de flota | `sum(empty_km) / sum(total_km)` | Viaje | `fact_trips` |
| OP-08 | Deadhead imputado | `sum(empty_km_allocated) / (sum(loaded_km) + sum(empty_km_allocated))` | Envío × tramo | `bridge_shipment_trip` |
| OP-09 | Rendimiento de combustible | `sum(distance_km) / sum(gallons)` | Viaje | `fact_trips`, `fact_fuel_transactions` |
| OP-10 | Utilización de flota | `sum(weight_kg) / sum(capacity_kg_available)` | Viaje | `fact_trips` |

## 2. Finanzas

| ID | Indicador | Fórmula | Grano | Fuente |
|---|---|---|---|---|
| FI-01 | Ingreso facturado | `sum(billed_revenue_amount)` con estado `issued` o `paid`; las notas crédito restan | Línea de factura | `fact_invoices` |
| FI-02 | Costo carrier | `sum(payable_amount - deduction_amount)` | Línea de liquidación | `fact_carrier_settlements` |
| FI-03 | Costo de flota propia | `sum(cost_amount)` por componente | Envío × componente | `fact_shipment_costs` |
| FI-04 | Costo logístico total | `costo carrier + costo flota propia` | Envío / mes | FI-02, FI-03 |
| FI-05 | Margen bruto | `ingreso facturado - costo logístico total` | Cliente / ruta / mes | FI-01, FI-04 |
| FI-06 | Margen bruto % | `margen bruto / ingreso facturado`; nulo si el ingreso es ≤ 0 | Cliente / ruta / mes | FI-05 |
| FI-07 | Costo por envío | `costo logístico total / count(distinct shipment_id)` | Envío | FI-04 |
| FI-08 | Costo por km | `costo logístico total / sum(billable_km)` | Envío | FI-04, `fact_shipments` |
| FI-09 | Costo por kg | `costo logístico total / sum(weight_kg)` | Envío | FI-04, `fact_shipments` |
| FI-10 | Exactitud de factura | `100 * (1 - abs(facturado - tarifa) / tarifa)`, promedio ponderado, solo tarifa > 0 | Línea de factura | `fact_invoices` |
| FI-11 | DSO (días) | `avg(paid_date - invoice_date)` de facturas pagadas | Cliente / mes | `fact_invoices` |

**Regla de no duplicación:** un envío se ejecuta con flota propia o con carrier tercero. Nunca se suman ambos costos para el mismo envío.

## 3. Carriers

```text
Score = 0.35 * OTIF + 0.20 * Índice de costo + 0.15 * Recogida a tiempo
      + 0.10 * Aceptación + 0.10 * Tasa de daños + 0.10 * Exactitud de factura
```

| Componente | Fuente | Normalización |
|---|---|---|
| OTIF | `fact_shipments` | porcentaje directo |
| Índice de costo | `fact_carrier_settlements` | `100 * benchmark / costo`, acotado a 0–100 |
| Recogida a tiempo | `fact_shipments` | porcentaje directo |
| Aceptación | `fact_carrier_tenders` | `aceptados / ofertados * 100` |
| Tasa de daños | `fact_carrier_incidents` | `100 - min(100, 1000 * incidencias_con_daño / envíos)` |
| Exactitud de factura | `fact_invoices`, `fact_carrier_settlements` | `100 * (1 - abs(facturado - tarifa) / tarifa)` |

## 4. Datos y gobierno

| ID | Indicador | Fórmula | Fuente |
|---|---|---|---|
| DG-01 | DQ Score | `0.25*conformidad + 0.20*completitud + 0.20*unicidad + 0.15*validez + 0.10*integridad + 0.10*frescura` | `meta.dq_results` |
| DG-02 | Tasa de rechazo | `filas en cuarentena / filas ingeridas` | `quarantine` |
| DG-03 | Frescura (min) | `now() - max(ingested_at)` por dataset | `meta.datasets` |
| DG-04 | Cumplimiento de frescura | `datasets dentro del SLO / datasets con SLO` | `meta.datasets` |
| DG-05 | Éxito del pipeline | `runs exitosos / runs totales` | `meta.pipeline_runs` |
| DG-06 | Cobertura del catálogo | `tablas gold documentadas / tablas gold` | `meta.datasets` |

## 5. Dimensiones de análisis

Fecha · cliente y segmento · carrier y modelo de ejecución · ruta y región · tipo de servicio · vehículo · componente de costo · estado del envío y tipo de novedad.

## 6. Reglas de publicación

1. Un indicador sin fuente declarada no se publica.
2. Un indicador sin grano declarado no se publica.
3. Un valor sin fecha de medición y artefacto se rotula **por medir**.
4. Cambiar una fórmula exige actualizar este catálogo y el modelo que la calcula en el mismo cambio.

# Reglas de calidad

## 1. Dimensiones

| Dimensión | Pregunta |
|---|---|
| Completitud | ¿Faltan datos? |
| Unicidad | ¿Hay duplicados? |
| Validez | ¿Los valores cumplen formato y rango? |
| Consistencia | ¿Los datos concuerdan entre fuentes? |
| Frescura | ¿El dato está actualizado según su SLO? |
| Integridad referencial | ¿Las claves existen en las dimensiones? |
| Exactitud | ¿El dato refleja la realidad? |

## 2. Frescura por tier

| Tier | Datasets | Cadencia | SLO |
|---|---|---|---|
| T0 | Telemetría GPS | Continua | < 5 min (extensión) |
| T1 | APIs: clima, geocodificación, tasa de cambio, maestros | Horaria | < 2 h |
| T2 | TMS, ERP, CRM y hechos | Diaria | < 26 h |

Cada dataset declara `freshness_slo_minutes` en `meta.datasets`. Un dataset sin SLO no cuenta para el indicador de cumplimiento.

## 3. Reglas

| ID | Regla | Dimensión | Umbral | Acción |
|---|---|---|---|---|
| DQ-001 | `shipment_id` no nulo y único | Unicidad | 100 % | Cuarentena |
| DQ-002 | `delivered_at >= picked_up_at` | Consistencia | 100 % | Cuarentena |
| DQ-003 | `weight_kg` entre 0.1 y 40 000 | Validez | 99.5 % | Alerta |
| DQ-004 | `carrier_id` existe en `dim_carrier` | Integridad | 100 % | Inferido + alerta |
| DQ-005 | `customer_id` existe en `dim_customer` | Integridad | 100 % | Inferido + alerta |
| DQ-006a | Frescura batch < 26 h | Frescura | 100 % | Alerta |
| DQ-006b | Frescura API < 2 h | Frescura | 100 % | Alerta |
| DQ-006c | Frescura streaming < 5 min | Frescura | 100 % | Alerta |
| DQ-007 | Volumen diario dentro de ±30 % de la mediana de 7 días | Consistencia | 100 % | Alerta |
| DQ-008 | Campos esperados presentes en el JSON de API | Consistencia | 100 % | Bloqueo + alerta |
| DQ-009 | `invoice_line_id` único | Unicidad | 100 % | Deduplicar |
| DQ-010 | Moneda en el conjunto permitido | Validez | 100 % | Cuarentena |
| DQ-011 | `promised_delivery_ts` no nulo | Completitud | 99 % | Alerta |
| DQ-012 | PII no expuesta a roles no autorizados | Seguridad | 100 % | Alerta |
| DQ-013 | `settlement_line_id` único | Unicidad | 100 % | Deduplicar |
| DQ-014 | `tender_id` único y con respuesta o marca de expiración | Completitud | 100 % | Alerta |
| DQ-015 | Miembros inferidos con más de 7 días | Consistencia | 0 | Alerta |

## 4. Separación de válidos e inválidos

Los tests de dbt marcan fallos, pero no separan filas. La separación se hace en staging:

```text
stg_<fuente>__<entidad>_validated
  ├── calcula dq_errors (array de rule_id) e is_valid
  ├── stg_<fuente>__<entidad>_valid   → where is_valid
  └── quarantine.<entidad>            → where not is_valid
```

Cada fila en cuarentena contiene:

| Columna | Contenido |
|---|---|
| `quarantine_id` | Identificador de la fila rechazada |
| `source_table` | Tabla de origen |
| `rule_id` | Regla incumplida |
| `reason` | Descripción legible |
| `payload` | Fila original en JSON |
| `detected_at` | Momento de detección |
| `run_id` | Ejecución que la produjo |
| `severity` | Alta, media o baja |

Invariante verificada por test: `raw = valid ∪ quarantine` por `run_id`, y la intersección es vacía.

## 5. DQ Score

```text
DQ Score = 0.25 * conformidad
         + 0.20 * completitud
         + 0.20 * unicidad
         + 0.15 * validez
         + 0.10 * integridad referencial
         + 0.10 * frescura
```

- Se mide sobre `core` y `marts`: refleja la calidad de lo publicado.
- Se reporta por separado la tasa de rechazo de `raw`, la cuarentena por regla y los eventos de cambio de esquema.
- La línea base se mide en las primeras dos semanas. El objetivo se fija después, con datos.

## 6. Umbrales y alertas

- Los umbrales viven en `meta.dq_rules`, versionados.
- Una regla sin umbral declarado no se ejecuta.
- Las alertas incluyen dataset, regla, valor observado, umbral y ejecución.
- Ningún umbral se ajusta para que pase una regla; se ajusta porque cambió el negocio.

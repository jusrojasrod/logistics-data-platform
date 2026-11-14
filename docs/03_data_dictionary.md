# Diccionario de datos

Describe los archivos de origen que produce el generador sintético y las tablas del esquema `raw` donde aterrizan. Los tipos y la nulabilidad de `raw` son los del DDL explícito del loader: no se infieren de los CSV.

## 1. Archivos de origen

El generador escribe un CSV por entidad y un manifiesto de defectos en `data/<perfil>/`:

```bash
make data SCALE=demo        # o SCALE=local
```

| Archivo | Entidad | Grano | Filas | `source_system` |
|---|---|---|---|---|
| `shipments.csv` | Envíos | Una fila por envío (`shipment_id`), más los duplicados inyectados | 50 000 envíos base en `demo`, 200 000 en `local` | `tms` |
| `customers.csv` | Clientes | Una fila por cliente (`customer_id`) | 150 | `erp` |
| `carriers.csv` | Transportadores | Una fila por carrier (`carrier_id`) | 40 (10 propios, 30 terceros) | `tms` |
| `routes.csv` | Rutas | Una fila por par ordenado origen–destino (`route_id`) | 306 (18 ciudades) | `tms` |
| `service_types.csv` | Tipos de servicio | Una fila por tipo de servicio (`service_type_id`) | 6 | `tms` |
| `defect_manifest.json` | Defectos inyectados | Una entrada por tipo de defecto y columna | 5 entradas | — |

Convenciones comunes de los CSV:

- UTF-8, separador coma, fin de línea LF y fila de cabecera con el orden de columnas de este documento.
- Un campo vacío es un nulo.
- Marcas de tiempo en ISO 8601 con desplazamiento UTC explícito (`2026-02-27T14:08:00+00:00`); fechas en `YYYY-MM-DD`.
- Booleanos como `true` o `false`.
- La historia cubre 24 meses completos que terminan en la fecha de corte fija `2026-11-09`. Con el mismo perfil y la misma semilla, los archivos son idénticos byte a byte.
- Los ids de las dimensiones dependen solo de la posición en el catálogo. Todo id no nulo de `shipments.csv` existe en el catálogo de la misma corrida: no se generan huérfanos.

## 2. Tablas `raw`

Cada archivo aterriza en su tabla, append-only. El loader carga primero las dimensiones y después los envíos.

| Tabla | Archivo | `source_system` | Columnas |
|---|---|---|---|
| `raw.shipments` | `shipments.csv` | `tms` | 15 de negocio + 5 de metadatos = 20 |
| `raw.customers` | `customers.csv` | `erp` | 7 + 5 = 12 |
| `raw.carriers` | `carriers.csv` | `tms` | 5 + 5 = 10 |
| `raw.routes` | `routes.csv` | `tms` | 6 + 5 = 11 |
| `raw.service_types` | `service_types.csv` | `tms` | 3 + 5 = 8 |

### 2.1 Metadatos de ejecución

El loader añade estas columnas a toda fila de las cinco tablas, en este orden y detrás de las columnas de negocio.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `run_id` | `text` | `NOT NULL` | Identificador de la carga. Por defecto, un UUID nuevo por ejecución; todas las tablas de una misma carga comparten el valor |
| `source_system` | `text` | `NOT NULL` | Sistema de origen declarado por tabla: `erp` en `customers`, `tms` en el resto |
| `source_file` | `text` | `NOT NULL` | Nombre del archivo cargado (`shipments.csv`), nunca una ruta |
| `source_file_hash` | `text` | `NOT NULL` | SHA-256 del archivo en hexadecimal (64 caracteres) |
| `ingested_at` | `timestamptz` | `NOT NULL` | Instante de la carga; el mismo para todas las tablas de una ejecución |

`run_id` e `ingested_at` cambian entre ejecuciones; no forman parte de los archivos de origen ni del manifiesto.

### 2.2 Idempotencia

La carga es idempotente por archivo. Antes de insertar, el loader calcula el SHA-256 del CSV; si ese hash ya está en `source_file_hash` de la tabla destino, omite el archivo y no inserta filas. Cada archivo se carga en una sola transacción, con la tabla bloqueada para que dos cargas simultáneas del mismo archivo no lo inserten dos veces. La cabecera del CSV debe coincidir exactamente con el orden de columnas del contrato; si no, la carga falla.

### 2.3 `raw.shipments`

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `shipment_id` | `text` | `NOT NULL` | Identificador del envío (`SHP-000001`). Se repite solo en los duplicados inyectados |
| `customer_id` | `text` | `NULL` | Cliente, de `raw.customers`. Nulo solo por defecto inyectado |
| `carrier_id` | `text` | `NULL` | Transportador, de `raw.carriers`. Nulo solo por defecto inyectado |
| `route_id` | `text` | `NOT NULL` | Ruta, de `raw.routes` |
| `service_type_id` | `text` | `NOT NULL` | Tipo de servicio, de `raw.service_types` |
| `pickup_ts` | `timestamptz` | `NOT NULL` | Recogida planificada, entre las 06:00 y las 17:59 hora local (UTC−5) |
| `pickup_date` | `date` | `NOT NULL` | Fecha de la recogida planificada: `pickup_ts::date` |
| `promised_delivery_ts` | `timestamptz` | `NULL` | Entrega prometida: `pickup_ts` más el mayor entre el SLA del servicio y el tránsito estimado de la ruta más 2 h. Nulo solo por defecto inyectado |
| `picked_up_at` | `timestamptz` | `NOT NULL` | Recogida real. Todo envío generado es histórico, así que siempre tiene recogida |
| `delivered_at` | `timestamptz` | `NULL` | Entrega real. Nulo si el envío sigue en tránsito en la fecha de corte o si cerró sin entrega |
| `ordered_qty` | `integer` | `NOT NULL`, `check (ordered_qty >= 1)` | Unidades pedidas |
| `delivered_qty` | `integer` | `NULL` | Unidades entregadas; menor que `ordered_qty` en entregas parciales |
| `ordered_weight_kg` | `numeric(12,3)` | `NOT NULL`, `check (ordered_weight_kg > 0)` | Peso pedido, en kg |
| `delivered_weight_kg` | `numeric(12,3)` | `NULL` | Peso entregado, en kg, proporcional a las unidades entregadas |
| `definitive_failure_flag` | `boolean` | `NOT NULL` | `true` si el envío cerró sin entrega; `false` si se entregó o sigue en tránsito |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución |

Reglas de la tabla:

1. `definitive_failure_flag` tiene `true` o `false` en toda fila; nunca es nulo.
2. `delivered_qty` y `delivered_weight_kg` son nulos exactamente cuando `delivered_at` es nulo.
3. Los nulos inyectados caen solo en `promised_delivery_ts`, `customer_id` y `carrier_id`, y como mucho uno por fila. `shipment_id`, `pickup_ts`, `pickup_date`, `route_id`, `service_type_id`, cantidades, pesos y `definitive_failure_flag` nunca son nulos.
4. `pickup_date` coincide con `pickup_ts::date`.

### 2.4 `raw.customers`

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `customer_id` | `text` | `NOT NULL` | Identificador del cliente (`CUS-0001`) |
| `customer_name` | `text` | `NOT NULL` | Razón social ficticia |
| `segment` | `text` | `NOT NULL` | `ecommerce`, `manufacturing`, `pharma`, `retail` o `wholesale` |
| `industry` | `text` | `NOT NULL` | `agro`, `automotive`, `beverage`, `chemical`, `food`, `pharma`, `retail` o `technology` |
| `region` | `text` | `NOT NULL` | Región de la ciudad: `andina`, `caribe`, `orinoquia` o `pacifico` |
| `city` | `text` | `NOT NULL` | Ciudad del catálogo |
| `payment_term_days` | `integer` | `NOT NULL` | Plazo de pago: 30, 45, 60 o 90 días |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'erp'` |

### 2.5 `raw.carriers`

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `carrier_id` | `text` | `NOT NULL` | Identificador del transportador (`CAR-001`) |
| `carrier_name` | `text` | `NOT NULL` | Nombre ficticio |
| `carrier_type` | `text` | `NOT NULL` | `owned` (flota propia) o `third_party` (tercero) |
| `service_level` | `text` | `NOT NULL` | `economy`, `standard` o `premium` |
| `rate_index` | `numeric(6,2)` | `NOT NULL` | Índice relativo de tarifa, de 0.85 a 1.30 en pasos de 0.05 |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.6 `raw.routes`

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `route_id` | `text` | `NOT NULL` | Identificador de la ruta (`RTE-0001`) |
| `origin_city` | `text` | `NOT NULL` | Ciudad de origen |
| `destination_city` | `text` | `NOT NULL` | Ciudad de destino, distinta del origen |
| `distance_km` | `numeric(8,1)` | `NOT NULL` | Distancia por carretera: distancia geodésica × 1.28 |
| `estimated_hours` | `numeric(6,1)` | `NOT NULL` | Tránsito estimado: `distance_km / 62 + 0.75` h de cargue |
| `road_type` | `text` | `NOT NULL` | `primary` (≥ 700 km), `secondary` (≥ 300 km) o `tertiary` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.7 `raw.service_types`

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `service_type_id` | `text` | `NOT NULL` | Identificador del tipo de servicio |
| `service_type_name` | `text` | `NOT NULL` | Nombre del servicio |
| `sla_hours` | `integer` | `NOT NULL` | Ventana contractual de entrega, en horas |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

Catálogo:

| `service_type_id` | `service_type_name` | `sla_hours` |
|---|---|---|
| `SVC-STD` | `standard` | 72 |
| `SVC-EXP` | `express` | 24 |
| `SVC-ECO` | `economy` | 120 |
| `SVC-REF` | `refrigerated` | 48 |
| `SVC-FTL` | `full_truckload` | 96 |
| `SVC-LTL` | `less_than_truckload` | 72 |

## 3. Manifiesto de defectos

`data/<perfil>/defect_manifest.json` declara los defectos inyectados en `shipments.csv`. Es determinista: no guarda fecha de generación, ruta de salida, versión ni `run_id`, y `defects` sigue un orden fijo.

| Campo | Tipo | Descripción |
|---|---|---|
| `scale` | string | Perfil: `demo` o `local` |
| `seed` | integer | Semilla usada |
| `months` | integer | Meses de historia generados |
| `cutoff_date` | string | Fecha de corte, ISO 8601 |
| `shipments` | integer | Envíos base, sin contar duplicados |
| `defects[]` | array | Una entrada por tipo de defecto y columna |
| `defects[].type` | string | `duplicate`, `critical_null` o `delivered_before_pickup` |
| `defects[].column` | string | Columna afectada |
| `defects[].count` | integer | Filas afectadas |
| `defects[].rate` | number | `count / shipments`, redondeado a 5 decimales |
| `defects[].rule_id` | string | Regla de [`06_quality_rules.md`](06_quality_rules.md) que lo detecta |
| `defects[].expected_action` | string | `quarantine` o `alert` |

Defectos configurados. Cada fila afectada lleva un solo defecto:

| `type` | `column` | Tasa sobre los envíos base | `rule_id` | `expected_action` | Qué se inyecta |
|---|---|---|---|---|---|
| `duplicate` | `shipment_id` | 2 % | DQ-001 | `quarantine` | Copia idéntica de una fila limpia, escrita justo después del original y por encima de la base |
| `critical_null` | `promised_delivery_ts` | 1/3 del 1 % | DQ-011 | `alert` | Valor vacío |
| `critical_null` | `customer_id` | 1/3 del 1 % | DQ-005 | `alert` | Valor vacío |
| `critical_null` | `carrier_id` | 1/3 del 1 % | DQ-004 | `alert` | Valor vacío |
| `delivered_before_pickup` | `delivered_at` | 0,8 % | DQ-002 | `quarantine` | `delivered_at` movido entre 30 min y 48 h antes de `picked_up_at`, solo en envíos entregados |

El 1 % de nulos se reparte en tercios que difieren como mucho en una fila. Con `demo` (50 000 envíos base), `shipments.csv` tiene 51 000 filas y el manifiesto declara:

| `type` | `column` | `count` | `rate` |
|---|---|---|---|
| `duplicate` | `shipment_id` | 1000 | 0.02 |
| `critical_null` | `promised_delivery_ts` | 167 | 0.00334 |
| `critical_null` | `customer_id` | 167 | 0.00334 |
| `critical_null` | `carrier_id` | 166 | 0.00332 |
| `delivered_before_pickup` | `delivered_at` | 400 | 0.008 |

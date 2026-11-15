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

## 4. Linaje `raw` → `stg` → `core`

La transformación la hace dbt. Las relaciones de `stg` son vistas; las de `quarantine` y `core` son tablas que se reconstruyen en cada `dbt build`.

```text
raw.shipments ─► stg_tms__shipments ─► stg_tms__shipments_validated ─┬─► stg_tms__shipments_valid ─► core.fact_shipments
                                                                     └─► quarantine.shipments
raw.customers     ─► stg_erp__customers     ─► core.dim_customer      ─┐
raw.carriers      ─► stg_tms__carriers      ─► core.dim_carrier       ─┤
raw.routes        ─► stg_tms__routes        ─► core.dim_route         ─┼─► core.fact_shipments
raw.service_types ─► stg_tms__service_types ─► core.dim_service_type  ─┘
stg_tms__shipments_valid ─► core.dim_date
```

| Relación | Esquema | Materialización | Origen | Grano |
|---|---|---|---|---|
| `stg_tms__shipments` | `stg` | vista | `raw.shipments` | Una fila por fila de `raw.shipments` |
| `stg_tms__shipments_validated` | `stg` | vista | `stg_tms__shipments` | Una fila por fila de `raw.shipments`, clasificada |
| `stg_tms__shipments_valid` | `stg` | vista | `stg_tms__shipments_validated` | Una fila por `shipment_id` válido |
| `stg_erp__customers` | `stg` | vista | `raw.customers` | Una fila por `customer_id` |
| `stg_tms__carriers` | `stg` | vista | `raw.carriers` | Una fila por `carrier_id` |
| `stg_tms__routes` | `stg` | vista | `raw.routes` | Una fila por `route_id` |
| `stg_tms__service_types` | `stg` | vista | `raw.service_types` | Una fila por `service_type_id` |
| `quarantine.shipments` | `quarantine` | tabla | `stg_tms__shipments_validated` | Una fila por fila rechazada |
| `dim_date` | `core` | tabla | `stg_tms__shipments_valid` | Un día del calendario |
| `dim_customer` | `core` | tabla | `stg_erp__customers` | Un cliente, una sola versión |
| `dim_carrier` | `core` | tabla | `stg_tms__carriers` | Un transportador, una sola versión |
| `dim_route` | `core` | tabla | `stg_tms__routes` | Una ruta, una sola versión |
| `dim_service_type` | `core` | tabla | `stg_tms__service_types` | Un tipo de servicio, una sola versión |
| `fact_shipments` | `core` | tabla | `stg_tms__shipments_valid` y las cuatro dimensiones | Un envío válido (`shipment_id`) |

Partición exacta: para cada `run_id`, cada fila de `raw.shipments` termina en `stg_tms__shipments_valid` o en `quarantine.shipments`, nunca en las dos ni en ninguna. Un envío duplicado conserva exactamente una copia válida y el resto va a cuarentena con DQ-001; un envío rechazado por cualquier otra regla no tiene copia válida. Lo comprueba `dbt/tests/assert_shipments_partition.sql`, que lee el `shipment_id` de la cuarentena en `payload->>'shipment_id'`.

Nulabilidad en `stg`, `quarantine` y `core`: las vistas y las tablas creadas por dbt no declaran `NOT NULL` en la base; `information_schema` las muestra todas como nulables. Las garantías de las tablas siguientes salen de la lógica del modelo y de los tests de dbt, y la columna "Garantía" indica cuál aplica.

## 5. Staging de envíos

### 5.1 `stg.stg_tms__shipments`

Tipa `raw.shipments` sin borrar filas: tiene las mismas filas que la tabla de origen. Conserva las 15 columnas de negocio y las 5 de metadatos de §2.3 con el mismo nombre y los mismos tipos (`text`, `timestamptz`, `date`, `integer`, `numeric(12,3)` y `boolean`), y añade una columna.

| Columna | Tipo | Garantía | Descripción |
|---|---|---|---|
| 15 columnas de negocio y 5 de metadatos | como en §2.3 | como en §2.3 | Mismos valores que en `raw.shipments`, con el tipo explícito |
| `copy_rank` | `bigint` | ≥ 1 | Posición de la fila entre las copias del mismo envío: `row_number()` sobre `(run_id, shipment_id, source_file_hash)` ordenado por `ingested_at`. `1` es la primera copia; un valor mayor indica un duplicado |

### 5.2 `stg.stg_tms__shipments_validated`

Las 21 columnas de `stg_tms__shipments` más la clasificación de calidad. Cada fila de `raw.shipments` aparece una sola vez.

| Columna | Tipo | Garantía | Descripción |
|---|---|---|---|
| 21 columnas de `stg_tms__shipments` | como en §5.1 | como en §5.1 | Sin cambios |
| `dq_errors` | `text[]` | nunca nulo; vacío si la fila es válida | Reglas incumplidas, en orden de prioridad: `DQ-001` antes que `DQ-002` |
| `dq_primary_rule` | `text` | igual a `dq_errors[1]`; nulo si la fila es válida | Regla principal de la fila |
| `is_valid` | `boolean` | nunca nulo | `true` si `dq_errors` está vacío |

Reglas que rechazan filas (catálogo en [`06_quality_rules.md`](06_quality_rules.md)):

| Regla | Condición de rechazo | Prioridad |
|---|---|---|
| DQ-001 | `shipment_id` nulo o `copy_rank > 1` | 1 |
| DQ-002 | `delivered_at < picked_up_at`; si falta alguna de las dos fechas no se incumple | 2 |

Las fechas imposibles que inyecta el generador (`delivered_before_pickup`) caen en DQ-002, sin regla nueva. Una fila que incumple las dos reglas tiene `dq_errors = {DQ-001,DQ-002}` y `dq_primary_rule = 'DQ-001'`.

### 5.3 `stg.stg_tms__shipments_valid`

Filas de `stg_tms__shipments_validated` con `is_valid`. Tiene las 21 columnas de §5.1 —sin `dq_errors`, `dq_primary_rule` ni `is_valid`— y una fila por `shipment_id` (tests `unique` y `not_null`). Los nulos de `promised_delivery_ts`, `customer_id` y `carrier_id` no rechazan la fila: son alertas (§8).

## 6. Cuarentena

### 6.1 `quarantine.shipments`

Una fila por **fila rechazada** de `raw.shipments`, no por regla incumplida. Junto con `stg_tms__shipments_valid` forma la partición exacta de §4. No expone `shipment_id` como columna: se lee de `payload->>'shipment_id'`.

| Columna | Tipo | Garantía | Descripción |
|---|---|---|---|
| `quarantine_id` | `text` | `unique`, `not_null` | `md5` de `run_id`, `shipment_id`, `source_file_hash` y `copy_rank` unidos con `\|` |
| `source_table` | `text` | `not_null` | Tabla de origen: `raw.shipments` |
| `rule_id` | `text` | `not_null`; `DQ-001` o `DQ-002` | Regla principal según la prioridad de §5.2 |
| `reason` | `text` | `not_null` | Texto con la regla principal y todas las reglas incumplidas, por ejemplo `Regla principal: DQ-001. Reglas incumplidas: DQ-001, DQ-002.` |
| `payload` | `jsonb` | `not_null` | Fila original: las 20 columnas de `raw.shipments`, `copy_rank`, `dq_errors` (en el mismo orden) y `dq_primary_rule` |
| `detected_at` | `timestamptz` | — | Instante en que la construcción del modelo detectó el rechazo; cambia en cada `dbt build` |
| `run_id` | `text` | `not_null` | Carga de `raw` a la que pertenece la fila |
| `severity` | `text` | `not_null`; `high`, `medium` o `low` | Severidad del rechazo; hoy siempre `high` |

## 7. Staging de dimensiones

Cada modelo tipa su tabla `raw` y deja una fila por id natural: si el id llega en varias cargas, conserva la más reciente por `ingested_at` (y, a igualdad, por `source_file_hash`). El id natural tiene tests `unique` y `not_null`. Además de los atributos, conservan `run_id`, `source_system`, `source_file_hash` e `ingested_at` (`text` y `timestamptz`, como en §2.1); no conservan `source_file`.

| Modelo | Columnas de negocio y tipo |
|---|---|
| `stg.stg_erp__customers` | `customer_id` `text`, `customer_name` `text`, `segment` `text`, `industry` `text`, `region` `text`, `city` `text`, `payment_term_days` `integer` |
| `stg.stg_tms__carriers` | `carrier_id` `text`, `carrier_name` `text`, `carrier_type` `text`, `service_level` `text`, `rate_index` `numeric` |
| `stg.stg_tms__routes` | `route_id` `text`, `origin_city` `text`, `destination_city` `text`, `distance_km` `numeric`, `estimated_hours` `numeric`, `road_type` `text` |
| `stg.stg_tms__service_types` | `service_type_id` `text`, `service_type_name` `text`, `sla_hours` `integer` |

`rate_index`, `distance_km` y `estimated_hours` pasan a `numeric` sin precisión fija; los valores son los de `raw`. El significado de cada atributo es el de §2.4 a §2.7.

## 8. Alertas de calidad sobre envíos

Estas reglas no rechazan filas ni entran en la partición: son tests de dbt con `severity: warn`, que avisan sin romper la construcción.

| Regla | Qué comprueba | Columna | Test |
|---|---|---|---|
| DQ-003 | Peso entre 0.1 y 40 000 kg; los nulos de envíos sin entrega no cuentan | `delivered_weight_kg` de `stg_tms__shipments_validated` (es la `weight_kg` del catálogo) | `dbt/tests/assert_delivered_weight_range.sql` |
| DQ-004 | Clave de transportador resuelta en el hecho | `carrier_key` de `fact_shipments` | `dbt/tests/assert_dimension_foreign_keys.sql` |
| DQ-005 | Clave de cliente resuelta en el hecho | `customer_key` de `fact_shipments` | `dbt/tests/assert_dimension_foreign_keys.sql` |
| DQ-011 | Proporción de `promised_delivery_ts` no nulo ≥ 0.99 | `promised_delivery_ts` de `stg_tms__shipments_validated` | `dbt/tests/assert_promised_delivery_proportion.sql` |

Los `customer_id` y `carrier_id` nulos que inyecta el generador dejan nula la clave sustituta correspondiente en `fact_shipments`; no se crea un miembro inferido. `assert_dimension_foreign_keys.sql` vigila con el mismo criterio `route_key` y `service_type_key`, y los tests `relationships` del hecho hacia las cuatro dimensiones también avisan con `severity: warn`.

## 9. `core`

### 9.1 Dimensiones

Dimensiones de una sola versión: una fila por id natural, con clave sustituta estable `md5(<id>)` en `text`. No guardan historia (no tienen `valid_from`, `valid_to`, `is_current` ni `is_inferred`). Clave sustituta e id natural tienen tests `unique` y `not_null`; el número de filas coincide con el de su modelo de staging.

| Tabla | Columnas | Origen |
|---|---|---|
| `core.dim_customer` | `customer_key` `text` = `md5(customer_id)`, `customer_id`, `customer_name`, `segment`, `industry`, `region`, `city` (`text`), `payment_term_days` (`integer`) | `stg_erp__customers` |
| `core.dim_carrier` | `carrier_key` `text` = `md5(carrier_id)`, `carrier_id`, `carrier_name`, `carrier_type`, `service_level` (`text`), `rate_index` (`numeric`) | `stg_tms__carriers` |
| `core.dim_route` | `route_key` `text` = `md5(route_id)`, `route_id`, `origin_city`, `destination_city` (`text`), `distance_km`, `estimated_hours` (`numeric`), `road_type` (`text`) | `stg_tms__routes` |
| `core.dim_service_type` | `service_type_key` `text` = `md5(service_type_id)`, `service_type_id`, `service_type_name` (`text`), `sla_hours` (`integer`) | `stg_tms__service_types` |

### 9.2 `core.dim_date`

Calendario diario continuo entre la menor y la mayor `pickup_date` de `stg_tms__shipments_valid`. Si no hay envíos válidos, la tabla queda vacía.

| Columna | Tipo | Descripción |
|---|---|---|
| `date_key` | `date` | Día del calendario; una fila por día |
| `year_number` | `integer` | Año |
| `quarter_number` | `integer` | Trimestre, de 1 a 4 |
| `month_number` | `integer` | Mes, de 1 a 12 |
| `iso_year_number` | `integer` | Año ISO 8601 al que pertenece la semana |
| `iso_week_number` | `integer` | Semana ISO 8601, de 1 a 53 |
| `day_of_week_number` | `integer` | Día de la semana ISO 8601: 1 = lunes, 7 = domingo |

### 9.3 `core.fact_shipments`

Grano: una fila por envío válido (`shipment_id`), desde `stg_tms__shipments_valid`. Fecha de negocio `pickup_date`; sin partición. Guarda los ids naturales y las claves sustitutas de las cuatro dimensiones, unidas por `left join` sobre el id natural. No contiene columnas financieras: ni ingresos, ni costos, ni tarifas, ni moneda.

| Columna | Tipo | Garantía | Descripción |
|---|---|---|---|
| `shipment_id` | `text` | `unique`, `not_null` | Identificador del envío; clave del grano |
| `customer_key` | `text` | `relationships` con `dim_customer` (alerta) | Clave sustituta del cliente; nula si `customer_id` es nulo (DQ-005) |
| `carrier_key` | `text` | `relationships` con `dim_carrier` (alerta) | Clave sustituta del transportador; nula si `carrier_id` es nulo (DQ-004) |
| `route_key` | `text` | `relationships` con `dim_route` (alerta) | Clave sustituta de la ruta |
| `service_type_key` | `text` | `relationships` con `dim_service_type` (alerta) | Clave sustituta del tipo de servicio |
| `customer_id` | `text` | — | Id natural del cliente; nulo solo por defecto inyectado |
| `carrier_id` | `text` | — | Id natural del transportador; nulo solo por defecto inyectado |
| `route_id` | `text` | — | Id natural de la ruta |
| `service_type_id` | `text` | — | Id natural del tipo de servicio |
| `pickup_date` | `date` | `not_null` | Fecha de negocio: fecha de la recogida planificada |
| `pickup_ts` | `timestamptz` | — | Recogida planificada |
| `promised_delivery_ts` | `timestamptz` | — | Entrega prometida; nula solo por defecto inyectado (DQ-011) |
| `picked_up_at` | `timestamptz` | — | Recogida real |
| `delivered_at` | `timestamptz` | — | Entrega real; nula si no hubo entrega |
| `ordered_qty` | `integer` | — | Unidades pedidas |
| `delivered_qty` | `integer` | — | Unidades entregadas; nulas si no hubo entrega |
| `ordered_weight_kg` | `numeric(12,3)` | — | Peso pedido, en kg |
| `delivered_weight_kg` | `numeric(12,3)` | — | Peso entregado, en kg; nulo si no hubo entrega. Es la `weight_kg` de DQ-003 |
| `closed_flag` | `boolean` | `not_null` | Envío cerrado: `delivered_at is not null or definitive_failure_flag` |
| `on_time_flag` | `boolean` | nula si falta `delivered_at` o `promised_delivery_ts` | A tiempo: `delivered_at <= promised_delivery_ts`. Una entrega anticipada cuenta como a tiempo |
| `in_full_flag` | `boolean` | nula si no hubo entrega | Completo: `delivered_qty >= ordered_qty and delivered_weight_kg >= ordered_weight_kg` |
| `otif_flag` | `boolean` | igual a `on_time_flag and in_full_flag` en todas las filas | A tiempo y completo; con la lógica de tres valores de SQL es `false` si alguna de las dos es `false` y nula si ninguna es `false` y falta alguna |

`definitive_failure_flag` se usa para calcular `closed_flag` y no se proyecta en el hecho.

Tests del hecho, además de los de la tabla:

| Test | Qué comprueba |
|---|---|
| `dbt/tests/assert_fact_matches_valid_shipments.sql` | Cada `shipment_id` de `stg_tms__shipments_valid` aparece exactamente una vez en el hecho y el hecho no tiene ids ausentes en los válidos |
| `dbt/tests/assert_shipment_flags_consistent.sql` | `otif_flag is not distinct from (on_time_flag and in_full_flag)` en todas las filas |
| `dbt/tests/assert_no_money_in_fact_shipments.sql` | Ninguna columna se llama `currency_code` ni contiene `(billed\|payable\|cost\|revenue\|tariff\|deduction\|surcharge\|tax)_amount` |

### 9.4 Cómo seguir un envío

```sql
-- Un envío válido: al menos una fila en raw, una en válidos y una en el hecho.
select count(*) from raw.shipments where shipment_id = '<id>';
select count(*) from stg.stg_tms__shipments_valid where shipment_id = '<id>';
select count(*) from core.fact_shipments where shipment_id = '<id>';

-- Un rechazo: la fila está en cuarentena con su regla.
select rule_id, reason, payload->'dq_errors' as dq_errors
from quarantine.shipments
where payload->>'shipment_id' = '<id>';
```

Un rechazo por DQ-002 no aparece ni en `stg_tms__shipments_valid` ni en `fact_shipments`. Un duplicado por DQ-001 aparece en cuarentena y conserva exactamente una copia en los válidos y en el hecho.

## 10. `marts`

Los marts son agregados de consumo construidos por dbt a partir de `core`; no son hechos. Se materializan como tablas en el esquema `marts` y se reconstruyen en cada `dbt build`. Son la única capa que lee el BI.

### 10.1 `marts.mart_operations`

Grano: una fila por combinación **observada** de `pickup_date` × `customer_key` × `route_key` en `core.fact_shipments`, es decir, con al menos un envío, tenga o no envíos cerrados. No se genera rejilla completa: una combinación sin envíos no tiene fila. Las claves nulas que llegan del hecho (§8) se conservan como su propio grupo, para que las sumas del mart cuadren con el hecho.

Solo guarda conteos aditivos, que se pueden sumar por cualquier subconjunto de días, clientes o rutas.

| Columna | Tipo | Garantía | Descripción |
|---|---|---|---|
| `pickup_date` | `date` | `not_null`; parte del grano | Fecha de negocio: fecha de la recogida planificada del envío |
| `customer_key` | `text` | parte del grano | Clave sustituta del cliente; nula si el envío llega sin cliente (DQ-005) |
| `route_key` | `text` | parte del grano | Clave sustituta de la ruta; nula si el envío llega sin ruta |
| `closed_shipments` | `bigint` | — | Envíos cerrados del grupo: conteo de `closed_flag` verdadero. Denominador del OTIF |
| `otif_shipments` | `bigint` | — | Envíos a tiempo y completos del grupo: conteo de `otif_flag` verdadero. Numerador del OTIF |
| `on_time_shipments` | `bigint` | — | Envíos entregados a tiempo del grupo: conteo de `on_time_flag` verdadero |
| `in_full_shipments` | `bigint` | — | Envíos entregados completos del grupo: conteo de `in_full_flag` verdadero |

Las banderas nulas de los envíos abiertos no suman en ningún conteo. Como en `core`, `information_schema` muestra todas las columnas como nulables; las garantías salen del modelo y de los tests.

Tests del mart:

| Test | Qué comprueba |
|---|---|
| `dbt_utils.unique_combination_of_columns` | Una sola fila por `pickup_date`, `customer_key` y `route_key`; agrupa los nulos como iguales, así que también cubre los grupos con clave nula |
| `not_null` de `pickup_date` | Toda fila tiene fecha de negocio |
| `dbt/tests/assert_mart_reconciles_fact_shipments.sql` | Las sumas de `closed_shipments`, `otif_shipments`, `on_time_shipments` e `in_full_shipments` coinciden con los conteos de `closed_flag`, `otif_flag`, `on_time_flag` e `in_full_flag` verdaderos en `core.fact_shipments` |

### 10.2 OTIF: un porcentaje que no se almacena

El mart no guarda ningún porcentaje. El OTIF de cualquier periodo o segmento se calcula al consultar como razón de sumas:

```text
OTIF = Σ otif_shipments / Σ closed_shipments
```

- Nunca se promedian porcentajes diarios ni por grupo: el resultado cambiaría con el número de filas de cada grupo.
- Si el denominador es 0 (sin envíos cerrados en el periodo o segmento), el resultado es `NULL`, nunca 0. El dashboard lo muestra como sin datos.
- `otif_pct` de la consulta de referencia `benchmarks/queries/otif_monthly.sql` (columnas `month` y `otif_pct`, sobre todo el histórico) y la métrica «OTIF %» del dataset de Superset (`metric_name: otif_pct`) son una **razón entre 0 y 1**, no un valor de 0 a 100. Superset la muestra como porcentaje con el formato `.1%`; quien la reutilice en otra escala multiplica por 100 al presentar.

```sql
select
    date_trunc('month', pickup_date)::date as month,
    sum(otif_shipments)::numeric / nullif(sum(closed_shipments), 0) as otif_pct
from marts.mart_operations
group by date_trunc('month', pickup_date)
order by month;
```

### 10.3 Permisos de lectura

La carpeta `marts` de `dbt/dbt_project.yml` declara `+grants: {select: ['bi_reader']}`: en cada `dbt build`, dbt vuelve a conceder `SELECT` sobre los marts al rol de solo lectura `bi_reader`, que es el usuario de la conexión de Superset. `bi_reader` no recibe permisos sobre `raw`, `stg`, `quarantine` ni `core` desde dbt.

```sql
select grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'marts' and table_name = 'mart_operations';
```

### 10.4 Linaje hasta el dashboard

```text
raw.shipments ─► stg.stg_tms__shipments ─► stg.stg_tms__shipments_validated ─► stg.stg_tms__shipments_valid
  ─► core.fact_shipments ─► marts.mart_operations
  ─► dataset mart_operations (métrica «OTIF %») ─► chart «OTIF mensual» ─► dashboard «Operación»
```

| Objeto | Capa | Definición versionada | Origen |
|---|---|---|---|
| `marts.mart_operations` | `marts` | `dbt/models/marts/operations/mart_operations.sql` | `core.fact_shipments` |
| Consulta de referencia | — | `benchmarks/queries/otif_monthly.sql` | `marts.mart_operations` |
| Conexión `nexora` | Superset | `dashboards/superset/databases/nexora_postgres.yaml` | Base `nexora` con el rol `bi_reader`; la contraseña se fija al importar |
| Dataset `mart_operations` | Superset | `dashboards/superset/datasets/mart_operations.yaml` | Solo `marts.mart_operations`; no lee `raw` ni `stg` |
| Chart «OTIF mensual» | Superset | `dashboards/superset/charts/otif_monthly.yaml` | Dataset `mart_operations`: línea con `pickup_date` agrupado por mes y la métrica «OTIF %» |
| Dashboard «Operación» | Superset | `dashboards/superset/dashboards/operations_overview.yaml` | Chart «OTIF mensual», con el filtro «Mes» sobre `pickup_date` |

Los objetos de Superset se cargan con `make import-superset`. Para seguir un envío hasta el mart, se une por las tres claves del grano:

```sql
select m.*
from core.fact_shipments f
join marts.mart_operations m using (pickup_date, customer_key, route_key)
where f.shipment_id = '<id>';
```

Un envío con `customer_key` o `route_key` nulo no casa con `using`, porque `NULL` no es igual a `NULL`; en ese caso se une con `is not distinct from` en las tres columnas.

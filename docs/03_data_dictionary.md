# Diccionario de datos

Describe los archivos que produce el generador sintético, el esquema `tms` que simula el sistema de gestión de transporte, las tablas del esquema `raw` donde aterrizan los datos y las tablas de control de la ingesta en `meta`. A `raw` se llega por dos vías:

- **TMS:** los CSV de las entidades TMS (envíos, dimensiones, flota, viajes, tramos, paradas, eventos, combustible, ofertas a transportadores e incidencias) se publican en `tms.<tabla>`, y el conector batch los extrae de ahí a `raw.<tabla>` por watermark de `updated_at`.
- **ERP:** `customers.csv` llega como archivo a `raw.customers` y se identifica por el SHA-256 de su contenido.

Los tipos y la nulabilidad de `tms` y `raw` son los del DDL explícito de los contratos: no se infieren de los CSV.

## 1. Archivos de origen

El generador escribe un CSV por entidad y un manifiesto de defectos en `data/<perfil>/`:

```bash
make data SCALE=demo        # o SCALE=local, o SCALE=benchmark
```

Los CSV de las entidades TMS no se cargan como archivos en `raw`: se publican en el esquema `tms` (§1.1). Solo `customers.csv`, del ERP, llega como archivo a `raw`.

El perfil `benchmark` (2 000 000 envíos base) solo se genera a archivos, para medir la generación: el publicador rechaza su carpeta (§1.1) y nunca llega a `tms` ni a `raw`. El generador escribe en flujo, fila a fila, sin acumular filas en memoria: lo que retiene crece con el número de envíos base y de viajes, no con el de eventos.

| Archivo | Entidad | Grano | Filas | Destino | `source_system` |
|---|---|---|---|---|---|
| `shipments.csv` | Envíos | Una fila por envío (`shipment_id`), más los duplicados inyectados | 50 000 envíos base en `demo`, 200 000 en `local` | `tms.shipments` | `tms` |
| `customers.csv` | Clientes | Una fila por cliente (`customer_id`) | 150 | `raw.customers` | `erp` |
| `carriers.csv` | Transportadores | Una fila por carrier (`carrier_id`) | 40 (10 propios, 30 terceros) | `tms.carriers` | `tms` |
| `routes.csv` | Rutas | Una fila por par ordenado origen–destino (`route_id`) | 306 (18 ciudades) | `tms.routes` | `tms` |
| `service_types.csv` | Tipos de servicio | Una fila por tipo de servicio (`service_type_id`) | 6 | `tms.service_types` | `tms` |
| `vehicles.csv` | Vehículos de la flota propia | Una fila por vehículo (`vehicle_id`) | 300 (30 por carrier propio) | `tms.vehicles` | `tms` |
| `drivers.csv` | Conductores de la flota propia | Una fila por conductor (`driver_id`) | 400 (40 por carrier propio) | `tms.drivers` | `tms` |
| `trips.csv` | Viajes de la flota propia | Una fila por viaje (`trip_id`) | 23 130 en `demo` | `tms.trips` | `tms` |
| `trip_legs.csv` | Tramos de viaje | Una fila por tramo (`trip_id`, `leg_sequence`) | 47 986 en `demo` | `tms.trip_legs` | `tms` |
| `shipment_legs.csv` | Envío en un tramo | Una fila por envío y tramo (`shipment_id`, `trip_id`, `leg_sequence`) | 27 711 en `demo` | `tms.shipment_legs` | `tms` |
| `shipment_stops.csv` | Paradas del envío | Una fila por parada (`shipment_id`, `stop_sequence`) | 149 998 en `demo` (media de 3 por envío) | `tms.shipment_stops` | `tms` |
| `shipment_events.csv` | Eventos del envío | Una fila por evento (`event_id`), más los duplicados inyectados | 502 535 eventos base en `demo` (media de 10 por envío) | `tms.shipment_events` | `tms` |
| `fuel_transactions.csv` | Compras de combustible | Una fila por compra (`fuel_txn_id`) | 67 232 en `demo` | `tms.fuel_transactions` | `tms` |
| `carrier_tenders.csv` | Ofertas a transportadores | Una fila por oferta (`tender_id`) | 74 953 en `demo` (media de 1,5 por envío) | `tms.carrier_tenders` | `tms` |
| `carrier_incidents.csv` | Incidencias del transportador | Una fila por incidencia (`incident_id`) | 5 036 en `demo` (media de 0,1 por envío) | `tms.carrier_incidents` | `tms` |
| `defect_manifest.json` | Defectos inyectados | Una entrada por tabla, tipo de defecto y columna | 13 entradas | — | — |

Los conteos de `demo` son los del manifiesto (§3, campo `tables`) con la semilla por defecto; en `local` y `benchmark` las entidades por envío crecen en la misma proporción que los envíos base, y vehículos y conductores no cambian.

Convenciones comunes de los CSV:

- UTF-8, separador coma, fin de línea LF y fila de cabecera con el orden de columnas de este documento.
- Un campo vacío es un nulo.
- Marcas de tiempo en ISO 8601 con desplazamiento UTC explícito (`2026-02-27T14:08:00+00:00`); fechas en `YYYY-MM-DD`.
- Booleanos como `true` o `false`.
- La historia cubre 24 meses completos que terminan en la fecha de corte fija `2026-11-09`. Con el mismo perfil y la misma semilla, los archivos son idénticos byte a byte.
- Los ids de las dimensiones y de la flota dependen solo de la posición en el catálogo. Todo id no nulo de `shipments.csv` existe en el catálogo de la misma corrida: no se generan huérfanos.
- Las entidades que cuelgan de un envío (paradas, eventos, ofertas, incidencias y `shipment_legs`) se derivan de su fila base limpia, antes de inyectar los defectos de envíos, y solo referencian `shipment_id` que existen en `shipments.csv`. Todo `trip_id` de tramos, `shipment_legs` y combustible existe en `trips.csv`, y todo tramo de `shipment_legs` existe en `trip_legs.csv`.
- Montos en pesos colombianos: columnas `_amount` con `currency_code = 'COP'`.

Reglas de las entidades operativas:

1. **Viajes, tramos y combustible** existen solo para la flota propia (los 10 carriers `owned`). Los envíos base de un carrier propio con el mismo origen y la misma `pickup_date` viajan juntos en un viaje `delivery`: sale del origen a las 05:00 hora local más 0 a 59 minutos, visita los destinos ordenados por distancia desde el origen (a igual distancia, por posición en el catálogo de ciudades), se detiene de 30 a 90 minutos en cada destino y vuelve vacío al origen (`loaded_flag = false`). Distancias y horas de cada tramo salen del catálogo de rutas. Vehículo y conductor se sortean dentro del carrier. Tras cerca del 3 % de los viajes con carga hay un viaje `repositioning` de un solo tramo vacío y sin envíos.
2. Un envío ocupa los tramos de su viaje desde el primero hasta el que llega a su destino (`shipment_legs`). Los envíos de carriers terceros no tienen viaje ni `shipment_legs`.
3. Cada viaje hace `max(1, ceil(km planificados / 600))` compras de combustible, repartidas a partes iguales a lo largo de su recorrido planificado; `gallons` es el tramo de recorrido entre compras dividido por el rendimiento del vehículo, y el odómetro de cada vehículo se acumula en orden de viaje con la distancia generada.
4. **Paradas:** recogida, de 0 a 2 centros de tránsito y entrega. **Eventos:** `created`, `picked_up`, llegada y salida de cada centro de tránsito, `in_transit` de relleno, `out_for_delivery` y `delivered` o `delivery_failed` si el envío cerró, y `damage` si tiene una incidencia de daño. **Ofertas:** la mitad de los envíos tiene una oferta previa a otro carrier (`rejected` o `expired`) y todos tienen la oferta `accepted` al `carrier_id` del envío, ofrecida entre 2 y 48 h antes de `pickup_ts` y respondida antes de esa hora. **Incidencias:** cerca del 10 % de los envíos, reportadas entre la recogida y el fin del envío, respondidas de 10 a 1440 minutos después y resueltas de 1 a 14 días después del reporte.
5. **Fecha de corte:** ninguna marca observada de una fila limpia pasa del final de la fecha de corte (`2026-11-09T23:59:59+00:00`). Si una marca obligatoria (`start_ts`, `departure_ts`, `event_ts`, `txn_ts`, `offered_ts`, `reported_ts` o el `responded_ts` de una incidencia) caería después, la fila no se genera: un tramo omitido se lleva sus `shipment_legs` y su combustible, y una incidencia omitida, su evento `damage`; distancia, km vacíos, minutos detenido y galones del viaje suman solo sus tramos generados. Si caería después una marca nulable (`end_ts`, `arrival_ts`, `actual_ts`, `resolved_ts`), queda nula: la operación sigue en curso. `planned_ts` de las paradas es un plan, como `promised_delivery_ts`, y puede pasar de la fecha de corte. Las únicas filas con una marca observada posterior son los eventos con `impossible_date` (§3).
6. Simplificaciones: no se controla que un vehículo haga dos viajes a la vez ni su capacidad, y los horarios del viaje no se concilian con `delivered_at` del envío.

### 1.1 Esquema `tms`

`tms` simula la base operativa del TMS. El publicador inserta en ella los CSV TMS de una carpeta, conectado como `tms_writer` (`NEXORA_TMS_DSN` y `TMS_WRITER_PASSWORD`, del entorno o de `.env`):

```bash
python -m nexora.generators.tms_publisher --input data/demo                      # todas las filas
python -m nexora.generators.tms_publisher --input data/demo --until 2026-11-03   # hasta una fecha de corte
bash scripts/seed_db.sh demo                                                     # genera data/demo y publica todo
```

`scripts/seed_db.sh` genera el perfil y publica sin fecha de corte; no carga `raw`. `--schema` cambia el esquema de destino (por defecto `tms`) y `--dsn` la conexión.

| Tabla | Origen | Columnas | Unicidad e índices |
|---|---|---|---|
| `tms.carriers` | `carriers.csv` | `tms_row_id` + 5 de negocio + `updated_at` | `unique (carrier_id)` |
| `tms.routes` | `routes.csv` | `tms_row_id` + 6 de negocio + `updated_at` | `unique (route_id)` |
| `tms.service_types` | `service_types.csv` | `tms_row_id` + 3 de negocio + `updated_at` | `unique (service_type_id)` |
| `tms.shipments` | `shipments.csv` | `tms_row_id` + 15 de negocio + `updated_at` | Ninguna sobre `shipment_id`: conserva todas las copias, como la fuente |
| `tms.vehicles` | `vehicles.csv` | `tms_row_id` + 9 de negocio + `updated_at` | `unique (vehicle_id)` |
| `tms.drivers` | `drivers.csv` | `tms_row_id` + 9 de negocio + `updated_at` | `unique (driver_id)` |
| `tms.trips` | `trips.csv` | `tms_row_id` + 12 de negocio + `updated_at` | Índice no único `trips_natural_key_idx (trip_id)` |
| `tms.trip_legs` | `trip_legs.csv` | `tms_row_id` + 8 de negocio + `updated_at` | Índice no único `trip_legs_natural_key_idx (trip_id, leg_sequence)` |
| `tms.shipment_legs` | `shipment_legs.csv` | `tms_row_id` + 6 de negocio + `updated_at` | Índice no único `shipment_legs_natural_key_idx (shipment_id, trip_id, leg_sequence)` |
| `tms.shipment_stops` | `shipment_stops.csv` | `tms_row_id` + 6 de negocio + `updated_at` | Índice no único `shipment_stops_natural_key_idx (shipment_id, stop_sequence)` |
| `tms.shipment_events` | `shipment_events.csv` | `tms_row_id` + 8 de negocio + `updated_at` | Índice no único `shipment_events_natural_key_idx (event_id)`: conserva los duplicados |
| `tms.fuel_transactions` | `fuel_transactions.csv` | `tms_row_id` + 11 de negocio + `updated_at` | Índice no único `fuel_transactions_natural_key_idx (fuel_txn_id)` |
| `tms.carrier_tenders` | `carrier_tenders.csv` | `tms_row_id` + 9 de negocio + `updated_at` | Índice no único `carrier_tenders_natural_key_idx (tender_id)` |
| `tms.carrier_incidents` | `carrier_incidents.csv` | `tms_row_id` + 11 de negocio + `updated_at` | Índice no único `carrier_incidents_natural_key_idx (incident_id)` |

La clave natural de cada tabla es la columna o columnas entre paréntesis (en `tms.shipments`, `shipment_id`). Las tablas con `unique` lo tienen porque su fuente no repite ids; las demás no exigen unicidad, porque la fuente puede repetir filas, y el índice sirve a la comprobación de claves ya publicadas.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `tms_row_id` | `bigint` | `NOT NULL`, clave primaria | Identidad generada por la base (`generated always as identity`); da el orden de inserción |
| Columnas de negocio | como en §2.3 a §2.7 y §2.9 a §2.18 | ídem | Mismos nombres, tipos, nulabilidad y `check` que la tabla `raw` del mismo nombre |
| `updated_at` | `timestamptz` | `NOT NULL` | Instante de inserción, del reloj de la base (`clock_timestamp()`). Es la columna del watermark de extracción |

Reglas de la publicación:

1. Solo inserta: nunca actualiza ni borra filas de `tms`.
2. Publica en este orden: `carriers`, `routes`, `service_types`, `shipments`, `vehicles`, `drivers`, `trips`, `trip_legs`, `shipment_legs`, `shipment_stops`, `shipment_events`, `fuel_transactions`, `carrier_tenders` y `carrier_incidents`, cada tabla en su propia transacción. Cada transacción toma primero el bloqueo consultivo `pg_advisory_xact_lock(hashtext('<esquema>.<tabla>'))`, el mismo que toma el conector de extracción, así que ninguna fila se confirma con un `updated_at` anterior a un watermark ya leído.
3. Dentro de la transacción, el CSV se lee por bloques de 50 000 filas: cada bloque se copia con `COPY` a una tabla temporal, se inserta desde ahí y se vacía la temporal antes del siguiente. Un bloque solo supera ese tamaño para no separar las copias de una fila, así que el archivo nunca se carga entero en memoria. Con `--until`, el publicador guarda además una fecha por envío y otra por viaje.
4. Una fila cuya clave natural ya existe en la tabla no se vuelve a insertar. Las copias de una fila (envíos y eventos duplicados) van justo detrás del original y entran juntas, en la misma transacción. Por eso las copias de un envío o de un evento siempre caen en la misma extracción hacia `raw`.
5. Con `--until`, solo se publican las filas cuya fecha de disponibilidad es igual o anterior a la fecha de corte; sin `--until`, todas.
6. Una carpeta cuyo `defect_manifest.json` declara `"scale": "benchmark"` se rechaza antes de conectar, con código de salida 2.

Fecha de disponibilidad de un envío: con `x` el entero de los primeros 8 dígitos hexadecimales del SHA-256 de `shipment_id`, el envío es tardío si `x % 100 < 2` (cerca del 2 % de los envíos) y entonces está disponible `2 + (x // 100) % 6` días después de su `pickup_date`, es decir, de 2 a 7 días; si no, está disponible el mismo día de su `pickup_date`. La fecha de un grupo es la mayor de sus copias. Un envío tardío llega a `tms`, y después a `raw`, con una `pickup_date` anterior a la fecha de corte de la publicación que lo incluye.

Fecha de disponibilidad del resto de tablas:

| Tablas | Disponible |
|---|---|
| `carriers`, `routes`, `service_types`, `vehicles`, `drivers` | Siempre |
| `trips` | Fecha UTC de `start_ts` |
| `trip_legs` | Con su viaje |
| `fuel_transactions` | Fecha UTC de `txn_ts`; nunca antes que su viaje, porque `txn_ts` no es anterior a `start_ts` |
| `shipment_stops`, `shipment_legs` | Con su envío |
| `shipment_events` (`event_ts`), `carrier_tenders` (`offered_ts`), `carrier_incidents` (`reported_ts`) | Fecha UTC de esa marca más el retraso de su envío, y nunca antes que el envío. Si la marca pasa del final de la fecha de corte del dataset, cosa que solo ocurre en una fila defectuosa (`impossible_date`), toma la fecha del envío |

Así, con cualquier `--until`, ninguna fila que cuelga de un envío o de un viaje se publica antes que él. Toda fila se publica sin `--until` o con un `--until` igual o posterior a la fecha de corte del dataset más 7 días, el retraso máximo de un envío tardío.

Permisos: `tms_writer` crea las tablas de `tms` y es el único que escribe en ellas; `etl_writer` solo tiene `SELECT` sobre ellas.

## 2. Tablas `raw`

Las tablas `raw` son append-only: los conectores batch solo insertan, nunca borran ni reescriben filas. Las carga el conector `python -m nexora.ingestion.batch.loaders`, conectado como `etl_writer` (`NEXORA_PG_DSN` y `ETL_WRITER_PASSWORD`, del entorno o de `.env`), por una de dos vías:

- `--source tms`: extrae las catorce tablas de `tms`, en el orden de publicación de §1.1, hacia la tabla `raw` del mismo nombre, con las filas cuyo `updated_at` es posterior al watermark de la tabla (§2.2). `--tms-schema`, `--raw-schema` y `--meta-schema` cambian los esquemas de origen, destino y control (por defecto `tms`, `raw` y `meta`).
- `--source files --input <carpeta>`: carga en `raw.customers` los archivos `customers*.csv` de la carpeta, en orden de nombre, omitiendo los que ya están registrados por su hash (§2.2).

En Airflow, `dag_ingest_batch` ejecuta las dos vías con el calendario `0 2 * * *`, en las tareas `extract_tms` y `load_erp_files`; la carpeta de archivos es `NEXORA_DATA_DIR/NEXORA_SCALE`.

| Tabla | Origen | Vía | `source_system` | Columnas |
|---|---|---|---|---|
| `raw.shipments` | `tms.shipments` | Extracción por watermark | `tms` | 15 de negocio + 5 de metadatos = 20 |
| `raw.customers` | `customers.csv` | Archivo por hash | `erp` | 7 + 5 = 12 |
| `raw.carriers` | `tms.carriers` | Extracción por watermark | `tms` | 5 + 5 = 10 |
| `raw.routes` | `tms.routes` | Extracción por watermark | `tms` | 6 + 5 = 11 |
| `raw.service_types` | `tms.service_types` | Extracción por watermark | `tms` | 3 + 5 = 8 |
| `raw.vehicles` | `tms.vehicles` | Extracción por watermark | `tms` | 9 + 5 = 14 |
| `raw.drivers` | `tms.drivers` | Extracción por watermark | `tms` | 9 + 5 = 14 |
| `raw.trips` | `tms.trips` | Extracción por watermark | `tms` | 12 + 5 = 17 |
| `raw.trip_legs` | `tms.trip_legs` | Extracción por watermark | `tms` | 8 + 5 = 13 |
| `raw.shipment_legs` | `tms.shipment_legs` | Extracción por watermark | `tms` | 6 + 5 = 11 |
| `raw.shipment_stops` | `tms.shipment_stops` | Extracción por watermark | `tms` | 6 + 5 = 11 |
| `raw.shipment_events` | `tms.shipment_events` | Extracción por watermark | `tms` | 8 + 5 = 13 |
| `raw.fuel_transactions` | `tms.fuel_transactions` | Extracción por watermark | `tms` | 11 + 5 = 16 |
| `raw.carrier_tenders` | `tms.carrier_tenders` | Extracción por watermark | `tms` | 9 + 5 = 14 |
| `raw.carrier_incidents` | `tms.carrier_incidents` | Extracción por watermark | `tms` | 11 + 5 = 16 |

Ninguna tabla `raw` tiene restricción `unique`: las copias que llegan de la fuente se conservan.

### 2.1 Metadatos de ejecución

Los conectores añaden estas columnas a toda fila de todas las tablas `raw`, en este orden y detrás de las columnas de negocio.

| Columna | Tipo | Nulabilidad | Extracción de `tms` | Archivo ERP |
|---|---|---|---|---|
| `run_id` | `text` | `NOT NULL` | Identificador de la ejecución del conector: por defecto, un UUID nuevo por ejecución (`--run-id` lo fija). Todas las tablas de una misma ejecución comparten el valor | Igual |
| `source_system` | `text` | `NOT NULL` | `tms` | `erp` |
| `source_file` | `text` | `NOT NULL` | Tabla de origen, `<esquema>.<tabla>` (`tms.shipments`) | Nombre del archivo cargado (`customers.csv`), nunca una ruta |
| `source_file_hash` | `text` | `NOT NULL` | SHA-256 en hexadecimal (64 caracteres) del lote extraído: las filas de negocio de la ventana como `row(...)::text`, unidas por salto de línea en orden de `tms_row_id` y calculado en UTC. Todas las filas de un lote comparten el valor | SHA-256 en hexadecimal (64 caracteres) del contenido del archivo |
| `ingested_at` | `timestamptz` | `NOT NULL` | Instante de la ejecución; el mismo para todas las tablas de una ejecución | Igual |

Cálculo del hash del lote: el conector lo calcula en el cliente, sin concatenar la ventana en el servidor. Un cursor de servidor lee la ventana por bloques de 10 000 filas (`select row(<columnas de negocio>)::text … order by tms_row_id`, dentro de la transacción de la extracción y ya en UTC) y el SHA-256 recibe cada fila en UTF-8, con un salto de línea entre filas. El resultado es el mismo que el de concatenar en el servidor las filas en ese orden con `string_agg(…, E'\n' order by tms_row_id)` y aplicar SHA-256: los lotes ya cargados siguen siendo verificables con esa consulta. La memoria del cliente depende del tamaño del bloque, no del de la ventana. El número de filas del recorrido se compara con el de filas insertadas en `raw`.

`run_id` e `ingested_at` cambian entre ejecuciones; no forman parte de los archivos de origen, de `tms` ni del manifiesto. En `dag_ingest_batch`, `extract_tms` y `load_erp_files` son ejecuciones distintas y cada una tiene su `run_id`.

### 2.2 Idempotencia

**Extracción de `tms`, por watermark.** Cada tabla se extrae en una sola transacción, en este orden:

1. Fija la zona horaria de la transacción en UTC, para que el hash del lote no dependa de la sesión.
2. Toma el bloqueo consultivo `pg_advisory_xact_lock(hashtext('tms.<tabla>'))`, el mismo del publicador. Dos extracciones simultáneas de la misma tabla se ordenan, y la segunda ve el watermark que fijó la primera.
3. Solo entonces lee el watermark de `tms.<tabla>` en `meta.batch_watermarks` (§2.8) y el `max(updated_at)` de la tabla.
4. La ventana es `(watermark, max(updated_at)]`: excluye el watermark e incluye la cota superior. En la primera extracción, sin watermark, la ventana toma todas las filas hasta `max(updated_at)`.
5. Inserta en `raw.<tabla>` las filas de la ventana en orden de `tms_row_id`, con los metadatos de §2.1, comprueba que el número de filas insertadas coincide con el del lote y deja el watermark en `max(updated_at)`.

Si la tabla está vacía o `max(updated_at)` es igual al watermark, no inserta filas ni toca el watermark: repetir la extracción inserta 0 filas. Si `max(updated_at)` es anterior al watermark, la extracción falla. Como la ventana se define por `updated_at` y no por la fecha de negocio, los envíos tardíos entran en la extracción siguiente a su publicación aunque su `pickup_date` sea anterior. Cada fila de `tms` se extrae una sola vez y las copias de un `shipment_id` o de un `event_id` se publican juntas (§1.1), así que ninguno queda repartido en dos `run_id`.

Si `tms.<tabla>` no existe, la extracción de esa tabla falla con `LookupError` (`no existe la tabla de origen tms.<tabla>`) antes de crear nada en `raw` o en `meta`, y la ejecución se detiene ahí: las tablas anteriores de la misma ejecución ya quedaron extraídas, con sus filas en `raw` y su watermark, y las siguientes no se intentan. Pasa, por ejemplo, si `tms` se publicó con una versión del publicador que no tenía todas las tablas. Se corrige publicando la carpeta completa (`scripts/seed_db.sh` o `tms_publisher`) y repitiendo la extracción; las tablas ya extraídas no traen filas repetidas.

Cada tabla avanza en su propia transacción y con su propio watermark, como la publicación. Si se publica entre la extracción de dos tablas de una misma ejecución, una fila hija (por ejemplo, un evento) puede llegar a `raw` una ejecución antes que su envío o su viaje. Las capas siguientes no deben suponer que padre e hijo comparten `run_id`.

**Archivos ERP, por hash.** Cada archivo se carga en una sola transacción. La cabecera del CSV debe coincidir exactamente con el orden de columnas del contrato; si no, la carga falla. El conector calcula el SHA-256 del contenido, toma un bloqueo consultivo del registro de archivos de la tabla y busca el par `(tabla, hash)` en `meta.ingested_files` (§2.8):

- Si está registrado, omite el archivo y no inserta filas. Una copia renombrada de un archivo ya cargado tiene el mismo hash y también se omite.
- Si no, copia el archivo a una tabla temporal tipada con el contrato, lo inserta en `raw.customers` con los metadatos de §2.1 y lo registra en `meta.ingested_files`, en la misma transacción. Un archivo modificado tiene un hash nuevo y se carga.

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

### 2.8 Control de la ingesta en `meta`

Los conectores crean estas tablas en su primera ejecución, como `etl_writer`, y las escriben en la misma transacción que la carga en `raw`: si la carga de una tabla o de un archivo falla, no deja filas en `raw` ni cambios en `meta`.

`meta.batch_watermarks`: una fila por tabla de `tms` extraída al menos una vez.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `source_table` | `text` | `NOT NULL`, clave primaria | Tabla de origen, `<esquema>.<tabla>` (`tms.shipments`) |
| `watermark_ts` | `timestamptz` | `NOT NULL` | Mayor `updated_at` ya copiado a `raw`; cota inferior, excluida, de la extracción siguiente |
| `run_id` | `text` | `NOT NULL` | Ejecución que fijó el watermark |
| `set_at` | `timestamptz` | `NOT NULL` | `ingested_at` de esa ejecución |

`meta.ingested_files`: una fila por archivo ERP cargado, identificado por su contenido.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `target_table` | `text` | `NOT NULL`, clave primaria con `source_file_hash` | Tabla `raw` de destino, sin esquema (`customers`) |
| `source_file_hash` | `text` | `NOT NULL`, clave primaria con `target_table` | SHA-256 del contenido del archivo |
| `source_file` | `text` | `NOT NULL` | Nombre del archivo con el que se cargó por primera vez |
| `run_id` | `text` | `NOT NULL` | Ejecución que lo cargó |
| `row_count` | `integer` | `NOT NULL` | Filas insertadas en `raw` |
| `ingested_at` | `timestamptz` | `NOT NULL` | Instante de la carga |

```sql
select * from meta.batch_watermarks order by source_table;
select target_table, source_file, row_count, ingested_at from meta.ingested_files order by ingested_at;
```

### 2.9 `raw.vehicles`

Catálogo fijo de la flota propia: 30 vehículos por cada uno de los 10 carriers `owned`, en bloques contiguos de ids. Los atributos dependen solo de la posición.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `vehicle_id` | `text` | `NOT NULL` | Identificador del vehículo (`VEH-001`) |
| `carrier_id` | `text` | `NOT NULL` | Carrier propio dueño del vehículo, de `raw.carriers` |
| `plate` | `text` | `NOT NULL` | Placa ficticia con formato no oficial (`NXV-0001`) |
| `vehicle_type` | `text` | `NOT NULL` | `tractor_trailer`, `rigid_truck`, `medium_truck` o `light_truck` |
| `capacity_kg` | `numeric(10,1)` | `NOT NULL` | Capacidad nominal de su clase, en kg |
| `capacity_m3` | `numeric(8,2)` | `NOT NULL` | Capacidad nominal de su clase, en m³ |
| `fuel_type` | `text` | `NOT NULL` | `diesel` o `natural_gas` |
| `km_per_gallon` | `numeric(6,2)` | `NOT NULL` | Rendimiento: el nominal de su clase con un ajuste de −0.40 a +0.40 según la posición |
| `purchase_date` | `date` | `NOT NULL` | Fecha de compra, entre 2016 y 2024 |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.10 `raw.drivers`

Catálogo fijo de conductores de la flota propia: 40 por cada carrier `owned`. Contiene datos personales ficticios clasificados como PII ([`05_governance_policy.md`](05_governance_policy.md) §4).

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `driver_id` | `text` | `NOT NULL` | Identificador del conductor (`DRV-001`) |
| `carrier_id` | `text` | `NOT NULL` | Carrier propio del conductor, de `raw.carriers` |
| `driver_name` | `text` | `NOT NULL` | PII. Nombre ficticio: combina por posición un nombre y un apellido inventado; no se repite |
| `driver_document` | `text` | `NOT NULL` | PII. Documento con formato no oficial (`NXD-0000001`) |
| `driver_phone` | `text` | `NOT NULL` | PII. Teléfono con prefijo no asignado (`+57 000 000 0001`) |
| `driver_email` | `text` | `NOT NULL` | PII. Correo en el dominio reservado `example.com` (`drv001@example.com`) |
| `license_type` | `text` | `NOT NULL` | Categoría de licencia: `C1`, `C2` o `C3` |
| `hire_date` | `date` | `NOT NULL` | Fecha de contratación, entre 2014 y 2025 |
| `base_city` | `text` | `NOT NULL` | Ciudad base, del catálogo |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.11 `raw.trips`

Un viaje de la flota propia (§1, reglas de las entidades operativas). Fecha de negocio: `start_ts`.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `trip_id` | `text` | `NOT NULL` | Identificador del viaje (`TRP-0000001`), numerado por `pickup_date`, carrier y ciudad de origen; un viaje `repositioning` lleva el número siguiente al de su viaje con carga |
| `carrier_id` | `text` | `NOT NULL` | Carrier propio, de `raw.carriers` |
| `vehicle_id` | `text` | `NOT NULL` | Vehículo del carrier, de `raw.vehicles` |
| `driver_id` | `text` | `NOT NULL` | Conductor del carrier, de `raw.drivers` |
| `trip_type` | `text` | `NOT NULL` | `delivery` (con envíos) o `repositioning` (vacío, sin envíos) |
| `origin_city` | `text` | `NOT NULL` | Ciudad de salida y de regreso de un viaje `delivery` |
| `start_ts` | `timestamptz` | `NOT NULL` | Salida: 05:00 hora local (UTC−5) de la `pickup_date` de sus envíos, más 0 a 59 minutos |
| `end_ts` | `timestamptz` | `NULL` | Llegada del último tramo. Nula si el viaje sigue en curso en la fecha de corte |
| `distance_km` | `numeric(8,1)` | `NOT NULL` | Suma de la distancia de sus tramos generados |
| `empty_km` | `numeric(8,1)` | `NOT NULL` | Parte de `distance_km` recorrida sin carga |
| `stopped_minutes` | `integer` | `NOT NULL` | Minutos detenido en los destinos antes de sus tramos generados |
| `fuel_gallons` | `numeric(8,2)` | `NOT NULL` | Consumo estimado: `distance_km / km_per_gallon` del vehículo |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.12 `raw.trip_legs`

Un tramo de un viaje: de una ciudad a la siguiente.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `trip_id` | `text` | `NOT NULL` | Viaje, de `raw.trips` |
| `leg_sequence` | `integer` | `NOT NULL` | Posición del tramo en el viaje, desde 1 |
| `origin_city` | `text` | `NOT NULL` | Ciudad de salida del tramo |
| `destination_city` | `text` | `NOT NULL` | Ciudad de llegada del tramo |
| `distance_km` | `numeric(8,1)` | `NOT NULL` | Distancia de la ruta entre las dos ciudades, de `raw.routes` |
| `departure_ts` | `timestamptz` | `NOT NULL` | Salida: la del viaje en el primer tramo; en los siguientes, la llegada anterior más la parada |
| `arrival_ts` | `timestamptz` | `NULL` | `departure_ts` más `estimated_hours` de la ruta. Nula si cae después de la fecha de corte |
| `loaded_flag` | `boolean` | `NOT NULL` | `true` si lleva envíos; `false` en el retorno al origen y en los viajes `repositioning` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.13 `raw.shipment_legs`

Un envío en un tramo: fuente del puente entre envíos y viajes. Un envío de un carrier propio ocupa los tramos de su viaje desde el primero hasta el que llega a su destino; los envíos de carriers terceros no tienen filas.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `shipment_id` | `text` | `NOT NULL` | Envío base, de `raw.shipments` |
| `trip_id` | `text` | `NOT NULL` | Viaje, de `raw.trips` |
| `leg_sequence` | `integer` | `NOT NULL` | Tramo, de `raw.trip_legs` junto con `trip_id` |
| `loaded_km` | `numeric(8,1)` | `NOT NULL` | Km del tramo recorridos con el envío: la distancia del tramo |
| `weight_kg` | `numeric(12,3)` | `NOT NULL` | Peso del envío en el tramo: su `ordered_weight_kg` |
| `volume_m3` | `numeric(10,3)` | `NOT NULL` | Volumen del envío: el peso dividido por una densidad de 150 a 450 kg/m³, sorteada por envío |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.14 `raw.shipment_stops`

Una parada de un envío: la recogida, de 0 a 2 centros de tránsito y la entrega.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `shipment_id` | `text` | `NOT NULL` | Envío base, de `raw.shipments` |
| `stop_sequence` | `integer` | `NOT NULL` | Posición de la parada, desde 1 |
| `stop_type` | `text` | `NOT NULL` | `pickup`, `transit_hub` o `delivery` |
| `city` | `text` | `NOT NULL` | Ciudad de la parada: el origen, un centro de tránsito o el destino de la ruta |
| `planned_ts` | `timestamptz` | `NOT NULL` | Plan: `pickup_ts` en la recogida, `promised_delivery_ts` en la entrega y puntos equidistantes entre ambos en los centros. Puede pasar de la fecha de corte |
| `actual_ts` | `timestamptz` | `NULL` | Real: `picked_up_at`, la llegada al centro o `delivered_at`. Nula si no ocurrió o si caería después de la fecha de corte |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.15 `raw.shipment_events`

Un evento del seguimiento de un envío. Fecha de negocio: `event_ts`.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `event_id` | `text` | `NOT NULL` | Identificador del evento (`EVT-000000001`). Se repite solo en los duplicados inyectados |
| `shipment_id` | `text` | `NOT NULL` | Envío base, de `raw.shipments` |
| `event_ts` | `timestamptz` | `NOT NULL` | Instante del evento. Posterior a la fecha de corte solo por defecto inyectado (`impossible_date`) |
| `event_type` | `text` | `NULL` | `created`, `picked_up`, `arrived_at_hub`, `departed_hub`, `in_transit`, `out_for_delivery`, `delivered`, `delivery_failed` o `damage`. Nulo solo por defecto inyectado |
| `location_city` | `text` | `NULL` | Ciudad del evento. Nula solo por defecto inyectado |
| `latitude` | `numeric(9,6)` | `NOT NULL` | Latitud del centro de la ciudad del evento |
| `longitude` | `numeric(9,6)` | `NOT NULL` | Longitud del centro de la ciudad del evento |
| `severity` | `text` | `NOT NULL` | `info`; `warning` en `delivery_failed` y en `damage` de incidencias de severidad `medium`; `critical` en `damage` de severidad `high` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.16 `raw.fuel_transactions`

Una compra de combustible de un viaje. Fecha de negocio: `txn_ts`.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `fuel_txn_id` | `text` | `NOT NULL` | Identificador de la compra (`FUE-00000001`), numerado en orden de viaje |
| `trip_id` | `text` | `NOT NULL` | Viaje, de `raw.trips` |
| `vehicle_id` | `text` | `NOT NULL` | Vehículo del viaje |
| `driver_id` | `text` | `NOT NULL` | Conductor del viaje |
| `txn_ts` | `timestamptz` | `NOT NULL` | Instante de la compra, dentro del tramo en que cae; nunca anterior a `start_ts` del viaje |
| `station_city` | `text` | `NOT NULL` | Ciudad de la estación: la de salida o la de llegada del tramo, la más cercana |
| `gallons` | `numeric(8,2)` | `NULL` | Galones comprados. Nulo solo por defecto inyectado |
| `price_per_gallon_amount` | `numeric(10,2)` | `NOT NULL` | Precio por galón: de 10 200 a 11 400 COP en diésel y de 5 800 a 6 600 COP en gas natural |
| `total_amount` | `numeric(12,2)` | `NULL` | `gallons × price_per_gallon_amount`. Nulo solo por defecto inyectado |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `odometer_km` | `numeric(10,1)` | `NOT NULL` | Odómetro del vehículo en la compra |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.17 `raw.carrier_tenders`

Una oferta de un envío a un transportador. Fecha de negocio: `offered_ts`.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `tender_id` | `text` | `NOT NULL` | Identificador de la oferta (`TND-00000001`) |
| `shipment_id` | `text` | `NOT NULL` | Envío base, de `raw.shipments` |
| `carrier_id` | `text` | `NOT NULL` | Transportador ofertado: el del envío en la oferta aceptada, otro en la previa |
| `offered_ts` | `timestamptz` | `NOT NULL` | Oferta: entre 2 y 48 h antes de `pickup_ts`; la previa, antes que la aceptada |
| `responded_ts` | `timestamptz` | `NULL` | Respuesta, entre `offered_ts` y `pickup_ts`. Nula solo por defecto inyectado (`no_response`) |
| `response_status` | `text` | `NULL` | `accepted`, `rejected` o `expired`. Nulo solo por defecto inyectado (`no_response`) |
| `offered_rate_amount` | `numeric(12,2)` | `NOT NULL` | Tarifa ofrecida, por km de la ruta y por kg del envío |
| `accepted_rate_amount` | `numeric(12,2)` | `NULL` | Tarifa aceptada, del 94 % al 100 % de la ofrecida. Solo en ofertas `accepted` |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

### 2.18 `raw.carrier_incidents`

Una incidencia del transportador en un envío. Fecha de negocio: `reported_ts`.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `incident_id` | `text` | `NOT NULL` | Identificador de la incidencia (`INC-0000001`) |
| `shipment_id` | `text` | `NOT NULL` | Envío base, de `raw.shipments` |
| `carrier_id` | `text` | `NOT NULL` | Transportador del envío |
| `incident_type` | `text` | `NOT NULL` | `delay`, `damage`, `documentation`, `vehicle_breakdown` o `loss` |
| `severity` | `text` | `NOT NULL` | `low`, `medium` o `high`, según el tipo |
| `damage_flag` | `boolean` | `NOT NULL` | `true` solo en `damage`; ese tipo añade un evento `damage` al envío |
| `claim_amount` | `numeric(12,2)` | `NULL` | Reclamo, proporcional al peso del envío. Solo en `damage` y `loss`, y no en todas |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `reported_ts` | `timestamptz` | `NOT NULL` | Reporte, entre la recogida y el fin del envío |
| `responded_ts` | `timestamptz` | `NOT NULL` | Respuesta, de 10 a 1440 minutos después del reporte |
| `resolved_ts` | `timestamptz` | `NULL` | Resolución, de 1 a 14 días después del reporte. Nula si caería después de la fecha de corte o por defecto inyectado (`unclosed`) |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'tms'` |

## 3. Manifiesto de defectos

`data/<perfil>/defect_manifest.json` declara las filas base de cada tabla TMS y los defectos inyectados en `shipments.csv`, `shipment_events.csv`, `fuel_transactions.csv`, `carrier_tenders.csv` y `carrier_incidents.csv`. Es determinista: no guarda fecha de generación, ruta de salida, versión ni `run_id`, y `tables` y `defects` siguen un orden fijo.

| Campo | Tipo | Descripción |
|---|---|---|
| `scale` | string | Perfil: `demo`, `local` o `benchmark` |
| `seed` | integer | Semilla usada |
| `months` | integer | Meses de historia generados |
| `cutoff_date` | string | Fecha de corte, ISO 8601 |
| `shipments` | integer | Envíos base, sin contar duplicados |
| `tables` | object | Filas base de cada una de las 14 tablas TMS, sin las copias inyectadas, en el orden de publicación de §1.1 |
| `defects[]` | array | Una entrada por tabla, tipo de defecto y columna |
| `defects[].table` | string | Tabla del defecto, sin esquema (`shipments`) |
| `defects[].type` | string | `duplicate`, `critical_null`, `delivered_before_pickup`, `impossible_date`, `no_response` o `unclosed` |
| `defects[].column` | string | Columna afectada |
| `defects[].count` | integer | Filas afectadas, contadas tal como se inyectaron |
| `defects[].rate` | number | `count` dividido por el denominador de su tipo (tabla siguiente), redondeado a 5 decimales |
| `defects[].rule_id` | string o `null` | Regla de [`06_quality_rules.md`](06_quality_rules.md) que lo detecta; `null` si el catálogo aún no tiene regla para ese defecto |
| `defects[].expected_action` | string o `null` | `quarantine` o `alert`; `null` sin regla |

Defectos configurados. Cada fila afectada lleva un solo defecto. Viajes, tramos, `shipment_legs`, paradas, vehículos, conductores y dimensiones van limpios.

| `table` | `type` | `column` | Tasa | Denominador de `rate` | `rule_id` | `expected_action` | Qué se inyecta |
|---|---|---|---|---|---|---|---|
| `shipments` | `duplicate` | `shipment_id` | 2 % | Envíos base | DQ-001 | `quarantine` | Copia idéntica de una fila limpia, escrita justo después del original y por encima de la base |
| `shipments` | `critical_null` | `promised_delivery_ts` | 1/3 del 1 % | Envíos base | DQ-011 | `alert` | Valor vacío |
| `shipments` | `critical_null` | `customer_id` | 1/3 del 1 % | Envíos base | DQ-005 | `alert` | Valor vacío |
| `shipments` | `critical_null` | `carrier_id` | 1/3 del 1 % | Envíos base | DQ-004 | `alert` | Valor vacío |
| `shipments` | `delivered_before_pickup` | `delivered_at` | 0,8 % | Envíos base | DQ-002 | `quarantine` | `delivered_at` movido entre 30 min y 48 h antes de `picked_up_at`, solo en envíos entregados |
| `shipment_events` | `duplicate` | `event_id` | 2 % | Eventos base | DQ-001 | `quarantine` | Copia idéntica del evento, escrita justo después del original |
| `shipment_events` | `critical_null` | `event_type` | 1/2 del 1 % | Eventos base | `null` | `null` | Valor vacío |
| `shipment_events` | `critical_null` | `location_city` | 1/2 del 1 % | Eventos base | `null` | `null` | Valor vacío |
| `shipment_events` | `impossible_date` | `event_ts` | 0,5 % | Eventos base | `null` | `null` | `event_ts` más 36 525 días, posterior a la fecha de corte |
| `fuel_transactions` | `critical_null` | `gallons` | 1/2 del 1 % | Compras base | `null` | `null` | Valor vacío |
| `fuel_transactions` | `critical_null` | `total_amount` | 1/2 del 1 % | Compras base | `null` | `null` | Valor vacío |
| `carrier_tenders` | `no_response` | `response_status` | 2 % | Ofertas base | DQ-014 | `alert` | `responded_ts`, `response_status` y `accepted_rate_amount` vacíos |
| `carrier_incidents` | `unclosed` | `resolved_ts` | 1 % | Incidencias elegibles: las de `reported_ts` anterior a 30 días antes del final de la fecha de corte (`2026-11-09T23:59:59+00:00`) | `null` | `null` | `resolved_ts` vacío |

En los envíos, los defectos se sortean sobre índices y sus conteos son exactos: el 1 % de nulos se reparte en tercios que difieren como mucho en una fila. En las demás tablas se sortea un uniforme por fila base, en orden de escritura, con tramos excluyentes, así que los conteos son los realizados y quedan cerca de la tasa; los nulos de cada tabla alternan entre sus dos columnas. Solo las incidencias elegibles sortean `unclosed`: una incidencia reciente aún puede estar abierta sin ser un defecto, y una con `resolved_ts` posterior a la fecha de corte queda nula sin contarse (§1). Las entradas con `rule_id` nulo esperan su regla en el catálogo de calidad.

Cada entrada se reproduce con una consulta sobre `tms`, con `cut = timestamptz '2026-11-09 23:59:59+00'`:

| Entrada | Consulta |
|---|---|
| `duplicate` | `count(*) - count(distinct <column>)` de la tabla |
| `critical_null` | `count(*) filter (where <column> is null)` |
| `delivered_before_pickup` | `count(*) filter (where delivered_at < picked_up_at)` de `tms.shipments` |
| `impossible_date` | `count(*) filter (where event_ts > cut)` de `tms.shipment_events` |
| `no_response` | `count(*) filter (where response_status is null)` de `tms.carrier_tenders` |
| `unclosed` | `count(*) filter (where resolved_ts is null and reported_ts < cut - interval '30 days')` de `tms.carrier_incidents` |

Con `demo` (50 000 envíos base y la semilla por defecto), `shipments.csv` tiene 51 000 filas, `shipment_events.csv` tiene 512 686 (502 535 eventos base más 10 151 copias) y el manifiesto declara:

| `table` | `type` | `column` | `count` | `rate` |
|---|---|---|---|---|
| `shipments` | `duplicate` | `shipment_id` | 1000 | 0.02 |
| `shipments` | `critical_null` | `promised_delivery_ts` | 167 | 0.00334 |
| `shipments` | `critical_null` | `customer_id` | 167 | 0.00334 |
| `shipments` | `critical_null` | `carrier_id` | 166 | 0.00332 |
| `shipments` | `delivered_before_pickup` | `delivered_at` | 400 | 0.008 |
| `shipment_events` | `duplicate` | `event_id` | 10151 | 0.0202 |
| `shipment_events` | `critical_null` | `event_type` | 2475 | 0.00493 |
| `shipment_events` | `critical_null` | `location_city` | 2474 | 0.00492 |
| `shipment_events` | `impossible_date` | `event_ts` | 2488 | 0.00495 |
| `fuel_transactions` | `critical_null` | `gallons` | 315 | 0.00469 |
| `fuel_transactions` | `critical_null` | `total_amount` | 314 | 0.00467 |
| `carrier_tenders` | `no_response` | `response_status` | 1484 | 0.0198 |
| `carrier_incidents` | `unclosed` | `resolved_ts` | 40 | 0.00831 |

En `demo`, la base de `unclosed` son 4 813 incidencias elegibles de 5 036.

## 4. Linaje `tms` → `raw` → `stg` → `core`

Las entidades TMS llegan a `raw` por extracción de `tms.<tabla>` por watermark, y los clientes por carga de `customers.csv` por hash (§2.2). Desde `raw`, la transformación la hace dbt. Las relaciones de `stg` son vistas; las de `quarantine` y `core` son tablas que se reconstruyen en cada `dbt build`.

```text
tms.shipments ─► raw.shipments ─► stg_tms__shipments ─► stg_tms__shipments_validated ─┬─► stg_tms__shipments_valid ─► core.fact_shipments
                                                                                      └─► quarantine.shipments
customers.csv     ─► raw.customers     ─► stg_erp__customers     ─► core.dim_customer      ─┐
tms.carriers      ─► raw.carriers      ─► stg_tms__carriers      ─► core.dim_carrier       ─┤
tms.routes        ─► raw.routes        ─► stg_tms__routes        ─► core.dim_route         ─┼─► core.fact_shipments
tms.service_types ─► raw.service_types ─► stg_tms__service_types ─► core.dim_service_type  ─┘
stg_tms__shipments_valid ─► core.dim_date
```

Las diez tablas de la flota y de la operación aterrizan en `raw` y todavía no tienen staging ni modelos en dbt:

```text
tms.vehicles          ─► raw.vehicles
tms.drivers           ─► raw.drivers
tms.trips             ─► raw.trips
tms.trip_legs         ─► raw.trip_legs
tms.shipment_legs     ─► raw.shipment_legs
tms.shipment_stops    ─► raw.shipment_stops
tms.shipment_events   ─► raw.shipment_events
tms.fuel_transactions ─► raw.fuel_transactions
tms.carrier_tenders   ─► raw.carrier_tenders
tms.carrier_incidents ─► raw.carrier_incidents
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
tms.shipments ─► raw.shipments ─► stg.stg_tms__shipments ─► stg.stg_tms__shipments_validated ─► stg.stg_tms__shipments_valid
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

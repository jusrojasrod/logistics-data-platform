# Diccionario de datos

Describe los archivos que produce el generador sintético, el esquema `tms` que simula el sistema de gestión de transporte, las tablas del esquema `raw` donde aterrizan los datos y las tablas de control de la ingesta en `meta`. A `raw` se llega por dos vías:

- **TMS:** los CSV de las entidades TMS (envíos, dimensiones, flota, viajes, tramos, paradas, eventos, combustible, ofertas a transportadores e incidencias) se publican en `tms.<tabla>`, y el conector batch los extrae de ahí a `raw.<tabla>` por watermark de `updated_at`.
- **Archivos ERP y CRM:** `customers.csv` y los nueve CSV del ERP (`tariffs`, `invoices`, `invoice_lines`, `payments`, `carrier_settlements` y `shipment_costs`) y del CRM (`contacts`, `contracts` y `leads`) llegan como archivos a `raw.<tabla>` y se identifican por el SHA-256 de su contenido. Cerca del 3 % de las filas de esas nueve entidades llega aparte, en un archivo tardío de la subcarpeta `late/` que se carga después (§1 y §2.2).

Los tipos y la nulabilidad de `tms` y `raw` son los del DDL explícito de los contratos: no se infieren de los CSV.

## 1. Archivos de origen

El generador escribe un CSV por entidad y un manifiesto de defectos en `data/<perfil>/`:

```bash
make data SCALE=demo        # o SCALE=local, o SCALE=benchmark
```

Los CSV de las entidades TMS no se cargan como archivos en `raw`: se publican en el esquema `tms` (§1.1). Los CSV del ERP (`customers.csv` y seis entidades financieras) y del CRM (tres entidades comerciales) llegan como archivos a `raw` (§2.2).

Las nueve entidades ERP y CRM nuevas se reparten en dos archivos con la misma cabecera: `<entidad>.csv`, en la carpeta del perfil, y `late/<entidad>_late.csv`, en su subcarpeta `late/`, con las filas que llegan a la zona de aterrizaje después del resto. Con `x` el entero de los primeros 8 dígitos hexadecimales del SHA-256 de la clave natural de la fila, la fila va al archivo tardío si `x % 100 < 3`, y si no, al principal. La clave es la de la tabla (§2.19 a §2.27); en `tariffs`, que tiene clave compuesta, sus dos columnas unidas por `|` (`tariff_id|valid_from`). La regla depende solo de la clave: cada fila vive en un solo archivo, los dos archivos se escriben aunque uno quede vacío y la proporción tardía es la que resulta del hash, cerca del 3 % y más lejos en las tablas pequeñas. `customers.csv` no tiene archivo tardío. Por eso los ids de un archivo principal tienen huecos: las filas que faltan están en el tardío.

El perfil `benchmark` (2 000 000 envíos base) solo se genera a archivos, para medir la generación: el publicador rechaza su carpeta (§1.1) y nunca llega a `tms` ni a `raw`. El generador escribe en flujo, fila a fila, sin acumular filas en memoria: lo que retiene crece con el número de envíos base y de viajes, no con el de eventos; de las entidades ERP y CRM solo retiene tablas que no crecen con los envíos: los catálogos, las del CRM y las versiones de tarifa.

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
| `tariffs.csv` | Tarifas por cliente | Una fila por versión de tarifa (`tariff_id`, `valid_from`) | 1 800 (12 por cliente) | `raw.tariffs` | `erp` |
| `contacts.csv` | Contactos de clientes | Una fila por contacto (`contact_id`) | 450 (3 por cliente) | `raw.contacts` | `crm` |
| `contracts.csv` | Contratos de clientes | Una fila por contrato (`contract_id`) | 150 (1 por cliente) | `raw.contracts` | `crm` |
| `leads.csv` | Oportunidades comerciales | Una fila por oportunidad (`lead_id`) | 500 | `raw.leads` | `crm` |
| `invoices.csv` | Facturas | Una fila por cabecera de factura (`invoice_id`) | 46 152 en `demo` (una por envío entregado y facturado) | `raw.invoices` | `erp` |
| `invoice_lines.csv` | Líneas de factura | Una fila por línea (`invoice_line_id`) | 92 304 en `demo` (2 por factura) | `raw.invoice_lines` | `erp` |
| `payments.csv` | Pagos de clientes | Una fila por pago (`payment_id`) | 40 013 en `demo` (a lo sumo 1 por factura) | `raw.payments` | `erp` |
| `carrier_settlements.csv` | Liquidaciones a transportadores | Una fila por línea de liquidación (`settlement_line_id`): envío × carrier × concepto | 22 577 en `demo` | `raw.carrier_settlements` | `erp` |
| `shipment_costs.csv` | Costos de la flota propia | Una fila por envío y componente de costo (`shipment_cost_id`) | 75 198 en `demo` (3 por envío de la flota propia) | `raw.shipment_costs` | `erp` |
| `late/<entidad>_late.csv` | Filas tardías de las nueve entidades anteriores | Las de su entidad | Cerca del 3 % de su entidad (§3, campo `late_files`) | `raw.<entidad>` | El de su entidad |
| `defect_manifest.json` | Defectos inyectados | Una entrada por tabla, tipo de defecto y columna | 13 entradas | — | — |

Los conteos de `demo` son los del manifiesto (§3, campos `tables` y `late_files`) con la semilla por defecto; en las nueve entidades ERP y CRM suman el archivo principal y el tardío. En `local` y `benchmark` las entidades por envío crecen en la misma proporción que los envíos base, y vehículos, conductores, contactos, contratos y oportunidades no cambian; las tarifas dependen de las combinaciones de ruta y servicio de cada cliente y llegan a lo sumo a 12 por cliente.

Convenciones comunes de los CSV:

- UTF-8, separador coma, fin de línea LF y fila de cabecera con el orden de columnas de este documento.
- Un campo vacío es un nulo.
- Marcas de tiempo en ISO 8601 con desplazamiento UTC explícito (`2026-02-27T14:08:00+00:00`); fechas en `YYYY-MM-DD`.
- Booleanos como `true` o `false`.
- La historia cubre 24 meses completos que terminan en la fecha de corte fija `2026-11-09`. Con el mismo perfil y la misma semilla, los archivos son idénticos byte a byte.
- Los ids de las dimensiones y de la flota dependen solo de la posición en el catálogo. Todo id no nulo de `shipments.csv` existe en el catálogo de la misma corrida: no se generan huérfanos.
- Las entidades ERP y CRM tampoco generan huérfanos, contando el archivo principal y el tardío juntos: todo `customer_id`, `route_id`, `service_type_id` y `carrier_id` existe en su catálogo, todo `shipment_id` en `shipments.csv` y todo `invoice_id` de líneas y pagos en las facturas. Van limpias: no tienen defectos inyectados ni duplicados.
- Las entidades que cuelgan de un envío (paradas, eventos, ofertas, incidencias y `shipment_legs`) se derivan de su fila base limpia, antes de inyectar los defectos de envíos, y solo referencian `shipment_id` que existen en `shipments.csv`. Todo `trip_id` de tramos, `shipment_legs` y combustible existe en `trips.csv`, y todo tramo de `shipment_legs` existe en `trip_legs.csv`.
- Montos en pesos colombianos: columnas `_amount` con `currency_code = 'COP'`.

Reglas de las entidades operativas:

1. **Viajes, tramos y combustible** existen solo para la flota propia (los 10 carriers `owned`). Los envíos base de un carrier propio con el mismo origen y la misma `pickup_date` viajan juntos en un viaje `delivery`: sale del origen a las 05:00 hora local más 0 a 59 minutos, visita los destinos ordenados por distancia desde el origen (a igual distancia, por posición en el catálogo de ciudades), se detiene de 30 a 90 minutos en cada destino y vuelve vacío al origen (`loaded_flag = false`). Distancias y horas de cada tramo salen del catálogo de rutas. Vehículo y conductor se sortean dentro del carrier. Tras cerca del 3 % de los viajes con carga hay un viaje `repositioning` de un solo tramo vacío y sin envíos.
2. Un envío ocupa los tramos de su viaje desde el primero hasta el que llega a su destino (`shipment_legs`). Los envíos de carriers terceros no tienen viaje ni `shipment_legs`.
3. Cada viaje hace `max(1, ceil(km planificados / 600))` compras de combustible, repartidas a partes iguales a lo largo de su recorrido planificado; `gallons` es el tramo de recorrido entre compras dividido por el rendimiento del vehículo, y el odómetro de cada vehículo se acumula en orden de viaje con la distancia generada.
4. **Paradas:** recogida, de 0 a 2 centros de tránsito y entrega. **Eventos:** `created`, `picked_up`, llegada y salida de cada centro de tránsito, `in_transit` de relleno, `out_for_delivery` y `delivered` o `delivery_failed` si el envío cerró, y `damage` si tiene una incidencia de daño. **Ofertas:** la mitad de los envíos tiene una oferta previa a otro carrier (`rejected` o `expired`) y todos tienen la oferta `accepted` al `carrier_id` del envío, ofrecida entre 2 y 48 h antes de `pickup_ts` y respondida antes de esa hora. **Incidencias:** cerca del 10 % de los envíos, reportadas entre la recogida y el fin del envío, respondidas de 10 a 1440 minutos después y resueltas de 1 a 14 días después del reporte.
5. **Fecha de corte:** ninguna marca observada de una fila limpia pasa del final de la fecha de corte (`2026-11-09T23:59:59+00:00`). Si una marca obligatoria (`start_ts`, `departure_ts`, `event_ts`, `txn_ts`, `offered_ts`, `reported_ts` o el `responded_ts` de una incidencia) caería después, la fila no se genera: un tramo omitido se lleva sus `shipment_legs` y su combustible, y una incidencia omitida, su evento `damage`; distancia, km vacíos, minutos detenido y galones del viaje suman solo sus tramos generados. Si caería después una marca nulable (`end_ts`, `arrival_ts`, `actual_ts`, `resolved_ts`), queda nula: la operación sigue en curso. `planned_ts` de las paradas es un plan, como `promised_delivery_ts`, y puede pasar de la fecha de corte. Las únicas filas con una marca observada posterior son los eventos con `impossible_date` (§3).
6. Simplificaciones: no se controla que un vehículo haga dos viajes a la vez ni su capacidad, y los horarios del viaje no se concilian con `delivered_at` del envío.

Reglas de las entidades ERP y CRM. Se derivan de la fila base limpia de cada envío, antes de inyectar sus defectos, y de los catálogos; las fechas son fechas UTC y ninguna fecha observada (emisión, pago, liquidación, costo, creación o inicio de vigencia) pasa de la fecha de corte; `due_date` y `expected_close_date` son planes y pueden pasarla:

1. **Tarifas:** cada cliente tiene tarifa para las 6 primeras combinaciones distintas de ruta y tipo de servicio de sus envíos base, en orden de generación, con dos versiones por combinación (§2.19).
2. **Facturación:** cada envío entregado tiene una factura con dos líneas, `freight` y `fuel_surcharge`, fechada de 0 a 3 días después de `delivered_at`; si esa fecha pasa de la fecha de corte, la factura aún no existe. Los envíos no entregados no se facturan. No hay notas crédito (§2.23 y §2.24).
3. **Pagos:** cada factura recibe, con probabilidad 0,95, un pago por su total; si no lo recibe, o si la fecha del pago pasaría de la fecha de corte, no tiene pago y queda `issued` (§2.25).
4. **Liquidaciones y costos de flota:** un envío de un carrier tercero (`third_party`) entregado tiene una línea de liquidación al transportador, y un envío de la flota propia (`owned`), entregado o no, tiene tres filas de costo. Un envío nunca tiene las dos cosas, así que su costo no se cuenta dos veces (§2.26 y §2.27).
5. **CRM:** cada cliente tiene 3 contactos y 1 contrato activo; las 500 oportunidades se reparten entre los clientes existentes (§2.20 a §2.22). Los datos de contacto son ficticios: nombres de los mismos catálogos de nombres inventados de los conductores, documentos con el prefijo `NXD-`, teléfonos `+57 000` y correos `@example.com`.

### 1.1 Esquema `tms`

`tms` simula la base operativa del TMS. El publicador inserta en ella los CSV TMS de una carpeta, conectado como `tms_writer` (`NEXORA_TMS_DSN` y `TMS_WRITER_PASSWORD`, del entorno o de `.env`):

```bash
python -m nexora.generators.tms_publisher --input data/demo                      # todas las filas
python -m nexora.generators.tms_publisher --input data/demo --until 2026-11-03   # hasta una fecha de corte
bash scripts/seed_db.sh demo                                                     # genera data/demo y publica todo
```

`scripts/seed_db.sh` genera el perfil, publica sin fecha de corte y después carga en `raw` los archivos ERP y CRM de la carpeta y, a continuación, los de `late/` (§2.2), conectado como `etl_writer`; no toca las tablas TMS de `raw`, que llegan por extracción. `--schema` cambia el esquema de destino del publicador (por defecto `tms`) y `--dsn` la conexión.

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
- `--source files --input <carpeta>`: carga los archivos ERP y CRM de la carpeta en la tabla `raw` de su entidad. Recorre las diez tablas de archivo en este orden (`customers`, `tariffs`, `contacts`, `contracts`, `leads`, `invoices`, `invoice_lines`, `payments`, `carrier_settlements` y `shipment_costs`) y, de cada una, los archivos `<tabla>*.csv` del primer nivel de la carpeta, en orden de nombre, omitiendo los que ya están registrados por su hash (§2.2). No entra en subcarpetas: los tardíos se cargan con una segunda ejecución sobre `<carpeta>/late`. El patrón no mezcla tablas: `invoices*.csv` no recoge `invoice_lines.csv`, ni `shipment_costs*.csv` recoge `shipments.csv`.

En Airflow, `dag_ingest_batch` ejecuta las dos vías con el calendario `0 2 * * *`, en las tareas `extract_tms` y `load_erp_files`; `load_erp_files` carga la carpeta `NEXORA_DATA_DIR/NEXORA_SCALE` y después su subcarpeta `late/`, y falla si `late/` no existe (§2.2).

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
| `raw.tariffs` | `tariffs.csv` y `late/tariffs_late.csv` | Archivo por hash | `erp` | 10 + 5 = 15 |
| `raw.contacts` | `contacts.csv` y `late/contacts_late.csv` | Archivo por hash | `crm` | 8 + 5 = 13 |
| `raw.contracts` | `contracts.csv` y `late/contracts_late.csv` | Archivo por hash | `crm` | 8 + 5 = 13 |
| `raw.leads` | `leads.csv` y `late/leads_late.csv` | Archivo por hash | `crm` | 8 + 5 = 13 |
| `raw.invoices` | `invoices.csv` y `late/invoices_late.csv` | Archivo por hash | `erp` | 8 + 5 = 13 |
| `raw.invoice_lines` | `invoice_lines.csv` y `late/invoice_lines_late.csv` | Archivo por hash | `erp` | 9 + 5 = 14 |
| `raw.payments` | `payments.csv` y `late/payments_late.csv` | Archivo por hash | `erp` | 7 + 5 = 12 |
| `raw.carrier_settlements` | `carrier_settlements.csv` y `late/carrier_settlements_late.csv` | Archivo por hash | `erp` | 11 + 5 = 16 |
| `raw.shipment_costs` | `shipment_costs.csv` y `late/shipment_costs_late.csv` | Archivo por hash | `erp` | 8 + 5 = 13 |

Ninguna tabla `raw` tiene restricción `unique`: las copias que llegan de la fuente se conservan. Las tablas de archivo se crean en la primera carga, con el DDL del contrato. `raw.contacts` tiene datos personales ficticios: su clasificación está en [`05_governance_policy.md`](05_governance_policy.md) §4.

### 2.1 Metadatos de ejecución

Los conectores añaden estas columnas a toda fila de todas las tablas `raw`, en este orden y detrás de las columnas de negocio.

| Columna | Tipo | Nulabilidad | Extracción de `tms` | Archivo ERP o CRM |
|---|---|---|---|---|
| `run_id` | `text` | `NOT NULL` | Identificador de la ejecución del conector: por defecto, un UUID nuevo por ejecución (`--run-id` lo fija). Todas las tablas de una misma ejecución comparten el valor | Igual |
| `source_system` | `text` | `NOT NULL` | `tms` | `erp`; `crm` en `contacts`, `contracts` y `leads` |
| `source_file` | `text` | `NOT NULL` | Tabla de origen, `<esquema>.<tabla>` (`tms.shipments`) | Nombre del archivo cargado (`customers.csv`, `invoices_late.csv`), nunca una ruta: un tardío se registra sin `late/` |
| `source_file_hash` | `text` | `NOT NULL` | SHA-256 en hexadecimal (64 caracteres) del lote extraído: las filas de negocio de la ventana como `row(...)::text`, unidas por salto de línea en orden de `tms_row_id` y calculado en UTC. Todas las filas de un lote comparten el valor | SHA-256 en hexadecimal (64 caracteres) del contenido del archivo |
| `ingested_at` | `timestamptz` | `NOT NULL` | Instante de la ejecución; el mismo para todas las tablas de una ejecución | Igual |

Cálculo del hash del lote: el conector lo calcula en el cliente, sin concatenar la ventana en el servidor. Un cursor de servidor lee la ventana por bloques de 10 000 filas (`select row(<columnas de negocio>)::text … order by tms_row_id`, dentro de la transacción de la extracción y ya en UTC) y el SHA-256 recibe cada fila en UTF-8, con un salto de línea entre filas. El resultado es el mismo que el de concatenar en el servidor las filas en ese orden con `string_agg(…, E'\n' order by tms_row_id)` y aplicar SHA-256: los lotes ya cargados siguen siendo verificables con esa consulta. La memoria del cliente depende del tamaño del bloque, no del de la ventana. El número de filas del recorrido se compara con el de filas insertadas en `raw`.

`run_id` e `ingested_at` cambian entre ejecuciones; no forman parte de los archivos de origen, de `tms` ni del manifiesto. En `dag_ingest_batch`, `extract_tms` y `load_erp_files` son ejecuciones distintas y cada una tiene su `run_id`; dentro de `load_erp_files`, la carga de la carpeta y la de `late/` también son ejecuciones distintas, igual que en `scripts/seed_db.sh`.

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

**Archivos ERP y CRM, por hash.** Cada archivo se carga en una sola transacción. La cabecera del CSV debe coincidir exactamente con el orden de columnas del contrato; si no, la carga falla. El conector calcula el SHA-256 del contenido, toma un bloqueo consultivo del registro de archivos de la tabla y busca el par `(tabla, hash)` en `meta.ingested_files` (§2.8):

- Si está registrado, omite el archivo y no inserta filas. Una copia renombrada de un archivo ya cargado tiene el mismo hash y también se omite.
- Si no, copia el archivo a una tabla temporal tipada con el contrato, lo inserta en `raw.<tabla>` con los metadatos de §2.1 y lo registra en `meta.ingested_files`, en la misma transacción. Un archivo modificado tiene un hash nuevo y se carga.

Si la carpeta de una ejecución no existe, la carga falla con `FileNotFoundError` (`no existe la carpeta <carpeta>`) antes de cargar nada; una carpeta sin archivos de ninguna tabla no carga nada.

**Archivos tardíos.** El principal y el tardío de una entidad (`invoices.csv` y `late/invoices_late.csv`) son archivos distintos, con hashes distintos: `meta.ingested_files` registra los dos para la misma tabla, y `raw.<tabla>` acaba con las filas de ambos. `scripts/seed_db.sh` y `load_erp_files` cargan primero la carpeta y después `late/`, en dos ejecuciones con `run_id` distintos; repetir cualquiera de las dos omite sus archivos. Si falta `late/`, la segunda carga falla con `FileNotFoundError` tras haber cargado la carpeta principal: pasa con una carpeta generada por una versión del generador anterior a los tardíos, y se corrige generándola de nuevo (`scripts/seed_db.sh` o `make data`).

Como cada fila vive en un solo archivo, la carga del tardío no repite filas de la principal. En cambio, entre las dos cargas, o si el tardío se retrasa, una fila hija puede estar en `raw` antes que su padre: por ejemplo, una línea de factura o un pago cuya factura está en `invoices_late.csv`. Cuando las dos cargas terminan, no quedan huérfanos entre las tablas ERP y CRM. Las capas siguientes no deben suponer que padre e hijo comparten `run_id` ni archivo.

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

`meta.ingested_files`: una fila por archivo ERP o CRM cargado, identificado por su contenido. Tras cargar la carpeta y `late/`, cada una de las nueve tablas con tardíos tiene dos filas: la del principal y la del tardío.

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

### 2.19 `raw.tariffs`

Una versión de la tarifa de un cliente para una ruta y un tipo de servicio. Clave natural: `tariff_id`, `valid_from`. Cada cliente tiene tarifa para las 6 primeras combinaciones distintas de ruta y servicio de sus envíos base (menos si tiene menos), con dos versiones por combinación que comparten `tariff_id`. La versión vigente en un día `d` es la que cumple `valid_from <= d` y `d < valid_to`, o `valid_to` nulo.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `tariff_id` | `text` | `NOT NULL` | Identificador de la tarifa de una combinación (`TRF-00001`); se repite en sus dos versiones |
| `customer_id` | `text` | `NOT NULL` | Cliente, de `raw.customers` |
| `route_id` | `text` | `NOT NULL` | Ruta, de `raw.routes` |
| `service_type_id` | `text` | `NOT NULL` | Tipo de servicio, de `raw.service_types` |
| `valid_from` | `date` | `NOT NULL` | Inicio de la vigencia, incluido: el primer día de la ventana de historia en la primera versión y un día sorteado dentro de la ventana en la segunda |
| `valid_to` | `date` | `NULL` | Fin de la vigencia, excluido: el `valid_from` de la versión siguiente. Nulo en la última versión, que sigue vigente |
| `base_amount` | `numeric(14,2)` | `NOT NULL` | Cargo fijo por envío. La segunda versión sube de un 3 % a un 8 % sobre la primera |
| `per_km_amount` | `numeric(14,2)` | `NOT NULL` | Cargo por km de la ruta, con la misma subida que `base_amount` |
| `fuel_surcharge_pct` | `numeric(5,2)` | `NOT NULL` | Recargo de combustible sobre el flete, en porcentaje (de 6 a 14); igual en las dos versiones |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'erp'` |

Las vigencias de una combinación no se solapan ni dejan huecos desde el inicio de la ventana. Las dos versiones de una tarifa pueden quedar en archivos distintos, porque la clave del reparto tardío incluye `valid_from`.

### 2.20 `raw.contacts`

Un contacto de un cliente en el CRM. Clave natural: `contact_id`. Tres por cliente, uno por rol. Contiene datos personales ficticios: ver [`05_governance_policy.md`](05_governance_policy.md) §4.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `contact_id` | `text` | `NOT NULL` | Identificador del contacto (`CNT-00001`) |
| `customer_id` | `text` | `NOT NULL` | Cliente, de `raw.customers` |
| `customer_contact_name` | `text` | `NOT NULL` | Nombre y apellido ficticios, combinados por posición de los mismos catálogos de nombres inventados que los conductores. PII |
| `contact_role` | `text` | `NOT NULL` | `logistics_manager`, `procurement` o `accounts_payable` |
| `contact_document` | `text` | `NOT NULL` | Documento ficticio con el prefijo `NXD-` y 7 dígitos desde `NXD-5000001`, en un rango que no se solapa con el de los conductores. PII |
| `customer_phone` | `text` | `NOT NULL` | Teléfono ficticio con el prefijo `+57 000` (`+57 000 500 0001`). PII |
| `customer_email` | `text` | `NOT NULL` | Correo ficticio en el dominio reservado `example.com` (`cnt00001@example.com`). PII |
| `city` | `text` | `NOT NULL` | Ciudad del cliente |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'crm'` |

### 2.21 `raw.contracts`

El contrato de servicio de un cliente en el CRM. Clave natural: `contract_id`. Uno por cliente; los valores dependen solo de la posición del cliente en el catálogo.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `contract_id` | `text` | `NOT NULL` | Identificador del contrato (`CTR-0001`) |
| `customer_id` | `text` | `NOT NULL` | Cliente, de `raw.customers` |
| `service_type_id` | `text` | `NOT NULL` | Tipo de servicio contratado, de `raw.service_types`, rotando por posición del cliente entre los 6 del catálogo |
| `start_date` | `date` | `NOT NULL` | Inicio del contrato: el primer día de la ventana de historia o hasta 720 días antes |
| `end_date` | `date` | `NULL` | Fin del contrato. Nulo en todos: los contratos siguen vigentes |
| `payment_term_days` | `integer` | `NOT NULL` | Plazo de pago, el del cliente en `raw.customers` |
| `otif_target_pct` | `numeric(5,2)` | `NOT NULL` | Meta de OTIF del contrato, en porcentaje: `92.00`, `95.00` o `97.00` |
| `contract_status` | `text` | `NOT NULL` | `active` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'crm'` |

### 2.22 `raw.leads`

Una oportunidad comercial del CRM sobre un cliente existente. Clave natural: `lead_id`. Fecha de negocio: `created_date`. Son 500 en todos los perfiles.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `lead_id` | `text` | `NOT NULL` | Identificador de la oportunidad (`LEA-0001`) |
| `customer_id` | `text` | `NOT NULL` | Cliente, de `raw.customers`, sorteado entre los del catálogo |
| `created_date` | `date` | `NOT NULL` | Creación, dentro de la ventana de historia |
| `lead_source` | `text` | `NOT NULL` | `referral`, `website`, `trade_show`, `outbound` o `partner` |
| `stage` | `text` | `NOT NULL` | `prospect`, `qualified`, `proposal`, `negotiation`, `won` o `lost` |
| `estimated_value_amount` | `numeric(14,2)` | `NOT NULL` | Valor estimado, entre 5 000 000 y 250 000 000 |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `expected_close_date` | `date` | `NULL` | Cierre esperado, de 15 a 120 días después de `created_date`; es un plan y puede pasar de la fecha de corte. Nulo exactamente cuando `stage = 'lost'` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'crm'` |

### 2.23 `raw.invoices`

La cabecera de la factura de un envío entregado. Clave natural: `invoice_id`. Fecha de negocio: `invoice_date`. Un envío entregado tiene a lo sumo una factura; no la tiene si su `invoice_date` pasaría de la fecha de corte. No hay notas crédito: una factura emitida no se anula ni se corrige.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `invoice_id` | `text` | `NOT NULL` | Identificador de la factura (`INV-00000001`) |
| `customer_id` | `text` | `NOT NULL` | Cliente del envío, de `raw.customers` |
| `shipment_id` | `text` | `NOT NULL` | Envío facturado, de `raw.shipments` |
| `invoice_date` | `date` | `NOT NULL` | Emisión: la fecha UTC de `delivered_at` más 0 a 3 días |
| `due_date` | `date` | `NOT NULL` | Vencimiento: `invoice_date` más `payment_term_days` del cliente; puede pasar de la fecha de corte |
| `invoice_status` | `text` | `NOT NULL` | `paid` si la factura tiene pago en `raw.payments`; `issued` si no |
| `total_amount` | `numeric(14,2)` | `NOT NULL` | Suma de `billed_revenue_amount + tax_amount` de sus líneas |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'erp'` |

### 2.24 `raw.invoice_lines`

Una línea de factura: el grano de la facturación. Clave natural: `invoice_line_id`. Cada factura tiene dos líneas: `freight` (`line_number = 1`) y `fuel_surcharge` (`line_number = 2`).

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `invoice_line_id` | `text` | `NOT NULL` | Identificador de la línea (`INVL-000000001`) |
| `invoice_id` | `text` | `NOT NULL` | Factura, de `raw.invoices` |
| `shipment_id` | `text` | `NOT NULL` | Envío de la factura, de `raw.shipments` |
| `line_number` | `integer` | `NOT NULL` | Posición de la línea en la factura: `1` o `2` |
| `concept` | `text` | `NOT NULL` | `freight` o `fuel_surcharge` |
| `tariff_amount` | `numeric(14,2)` | `NOT NULL` | Importe según tarifa. Flete: `base_amount + per_km_amount × distance_km` de la versión de `raw.tariffs` vigente en la `pickup_date` del envío, o del precio de lista (320 000 fijo, 4 600 por km y 12 % de recargo) si el cliente no tiene tarifa para esa ruta y servicio. Recargo: el flete por `fuel_surcharge_pct / 100` |
| `billed_revenue_amount` | `numeric(14,2)` | `NOT NULL` | Importe facturado: igual a `tariff_amount`, salvo en cerca del 10 % de las líneas, que se desvían de un 1 % a un 8 % hacia arriba o hacia abajo |
| `tax_amount` | `numeric(14,2)` | `NOT NULL` | Impuesto sintético del 19 % sobre `billed_revenue_amount` |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'erp'` |

### 2.25 `raw.payments`

Un pago de un cliente. Clave natural: `payment_id`. Fecha de negocio: `paid_date`. Una factura tiene a lo sumo un pago, por su total.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `payment_id` | `text` | `NOT NULL` | Identificador del pago (`PAY-00000001`) |
| `invoice_id` | `text` | `NOT NULL` | Factura pagada, de `raw.invoices` |
| `customer_id` | `text` | `NOT NULL` | Cliente de la factura, de `raw.customers` |
| `paid_date` | `date` | `NOT NULL` | Pago: `due_date` más −10 a +20 días, o `invoice_date` si esa fecha caería antes. Nunca pasa de la fecha de corte: un pago que caería después no existe aún |
| `payment_amount` | `numeric(14,2)` | `NOT NULL` | Igual al `total_amount` de la factura |
| `payment_method` | `text` | `NOT NULL` | `bank_transfer`, `check` o `card` |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'erp'` |

### 2.26 `raw.carrier_settlements`

Una línea de liquidación a un transportador tercero: envío × carrier × concepto. Clave natural: `settlement_line_id`. Fecha de negocio: `settled_date`. Solo los envíos entregados de carriers `third_party` tienen liquidación, con una línea `freight`; no la tienen si su `settled_date` pasaría de la fecha de corte.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `settlement_line_id` | `text` | `NOT NULL` | Identificador de la línea (`STLL-00000001`) |
| `settlement_id` | `text` | `NOT NULL` | Liquidación que agrupa las líneas de un carrier y un mes de `settled_date` (`STL-CAR-023-202603`). No es clave |
| `shipment_id` | `text` | `NOT NULL` | Envío liquidado, de `raw.shipments` |
| `carrier_id` | `text` | `NOT NULL` | Transportador del envío, de `raw.carriers`; siempre `third_party` |
| `concept` | `text` | `NOT NULL` | `freight` |
| `settled_date` | `date` | `NOT NULL` | Liquidación: la fecha UTC de `delivered_at` más 7 a 20 días |
| `payable_amount` | `numeric(14,2)` | `NOT NULL` | Importe a pagar: `distance_km` de la ruta por 3 400 por km y por el `rate_index` del carrier |
| `deduction_amount` | `numeric(14,2)` | `NOT NULL` | Descuento del 5 % de `payable_amount` si `delivered_at` es posterior a `promised_delivery_ts`; `0.00` si no |
| `fuel_surcharge_amount` | `numeric(14,2)` | `NOT NULL` | Recargo de combustible: 9 % de `payable_amount` |
| `settlement_status` | `text` | `NOT NULL` | `settled` |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'erp'` |

### 2.27 `raw.shipment_costs`

Un componente del costo de un envío de la flota propia. Clave natural: `shipment_cost_id`. Fecha de negocio: `cost_date`. Todo envío base de un carrier `owned`, entregado o no, tiene tres filas: `fuel`, `driver` y `tolls`. Un envío con costos de flota no tiene liquidación en `raw.carrier_settlements`, y al revés.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `shipment_cost_id` | `text` | `NOT NULL` | Identificador del costo (`SHC-000000001`) |
| `shipment_id` | `text` | `NOT NULL` | Envío, de `raw.shipments` |
| `carrier_id` | `text` | `NOT NULL` | Transportador del envío, de `raw.carriers`; siempre `owned` |
| `cost_component` | `text` | `NOT NULL` | `fuel`, `driver` o `tolls` |
| `cost_date` | `date` | `NOT NULL` | La `pickup_date` del envío |
| `cost_amount` | `numeric(14,2)` | `NOT NULL` | `distance_km` de la ruta por el costo por km del componente: 1 350 en `fuel`, 620 en `driver` y, en `tolls`, 410, 260 o 90 según el `road_type` de la ruta (`primary`, `secondary` o `tertiary`) |
| `cost_center` | `text` | `NOT NULL` | Centro de costo del carrier (`CC-CAR-008`) |
| `currency_code` | `text` | `NOT NULL` | `COP` |
| `run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at` | ver §2.1 | `NOT NULL` | Metadatos de ejecución; `source_system = 'erp'` |

## 3. Manifiesto de defectos

`data/<perfil>/defect_manifest.json` declara las filas base de cada tabla TMS, los defectos inyectados en `shipments.csv`, `shipment_events.csv`, `fuel_transactions.csv`, `carrier_tenders.csv` y `carrier_incidents.csv`, y el reparto de las nueve entidades ERP y CRM entre su archivo principal y su archivo tardío (§1). Es determinista: no guarda fecha de generación, ruta de salida, versión ni `run_id`, y `tables`, `defects` y `late_files` siguen un orden fijo.

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
| `late_files[]` | array | Una entrada por entidad con archivo tardío, en el orden de carga de §2 (`--source files`), sin `customers`: `tariffs`, `contacts`, `contracts`, `leads`, `invoices`, `invoice_lines`, `payments`, `carrier_settlements` y `shipment_costs` |
| `late_files[].table` | string | Entidad, sin esquema (`invoices`) |
| `late_files[].file` | string | Ruta del archivo tardío relativa a la carpeta del perfil (`late/invoices_late.csv`) |
| `late_files[].key` | array de string | Columnas de la clave natural que decide el reparto (`["tariff_id", "valid_from"]` en `tariffs`) |
| `late_files[].rows` | integer | Filas de la entidad, sumando el archivo principal y el tardío |
| `late_files[].late_rows` | integer | Filas del archivo tardío; el principal tiene `rows - late_rows` |

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

El manifiesto no lista las claves de las filas tardías: son las del propio archivo tardío. Cada entrada se reproduce contando las filas de datos, sin la cabecera, de `<table>.csv` y de su `file`. Con `demo` y la semilla por defecto, `late_files` declara:

| `table` | `rows` | `late_rows` |
|---|---|---|
| `tariffs` | 1800 | 47 |
| `contacts` | 450 | 14 |
| `contracts` | 150 | 4 |
| `leads` | 500 | 17 |
| `invoices` | 46152 | 1375 |
| `invoice_lines` | 92304 | 2773 |
| `payments` | 40013 | 1230 |
| `carrier_settlements` | 22577 | 689 |
| `shipment_costs` | 75198 | 2203 |

## 4. Linaje `tms` → `raw` → `stg` → `core`

Las entidades TMS llegan a `raw` por extracción de `tms.<tabla>` por watermark, y las ERP y CRM por carga de sus archivos por hash (§2.2). Desde `raw`, la transformación la hace dbt. Las relaciones de `stg` son vistas; las de `quarantine` y `core` son tablas que se reconstruyen en cada `dbt build`.

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

Las nueve tablas ERP y CRM nuevas aterrizan en `raw` desde sus archivos y todavía no tienen staging ni modelos en dbt. Cada una recibe su archivo principal y, en una carga posterior, su archivo tardío `late/<tabla>_late.csv`:

```text
tariffs.csv             ─► raw.tariffs
contacts.csv            ─► raw.contacts
contracts.csv           ─► raw.contracts
leads.csv               ─► raw.leads
invoices.csv            ─► raw.invoices
invoice_lines.csv       ─► raw.invoice_lines
payments.csv            ─► raw.payments
carrier_settlements.csv ─► raw.carrier_settlements
shipment_costs.csv      ─► raw.shipment_costs
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

## 11. Ingesta por API

La vía API aterriza el JSON crudo sin transformar en MongoDB y registra la ejecución en PostgreSQL. Es append-only e idempotente por ejecución: repetir un `run_id` con la misma clave no añade ni reescribe, y otra ejecución del mismo rango aterriza sus documentos aunque el contenido coincida. No se deduplica por contenido: la normalización, la cuarentena y el grano analítico de cada entidad son de una etapa posterior. `dag_ingest_apis` corre cada hora con una tarea por fuente (§11.4).

### 11.1 Colecciones y metadatos

El usuario de aplicación `nexora_app` tiene `readWrite` solo en las bases `raw` y `meta`; el root queda para administración (§11.5).

| Colección | Grano | Campos propios | Índices |
|---|---|---|---|
| `raw.api_payloads` | Un documento por página recibida de un recurso en una ejecución (`run_id`, `endpoint`, `page`) | `payload` (JSON recibido, sin transformar), `content_type`, `http_status` | Único `uniq_run_endpoint_page (run_id, endpoint, page)`; `idx_payload_sha256 (payload_sha256)`; `idx_source_ingested_at (source_system, ingested_at)` |
| `meta.api_schema_drift` | Una fila por payload alterado y campo | `field`, `change_type`, `expected_type`, `actual_type`, `source_key`, `rule_id`, `severity`, `detected_at` | Solo el validador de metadatos |

Ambas colecciones exigen los seis metadatos obligatorios, sin nulos:

| Metadato | Tipo en MongoDB | Descripción |
|---|---|---|
| `run_id` | `string` | Identificador de la ejecución; el runner lo genera por dataset |
| `source_system` | `string` | `tms`, `weather`, `geocoding` o `exchange_rate` |
| `endpoint` | `string` | Recurso consultado, por ejemplo `/v1/shipments` |
| `page` | `int` | Número de página, desde 1 |
| `payload_sha256` | `string` | SHA-256 en hexadecimal de los bytes recibidos |
| `ingested_at` | `date` | Instante UTC del aterrizaje; en el registro de drift coincide con `detected_at` |

`payload_sha256` se calcula sobre los bytes recibidos, no sobre una reserialización. Si la clave `(run_id, endpoint, page)` existe con el mismo hash, la página no se escribe; si existe con otro hash, falla con `PayloadRewriteError` en lugar de reescribir. Una clave nueva se inserta siempre, aunque su hash ya exista en otro `run_id`. `source_key` guarda la clave natural del elemento alterado para ubicar el registro de drift.

### 11.2 Contrato de los recursos

Los tres recursos de `tms` comparten el sobre de paginación `{"data": [...], "page", "page_size", "total_pages", "next_page"}`: `page` empieza en 1, `page_size` por defecto es 200 y como máximo 1000, y el recorrido termina cuando `next_page` es nulo. En el JSON, los enteros son `integer`, los decimales `number`, las fechas `YYYY-MM-DD` (`date`), las marcas ISO 8601 en UTC con sufijo `Z` (`timestamp`) y los nulos del origen se serializan como `null`. Un campo nulable del contrato admite `null`; si aparece con otro tipo cuenta como drift (§11.3).

| Recurso | Endpoint | Dataset | Clave natural |
|---|---|---|---|
| `shipments` | `/v1/shipments` | `tms_shipments` | `shipment_id` |
| `shipment_events` | `/v1/shipment_events` | `tms_shipment_events` | `event_id` |
| `carrier_incidents` | `/v1/carrier_incidents` | `tms_carrier_incidents` | `incident_id` |

Campos de `shipments` (los de la tabla `raw.shipments`, §2.3, con sus tipos JSON):

| Campo | Tipo | Campo | Tipo |
|---|---|---|---|
| `shipment_id` | `string` | `delivered_at` | `timestamp`, nulable |
| `customer_id` | `string`, nulable | `ordered_qty` | `integer` |
| `carrier_id` | `string`, nulable | `delivered_qty` | `integer`, nulable |
| `route_id` | `string` | `ordered_weight_kg` | `number` |
| `service_type_id` | `string` | `delivered_weight_kg` | `number`, nulable |
| `pickup_ts` | `timestamp` | `definitive_failure_flag` | `boolean` |
| `pickup_date` | `date` | `promised_delivery_ts` | `timestamp`, nulable |
| `picked_up_at` | `timestamp` | | |

Campos de `shipment_events`:

| Campo | Tipo |
|---|---|
| `event_id` | `string` |
| `shipment_id` | `string` |
| `event_ts` | `timestamp` |
| `event_type` | `string`, nulable |
| `location_city` | `string`, nulable |
| `latitude` | `number` |
| `longitude` | `number` |
| `severity` | `string` |

Campos de `carrier_incidents`:

| Campo | Tipo | Campo | Tipo |
|---|---|---|---|
| `incident_id` | `string` | `damage_flag` | `boolean` |
| `shipment_id` | `string` | `claim_amount` | `number`, nulable |
| `carrier_id` | `string` | `currency_code` | `string` |
| `incident_type` | `string` | `reported_ts` | `timestamp` |
| `severity` | `string` | `responded_ts` | `timestamp` |
| `resolved_ts` | `timestamp`, nulable | | |

Las tres fuentes externas no recuperan paginación del proveedor: el runner envuelve cada payload simulado en el mismo sobre de paginación, con `data` de un elemento por página. Los payloads son deterministas por `sha256` de la fuente y de la ciudad o la fecha, con caché en disco (clima 60 min, geocodificación sin caducidad, TRM 24 h):

| Dataset | Endpoint | Elementos | Campos del payload |
|---|---|---|---|
| `weather_api` | `/v1/weather` | Las 18 ciudades del catálogo | `source`, `city`, `date`, `observed_at`, `temperature_c`, `humidity_pct`, `wind_speed_ms`, `condition` |
| `geocoding_api` | `/v1/geocoding` | Las 18 ciudades del catálogo | `source`, `city`, `region`, `latitude`, `longitude` |
| `exchange_rate_api` | `/v1/exchange_rate` | Una fecha | `source`, `date`, `base` (`USD`), `quote` (`COP`), `rate` |

La TRM se aterriza como payload; la conversión de moneda y su persistencia no forman parte de este corte.

### 11.3 Drift y regla DQ-008

El detector compara cada elemento del payload con el contrato del recurso. Registra una fila por campo alterado en `meta.api_schema_drift` con estos campos propios:

| Campo | Descripción |
|---|---|
| `field` | Campo añadido, ausente o retipado |
| `change_type` | `added` (fuera del contrato), `removed` (del contrato y ausente) o `retyped` (tipo observado distinto del declarado) |
| `expected_type` / `actual_type` | Tipos declarado y observado; en `added`, `expected_type` es vacío |
| `source_key` | Clave natural del elemento afectado |
| `rule_id` | `DQ-008` |
| `severity` | `blocking` |
| `detected_at` | Instante de la detección, igual que `ingested_at` |

El orden es el de la regla: primero se aterriza el payload, después se escriben las filas de drift, luego `meta.api_runs` queda en `blocked` y solo al final se propaga `ApiSchemaDriftError`, que falla la tarea y sirve de alerta. El payload crudo se conserva. La activación del mock con `NEXORA_API_DRIFT=1` está en [`07_runbook.md`](07_runbook.md) §4.4.

### 11.4 Registro en PostgreSQL y frescura

El registro crea estas dos tablas en `meta` en la primera ejecución, con el usuario que ejecuta la ingesta. No hay reglas DQ nuevas: la frescura es DQ-006b y el drift es DQ-008.

`meta.datasets`: una fila por fuente de API, sembrada por upsert en cada ejecución.

| Columna | Descripción |
|---|---|
| `dataset_name` | Clave primaria: `tms_shipments`, `tms_shipment_events`, `tms_carrier_incidents`, `weather_api`, `geocoding_api`, `exchange_rate_api` |
| `source_system` | `tms`, `weather`, `geocoding` o `exchange_rate` |
| `tier` | `api` |
| `freshness_slo_minutes` | `120` para las seis fuentes (T1) |
| `description` | Descripción de la fuente |
| `updated_at` | Última siembra, en UTC |

`meta.api_runs`: una fila por fuente y ejecución. Clave primaria `(run_id, dataset_name)`, con `dataset_name` referenciado a `meta.datasets`.

| Columna | Descripción |
|---|---|
| `run_id` / `dataset_name` | Clave primaria de la ejecución |
| `source_system` | Origen de la fuente |
| `started_at` / `finished_at` | Instantes UTC de inicio y fin |
| `status` | `ok`, `failed` o `blocked` |
| `pages` / `documents` | Páginas aterrizadas y elementos del payload |
| `drift_count` | Cambios de esquema de la ejecución; `0` por defecto |
| `error_class` / `error_detail` | Clase y detalle del error, si lo hay |

La frescura de cada dataset es `now() - max(ingested_at)` de su `endpoint` en `raw.api_payloads`, en minutos, y se compara con `freshness_slo_minutes` de `meta.datasets`. La consulta canónica está en `python -m nexora.ingestion.api.cli freshness`, que imprime una línea por dataset con sus minutos y su SLO. El mismo CLI expone `extract` para una fuente y `verify-keys` para contrastar las claves naturales de los tres recursos de `tms` con su tabla de origen; `verify-keys` falla si esa tabla está vacía, en lugar de comparar `0 = 0`.

### 11.5 Acceso y secretos

El cliente, el runner y la CLI se conectan a MongoDB con `NEXORA_MONGO_APP_DSN` (usuario `nexora_app`, `authSource=admin`) y a PostgreSQL con `NEXORA_PG_DSN`. El mock exige `NEXORA_API_TOKEN` para servir `/v1/*` y deja `/health` sin token para el healthcheck. El token, su rotación y el usuario de aplicación están descritos en [`05_governance_policy.md`](05_governance_policy.md) §10.

## 12. Exposición de la vía API a dbt

La vía API aterriza los sobres paginados en MongoDB (§11) y los conserva sin transformar. Para que dbt pueda consumirlos, un paso de exposición en Python expande `data[]` a una tabla nueva de PostgreSQL. El paso no ejecuta ni despausa `dag_ingest_apis` y no modifica `raw.api_payloads` ni `meta.*`: solo lee esas colecciones y escribe en la tabla aditiva `raw.api_items`. Su operación contra la instancia principal es el único punto de escritura de este corte.

### 12.1 Paso de exposición e idempotencia

El módulo `python -m nexora.ingestion.api.expose` ofrece dos subcomandos: `expose --run-id <id>` expande los elementos de una ejecución y `verify --run-id <id>` compara, por `(endpoint, page)`, el número de elementos aterrizados en `raw.api_payloads` con el de filas expuestas. `verify` falla si la ejecución no tiene elementos, en lugar de comparar cero con cero. Los nombres de base, colección, esquema y tabla se inyectan por argumentos de línea de comandos o por variables de entorno `NEXORA_EXPOSE_*`; sin inyección, apuntan a `raw.api_payloads`, `meta.api_schema_drift` y `raw.api_items`. En la documentación de operación, `python -m nexora.ingestion.api.expose` designa el módulo; la invocación ejecutable lleva siempre el subcomando.

Solo se exponen los tres recursos del contrato (`tms_shipments`, `tms_shipment_events` y `tms_carrier_incidents`, §11.2). La tabla se crea con `create table if not exists` y cada elemento se inserta con `insert … on conflict (run_id, endpoint, page, item_index) do nothing`: repetir un `run_id` no añade filas ni reescribe hashes, y el paso no emite `update` ni `delete`. `payload_sha256` e `ingested_at` se copian del sobre aterrizado; `exposed_at` se fija solo en la primera inserción.

### 12.2 `raw.api_items`

Grano: una fila por elemento de `data[]` de una página aterrizada, con los seis metadatos de esa página. Clave primaria `(run_id, endpoint, page, item_index)`; `item_index` es la posición 0-based del elemento dentro de `data[]` y distingue elementos repetidos en una misma página. Es un grano propio, distinto del de las entidades de §2: no agrega el sobre ni pierde la trazabilidad de la página.

| Columna | Tipo | Nulabilidad | Descripción |
|---|---|---|---|
| `run_id` | `text` | `NOT NULL`, parte de la PK | Ejecución de la ingesta de la vía API; se copia del sobre |
| `source_system` | `text` | `NOT NULL` | Sistema de origen del sobre (`tms` en los tres recursos) |
| `endpoint` | `text` | `NOT NULL`, parte de la PK | Recurso paginado: `/v1/shipments`, `/v1/shipment_events` o `/v1/carrier_incidents` |
| `page` | `integer` | `NOT NULL`, parte de la PK | Número de página del sobre aterrizado |
| `payload_sha256` | `text` | `NOT NULL` | SHA-256 del contenido del sobre; se copia del documento |
| `ingested_at` | `timestamptz` | `NOT NULL` | Instante de aterrizaje del sobre en MongoDB; se copia del documento |
| `dataset_name` | `text` | `NOT NULL` | Dataset del contrato, derivado del `endpoint` |
| `source_key` | `text` | `NOT NULL` | Valor de la clave natural del elemento (`shipment_id`, `event_id` o `incident_id`) |
| `item_index` | `integer` | `NOT NULL`, parte de la PK | Posición 0-based del elemento en `data[]` |
| `payload` | `jsonb` | `NOT NULL` | Elemento crudo del sobre, sin transformar |
| `has_drift_flag` | `boolean` | `NOT NULL`, `false` por defecto | Verdadero si el elemento tiene entradas en `meta.api_schema_drift` |
| `drift_fields` | `jsonb` | `NOT NULL`, `[]` por defecto | Arreglo de campos con drift del elemento |
| `exposed_at` | `timestamptz` | `NOT NULL` | Instante de la exposición; solo se fija en la primera inserción |

### 12.3 Bandera de drift

`has_drift_flag` y `drift_fields` se derivan en el paso de exposición consultando `meta.api_schema_drift` por `(run_id, endpoint, page, source_key)`: un elemento con una o más entradas de drift queda marcado. La bandera es la proyección en PostgreSQL de la pertenencia del elemento a la colección de drift (§11.3); la colección solo se lee y el payload crudo se conserva. El drift detectado no llega a `core`: queda trazable en staging y en cuarentena con `DQ-008` (§12.6).

### 12.4 Staging `stg_api__*`

Tres modelos tipan los elementos de `raw.api_items` desde los contratos del módulo de contratos de la vía API (§11.2), sin borrar filas: `stg_api__shipments`, `stg_api__shipment_events` y `stg_api__carrier_incidents` (esquema `stg`, vistas). Cada uno filtra por `dataset_name`, conserva los seis metadatos, `dataset_name`, `source_key`, `item_index`, `payload` íntegro, `has_drift_flag`, `drift_fields` y `exposed_at`, y añade `copy_rank = row_number() over (partition by run_id, dataset_name, source_key order by ingested_at, item_index)`. Su grano es el mismo que el de `raw.api_items`.

Cada modelo se clasifica en un par `_validated`/`_valid`: los `_validated` añaden `dq_errors`, `dq_primary_rule` e `is_valid`; los `_valid` conservan los campos del contrato y de trazabilidad sin las columnas de clasificación y solo pasan las filas `is_valid`.

### 12.5 Reglas de calidad

Sin reglas nuevas: siguen 17 (DQ-006b sigue siendo la frescura de `meta.datasets`, §11.4). Los modelos `_validated` aplican las reglas existentes en este orden de prioridad:

| Regla | Condición de rechazo | Recursos |
|---|---|---|
| DQ-001 | Clave natural nula o `copy_rank > 1` | Los tres recursos |
| DQ-002 | `delivered_at < picked_up_at`; si falta alguna fecha no incumple | Solo `shipments` |
| DQ-008 | `has_drift_flag` | Los tres recursos |

`dq_errors` conserva ese orden, `dq_primary_rule` es su primer elemento e `is_valid` es `true` cuando la lista está vacía. Una fila con drift y un duplicado lleva `dq_errors = {DQ-001,DQ-008}` y `dq_primary_rule = 'DQ-001'`.

### 12.6 Cuarentena `quarantine.api_items`

Una fila por elemento rechazado, no por regla incumplida. Reúne los tres recursos y emite las ocho columnas canónicas de cuarentena (§6):

| Columna | Descripción |
|---|---|
| `quarantine_id` | `md5` de `run_id`, `dataset_name`, `source_key`, `item_index`, `page` y `copy_rank` |
| `source_table` | `raw.api_items` |
| `rule_id` | Regla principal, igual a `dq_primary_rule` |
| `reason` | Regla principal y lista de todas las reglas incumplidas |
| `payload` | Elemento original con metadatos, `dq_errors` y `dq_primary_rule` |
| `detected_at` | Instante en que la construcción detectó el rechazo |
| `run_id` | Ejecución que produjo el elemento rechazado |
| `severity` | `blocking` si DQ-008 está en `dq_errors`; `high` en caso contrario |

Junto con los tres `stg_api__*_valid` forma una partición exacta por `run_id`: cada elemento expuesto está en válidos o en cuarentena, nunca en ambos. `quarantine.shipments` (vía por lotes) no se modifica.

### 12.7 Linaje y modelos `core_api__*`

```text
MongoDB raw.api_payloads ─► expose.py ─► raw.api_items ─► stg_api__* ─► stg_api__*_validated
                                                                    ├─► stg_api__*_valid ─► core_api__*
                                                                    └─► quarantine.api_items
MongoDB meta.api_schema_drift ─► has_drift_flag / drift_fields ─────► (excluye de core_api__*)
```

Los tres modelos `core_api__shipments`, `core_api__shipment_events` y `core_api__carrier_incidents` (esquema `core`, tablas) publican solo elementos `is_valid` y sin drift. Declaran su propio grano —una fila por elemento válido con clave `(run_id, endpoint, page, item_index)`— en su `_schema.yml`: no son un hecho canónico reservado a cortes posteriores ni sustituyen a `core.fact_shipments` (§9.3). No llevan claves sustitutas, banderas de cálculo ni importes, y no se tocan los modelos de la vía por lotes.

La partición, la unicidad del grano y la exclusión del drift se comprueban con los tests singulares `assert_api_items_partition`, `assert_api_grain` y `assert_api_drift_excluded_from_core`. El detalle del linaje completo está en §4 y la operación de la vía API en [`07_runbook.md`](07_runbook.md) §4.3 a §4.5.

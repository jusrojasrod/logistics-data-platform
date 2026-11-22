# Arquitectura

## 1. Vista general

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ FUENTES                                                                    │
│  TMS (OLTP)   ERP (CSV)   CRM (CSV)   GPS (JSON)   APIs externas           │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ INGESTA                                                                    │
│  batch (watermarks, idempotencia)  ·  cliente HTTP (retry, backoff, drift) │
│  streaming (validación Pydantic)                                           │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ ALMACENAMIENTO                                                             │
│  raw         aterrizaje inmutable, append-only                             │
│  stg         tipado, deduplicación, validación                             │
│  quarantine  rechazos con regla y payload                                  │
│  core        dimensiones, hechos, tabla puente                             │
│  marts       agregados de consumo                                          │
│  meta        catálogo, calidad, linaje, accesos                            │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ CONSUMO                                                                    │
│  Apache Superset (datasets, dashboards, RLS)                               │
│  Cloudflare Tunnel + Access (sin puertos abiertos)                         │
└────────────────────────────────────────────────────────────────────────────┘
```

Orquestación: Apache Airflow 3. Transformación: dbt. Todo corre en contenedores y se reproduce con una semilla fija.

## 2. Capas

| Capa | Esquema | Propósito | Contrato |
|---|---|---|---|
| Raw | `raw` | Aterrizar tal como llega | Append-only. Cada fila lleva `ingested_at`, `source_system`, `run_id` |
| Staging | `stg` | Limpiar y validar | `_validated` calcula errores; `_valid` filtra; nada se borra |
| Cuarentena | `quarantine` | Conservar rechazos | `rule_id`, `reason`, `payload`, `run_id`, `detected_at` |
| Core | `core` | Modelo dimensional | Grano declarado, claves sustitutas, SCD2 donde aplica |
| Marts | `marts` | Consumo | Agregados estables; ningún consumidor lee `raw` |
| Metadatos | `meta` | Gobierno y operación | Catálogo, reglas, resultados, linaje, accesos, ejecuciones; watermarks de extracción (`meta.batch_watermarks`) y registro de archivos cargados (`meta.ingested_files`) |

## 3. Flujo

```text
generador ─► CSV TMS ─► publicador (tms_writer) ─► tms.<tabla> ─► conector por watermark (etl_writer) ─► raw.<tabla>
generador ─► CSV ERP y CRM ─────────────────────────────────────► conector por hash (etl_writer) ──────► raw.<tabla>
generador ─► late/<tabla>_late.csv (ERP y CRM) ─────────────────► conector por hash, segunda carga ────► raw.<tabla>
mock TMS API ─► /v1/<recurso> (token Bearer) ─► cliente HTTP (reintento, Retry-After, tasa) ─► MongoDB raw.api_payloads
simuladores (clima, geocodificación, TRM) ───► payload crudo ──────────────────────────────► MongoDB raw.api_payloads
MongoDB raw.api_payloads ─► exposición (expose.py) ─► raw.api_items ─► stg_api__* ─► core_api__*
MongoDB meta.api_schema_drift ─► has_drift_flag ─► cuarentena.api_items / exclusión de core

CSV TMS:     carriers, routes, service_types, shipments, vehicles, drivers, trips, trip_legs,
             shipment_legs, shipment_stops, shipment_events, fuel_transactions,
             carrier_tenders, carrier_incidents
CSV ERP:     customers, tariffs, invoices, invoice_lines, payments, carrier_settlements,
             shipment_costs
CSV CRM:     contacts, contracts, leads
Tardíos:     late/<tabla>_late.csv de las entidades ERP y CRM, salvo customers
API TMS:     shipments, shipment_events, carrier_incidents (tres recursos paginados)
API externa: weather_api, geocoding_api, exchange_rate_api (simuladas, con caché en disco)
```

1. El generador escribe en flujo los CSV de cada perfil: envíos, dimensiones TMS, flota propia (`vehicles`, `drivers`), viajes y tramos (`trips`, `trip_legs`), envíos por tramo (`shipment_legs`), paradas (`shipment_stops`), eventos (`shipment_events`), combustible (`fuel_transactions`), ofertas (`carrier_tenders`) e incidencias (`carrier_incidents`), y los del ERP (`customers`, tarifas, facturas, líneas de factura, pagos, liquidaciones a transportadores y costos de flota) y del CRM (contactos, contratos y oportunidades). Cerca del 3 % de las filas de cada entidad ERP y CRM, salvo `customers`, va a `late/<tabla>_late.csv` según el hash de su clave natural, para simular archivos que llegan tarde. El publicador inserta las entidades TMS en el esquema `tms`, que simula la base operativa del TMS, por bloques y en el mismo orden en que se extraen: solo inserciones, con `updated_at` del reloj de la base en cada fila. El perfil `benchmark` solo se genera a archivos y no se publica.
2. Airflow dispara la ingesta según el calendario del dominio. `dag_ingest_batch` corre con el calendario `0 2 * * *`, sin catchup, con una ejecución activa a la vez y 3 reintentos por tarea.
3. La tarea `extract_tms` extrae cada `tms.<tabla>` a `raw.<tabla>` con la ventana `(watermark, max(updated_at)]`; en la misma transacción inserta en `raw` y avanza `meta.batch_watermarks`. Sin filas nuevas no inserta nada.
4. La tarea `load_erp_files` carga los archivos ERP y CRM de la carpeta de la escala configurada, cada uno en su `raw.<tabla>`, y después los tardíos de su subcarpeta `late/`; un archivo cuyo SHA-256 ya está en `meta.ingested_files` se omite. Si falta `late/`, la tarea falla. `scripts/seed_db.sh` hace las mismas dos cargas tras publicar `tms`.
5. Los datos aterrizan en `raw` con metadatos de ingestión (`run_id`, `source_system`, `source_file`, `source_file_hash`, `ingested_at`). Las tareas del DAG solo llaman a los conectores de `src/nexora/ingestion/batch`: no contienen lógica de negocio.
6. Staging tipa, deduplica y valida; separa válidos y rechazados.
7. dbt construye dimensiones, hechos y puente en `core`, y agregados en `marts`.
8. Las reglas de calidad calculan el scorecard sobre lo publicado.
9. Superset consume `marts` con un usuario de solo lectura.
10. La observabilidad monitorea frescura, volumen, fallos y SLA.

La vía API corre en paralelo a la batch. `dag_ingest_apis`, con calendario `0 * * * *` y pausado al crearse, delega en el runner de `src/nexora/ingestion/api/`: recorre los tres recursos paginados del mock TMS y produce los payloads de clima, geocodificación y tasa de cambio de los simuladores deterministas. El cliente HTTP autentica con token Bearer, reintenta con backoff exponencial (hasta 5 intentos), respeta `Retry-After` y un límite de tasa, valida el sobre de paginación con Pydantic y compara cada elemento con el contrato declarado. Cada payload se aterriza sin transformar en MongoDB `raw.api_payloads` con seis metadatos; el drift detectado se registra en `meta.api_schema_drift` con DQ-008, deja `meta.api_runs` en `blocked` y falla la tarea. La frescura de cada fuente es `now() - max(ingested_at)` sobre `raw.api_payloads`, comparada con el SLO de 120 min de `meta.datasets`.

Sobre lo ya aterrizado corre el tramo de exposición → staging → core de la vía API, sin ejecutar ni despausar el DAG. El paso de exposición lee `raw.api_payloads` y `meta.api_schema_drift` en MongoDB y expande `data[]` a la tabla `raw.api_items` de PostgreSQL con los seis metadatos de página, el elemento crudo y la bandera de drift; es idempotente por `(run_id, endpoint, page, item_index)` y no modifica las colecciones de origen. Desde ahí, dbt tipa los tres recursos en `stg_api__*`, clasifica la calidad con DQ-001, DQ-002 y DQ-008, separa los rechazos en `quarantine.api_items` y publica los elementos válidos sin drift en los tres `core_api__*`. El drift queda trazable en staging y en cuarentena, pero no llega a `core`; la vía por lotes no se toca.

Sobre las seis entidades financieras del ERP ya aterrizadas en `raw` corre el tramo staging ERP → cuarentena → core financiero, sin ingesta nueva y sin suponer que el archivo principal y el tardío comparten `run_id`. La fuente dbt `erp_financial` lee `raw.tariffs`, `raw.invoices`, `raw.invoice_lines`, `raw.payments`, `raw.carrier_settlements` y `raw.shipment_costs`. Cada entidad recorre `stg_erp__<entidad>` (tipado y `copy_rank` por clave natural), `stg_erp__<entidad>_validated` (clasificación con DQ-009, DQ-010 y DQ-013) y `stg_erp__<entidad>_valid` (solo filas válidas). Los rechazos de los seis `_validated` convergen en `quarantine.financial_items`, con las ocho columnas canónicas y un `payload` que conserva la clave natural y los metadatos de carga. Los huérfanos transitorios no son un rechazo: permanecen en `_valid`, quedan fuera del hecho hasta que llega su padre y se recuperan en la construcción siguiente, con los tests `relationships` en aviso. Desde los `_valid` se publican `core.dim_tariff`, con la historia de tarifas, y tres hechos: `core.fact_invoices` (línea de factura con cabecera y pago integrados), `core.fact_carrier_settlements` (envío × carrier × concepto) y `core.fact_shipment_costs` (envío × componente). Los tres se ligan por ids naturales y claves a `fact_shipments` y a las dimensiones existentes, sin importes en el hecho de envíos; la vía por lotes y la vía API siguen intactas. Los importes se expresan en `COP`, sin conversión de moneda.

El detalle de `tms`, de las tres vías de llegada a `raw` y de las tablas de `meta` está en [`03_data_dictionary.md`](03_data_dictionary.md) §1.1, §2.2, §2.8 y §11. La exposición, el linaje y los modelos de la vía API están en el mismo documento §12. El corte financiero, sus claves y su linaje están en el mismo documento §13. La operación de la vía API está en [`07_runbook.md`](07_runbook.md) §4.3 a §4.5.

## 4. Componentes

| Componente | Responsabilidad | Dónde |
|---|---|---|
| Airflow | Orquestación, reintentos, SLA y alertas de `dag_ingest_batch` y `dag_ingest_apis` | `airflow/` |
| dbt | Transformación, tests, linaje, snapshots | `dbt/` |
| Python (`src/nexora`) | Generación, conectores, calidad, utilidades | `src/` |
| Mock TMS API | Tres recursos paginados con token Bearer, fallos 429/500 y drift deterministas | `src/nexora/generators/api/`, `infra/api/` |
| Cliente HTTP de API | Autenticación, reintentos, límite de tasa, aterrizaje y simuladores de fuentes externas | `src/nexora/ingestion/api/` |
| Exposición de API a dbt | Expande `data[]` de `raw.api_payloads` a `raw.api_items` y deriva la bandera de drift desde `meta.api_schema_drift` | `src/nexora/ingestion/api/expose.py` |
| Modelos dbt de la vía API | Staging `stg_api__*`, cuarentena `quarantine.api_items` y core `core_api__*` | `dbt/models/staging/api/`, `dbt/models/quarantine/`, `dbt/models/core/api/` |
| Modelos dbt del core financiero | Staging ERP `stg_erp__*`, cuarentena `quarantine.financial_items`, dimensión `core.dim_tariff` y hechos `core.fact_invoices`, `core.fact_carrier_settlements` y `core.fact_shipment_costs` | `dbt/models/staging/erp/`, `dbt/models/quarantine/`, `dbt/models/core/dimensions/`, `dbt/models/core/facts/` |
| Tests singulares del core financiero | Partición exacta, grano, filas tardías, no duplicación de costo y frescura de las seis fuentes ERP | `dbt/tests/` |
| PostgreSQL | OLTP simulado, warehouse y metadatos de BI, con `meta.api_runs` y `meta.datasets` | `infra/postgres/` |
| MongoDB | Payloads JSON crudos (`raw.api_payloads`), drift (`meta.api_schema_drift`) y telemetría | `docker-compose.yml`, `infra/mongo/init/` |
| Superset | Dashboards, SQL Lab, RLS | `infra/superset/`, `dashboards/superset/` |
| Cloudflare Tunnel | Exposición sin puertos abiertos | `infra/cloudflared/` |

## 5. Decisiones de modelamiento

- **Grano explícito** en cada hecho, declarado en `schema.yml` y en el diccionario.
- **Grano propio de la vía API:** los modelos `core_api__*` declaran una fila por elemento válido con clave `(run_id, endpoint, page, item_index)`; no son hechos canónicos reservados a cortes posteriores ni sustituyen a los de la vía por lotes.
- **Separación financiera:** ingreso, pago a carriers y costo de flota en hechos distintos.
- **Grano de los hechos financieros:** el corte ERP declara una fila por línea de factura (`invoice_line_id`), con cabecera y pago integrados, una fila por línea de liquidación (`settlement_line_id`) y una fila por envío y componente de costo (`shipment_cost_id`). No hay hechos propios de cabecera ni de pago, y `fact_shipments` permanece sin importes.
- **Tabla puente envío–viaje** para imputar kilómetros en vacío y costos.
- **SCD2** con miembros inferidos para hechos que llegan antes que su dimensión.
- **SCD2 de tarifas:** `core.dim_tariff` guarda una versión por fila, con `tariff_key = md5(tariff_id || '|' || valid_from)` y `valid_to` exclusivo, con el centinela `9999-12-31` en la versión vigente. La atribución en `fact_invoices` se resuelve por vigencia sobre `pickup_date`, con ambos límites, y no por la marca de versión vigente.
- **Cuarentena explícita** en staging, no solo tests.
- **DQ Score sobre core y marts**, con métricas de ingestión separadas.

Los detalles y alternativas están en [`adr/`](adr/).

## 6. Entornos

| Entorno | Uso | Servicios |
|---|---|---|
| Local | Desarrollo e iteración | Postgres, Mongo, Airflow |
| BI | Dashboards | + Superset, Redis |
| Streaming | Extensión | + Redpanda |
| Producción | Demo protegida | Instancia gratuita con límites de memoria |

## 7. Seguridad

Roles de base de datos separados, creados por `infra/postgres/init/02_roles.sql`:

| Rol | Uso | Permisos |
|---|---|---|
| `tms_writer` | Publicador del OLTP simulado | `USAGE` y `CREATE` en `tms`; es el dueño de las tablas `tms.*` y el único que escribe en ellas. Sin acceso a `raw` |
| `etl_writer` | Conectores batch y DAGs de Airflow | `USAGE` y `CREATE` en `raw`, `stg`, `quarantine`, `core`, `marts` y `meta`; en `tms`, solo `USAGE` y `SELECT` sobre las tablas que crea `tms_writer` (privilegios por defecto) |
| `bi_reader` | Conexión de Superset | `USAGE` en `core`, `marts` y `meta`, y `SELECT` sobre las tablas que crea `etl_writer` en esos esquemas |
| `dq_runner` | Reglas de calidad | `USAGE` en `raw`, `stg`, `quarantine`, `core`, `marts` y `meta`; `SELECT` sobre las tablas de `core`, `marts` y `meta` existentes al inicializar y sobre las que crea `etl_writer`; `INSERT` sobre las tablas de `meta` existentes al inicializar |
| `airflow` | Metadatos de Airflow | Dueño de la base `airflow` |
| `superset` | Metadatos de Superset | Dueño de la base `superset` |

Otras medidas:

- Las contraseñas de los seis roles se leen de variables de entorno (`<ROL>_PASSWORD`, en `.env`) al crear el volumen de PostgreSQL; si falta alguna, la inicialización falla. `infra/postgres/init` y `src` no contienen contraseñas; `docker-compose.yml` y `.env.example` traen valores de desarrollo (`<rol>_dev`) que se sustituyen en `.env`. La rotación está en [`07_runbook.md`](07_runbook.md) §7.
- Superset con RLS por región y por carrier.
- Sin puertos públicos: túnel saliente y autenticación por correo.
- Secretos por entorno, nunca en el repositorio.
- Datos exclusivamente sintéticos.

# Arquitectura

## 1. Vista general

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ FUENTES                                                                    │
│  TMS (OLTP)   ERP (CSV)   CRM (Excel)   GPS (JSON)   APIs externas         │
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

CSV TMS:     carriers, routes, service_types, shipments, vehicles, drivers, trips, trip_legs,
             shipment_legs, shipment_stops, shipment_events, fuel_transactions,
             carrier_tenders, carrier_incidents
CSV ERP:     customers, tariffs, invoices, invoice_lines, payments, carrier_settlements,
             shipment_costs
CSV CRM:     contacts, contracts, leads
Tardíos:     late/<tabla>_late.csv de las entidades ERP y CRM, salvo customers
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

El detalle de `tms`, de las dos vías de llegada a `raw` y de las tablas de `meta` está en [`03_data_dictionary.md`](03_data_dictionary.md) §1.1, §2.2 y §2.8.

## 4. Componentes

| Componente | Responsabilidad | Dónde |
|---|---|---|
| Airflow | Orquestación, reintentos, SLA, alertas | `airflow/` |
| dbt | Transformación, tests, linaje, snapshots | `dbt/` |
| Python (`src/nexora`) | Generación, conectores, calidad, utilidades | `src/` |
| PostgreSQL | OLTP simulado, warehouse y metadatos de BI | `infra/postgres/` |
| MongoDB | Payloads JSON y telemetría | `docker-compose.yml` |
| Superset | Dashboards, SQL Lab, RLS | `infra/superset/`, `dashboards/superset/` |
| Cloudflare Tunnel | Exposición sin puertos abiertos | `infra/cloudflared/` |

## 5. Decisiones de modelamiento

- **Grano explícito** en cada hecho, declarado en `schema.yml` y en el diccionario.
- **Separación financiera:** ingreso, pago a carriers y costo de flota en hechos distintos.
- **Tabla puente envío–viaje** para imputar kilómetros en vacío y costos.
- **SCD2** con miembros inferidos para hechos que llegan antes que su dimensión.
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

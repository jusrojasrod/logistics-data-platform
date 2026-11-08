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
| Raw | `raw` | Aterrizar tal como llega | Append-only. Cada fila lleva `ingestion_ts`, `source_system`, `run_id` |
| Staging | `stg` | Limpiar y validar | `_validated` calcula errores; `_valid` filtra; nada se borra |
| Cuarentena | `quarantine` | Conservar rechazos | `rule_id`, `reason`, `payload`, `run_id`, `detected_at` |
| Core | `core` | Modelo dimensional | Grano declarado, claves sustitutas, SCD2 donde aplica |
| Marts | `marts` | Consumo | Agregados estables; ningún consumidor lee `raw` |
| Metadatos | `meta` | Gobierno y operación | Catálogo, reglas, resultados, linaje, accesos, ejecuciones |

## 3. Flujo

1. Airflow dispara la extracción según el calendario del dominio.
2. Los datos aterrizan en `raw` con metadatos de ingestión.
3. Staging tipa, deduplica y valida; separa válidos y rechazados.
4. dbt construye dimensiones, hechos y puente en `core`, y agregados en `marts`.
5. Las reglas de calidad calculan el scorecard sobre lo publicado.
6. Superset consume `marts` con un usuario de solo lectura.
7. La observabilidad monitorea frescura, volumen, fallos y SLA.

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

- Roles de base de datos separados: ETL, BI de solo lectura y calidad.
- Superset con RLS por región y por carrier.
- Sin puertos públicos: túnel saliente y autenticación por correo.
- Secretos por entorno, nunca en el repositorio.
- Datos exclusivamente sintéticos.

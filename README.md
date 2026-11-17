# Nexora Data Platform

Plataforma de analítica y gobierno de datos para una operación logística: ingesta de fuentes transaccionales y APIs, un data warehouse dimensional, indicadores de negocio, dashboards y controles de calidad y gobierno.

> **Aviso:** Nexora Logística es una empresa ficticia creada para este proyecto. Todos los datos son sintéticos. No se utiliza información de empresas ni de personas reales.

---

## Problema

La operación de Nexora Logística combina flota propia y transportadores terceros. La información vive dispersa en un TMS, un ERP, un CRM, archivos de Excel y APIs externas. Los reportes se arman a mano, cada área define sus indicadores a su manera y nadie puede decir qué tan confiable es un dato ni de dónde viene.

La plataforma resuelve tres cosas:

1. **Un solo número.** Cada indicador se define una vez y se calcula igual en todas partes.
2. **Trazabilidad.** Cada fila se puede seguir desde la fuente hasta el dashboard.
3. **Confianza medible.** La calidad y la frescura se monitorean, no se suponen.

## Arquitectura

```text
   FUENTES                 INGESTA                 ALMACENAMIENTO            CONSUMO
┌──────────────┐      ┌───────────────┐        ┌──────────────────┐     ┌──────────────┐
│ TMS (OLTP)   │─────▶│ Batch         │───────▶│ raw  (inmutable) │     │ Apache       │
│ ERP (CSV)    │      │ API cliente   │        │ stg  (validado)  │────▶│ Superset     │
│ CRM (CSV)    │      │ Streaming     │        │ quarantine       │     │              │
│ APIs externas│      └───────────────┘        │ core (dimensional)│     │ SQL + RLS    │
└──────────────┘                               │ marts (consumo)  │     └──────────────┘
                                               │ meta (gobierno)  │
                                               └──────────────────┘
        Orquestación: Apache Airflow  ·  Transformación: dbt  ·  Calidad: reglas + cuarentena
```

El diagrama describe la plataforma completa; qué parte funciona hoy se detalla en [Estado](#estado). Las capas y sus contratos están descritos en [`docs/04_architecture.md`](docs/04_architecture.md). Las decisiones, en [`docs/adr/`](docs/adr/).

## Stack

| Componente | Herramienta |
|---|---|
| Base relacional y warehouse | PostgreSQL 16 |
| Documentos y telemetría | MongoDB 7 |
| Transformación | dbt-core |
| Orquestación | Apache Airflow 3.4 |
| BI | Apache Superset 6 |
| Streaming (extensión) | Redpanda |
| Contenedores | Docker Compose |

## Inicio rápido

```bash
cp .env.example .env        # completar secretos locales
make up                     # postgres, mongo, airflow
make ps
make data                   # datos sintéticos en data/demo (perfil por defecto)
```

`make data SCALE=local` genera el perfil `local` en `data/local`. Cada perfil produce un CSV por entidad, los archivos tardíos de ERP y CRM en `late/` y el manifiesto de defectos, descritos en [`docs/03_data_dictionary.md`](docs/03_data_dictionary.md). La publicación en `tms`, la extracción a `raw` y la carga de archivos están en el [runbook](docs/07_runbook.md).

Airflow queda en `http://localhost:8080`; Superset, en `http://localhost:8088` cuando se levanta el perfil de BI (`make up-bi`). Ningún servicio se expone fuera de la máquina local.

## Estado

La plataforma está en desarrollo. Esta sección separa lo que ya se puede ejecutar de lo que viene después.

### Disponible hoy

- **Entorno local.** Docker Compose con PostgreSQL 16, MongoDB 7 y Airflow 3.4 (`make up`), con healthchecks, y un perfil de BI con Superset 6 y Redis (`make up-bi`). Esquemas `tms`, `raw`, `stg`, `quarantine`, `core`, `marts` y `meta`, con roles separados para publicar, cargar y leer.
- **Generador sintético.** Perfiles `demo` (50 000 envíos), `local` (200 000) y `benchmark` (2 000 000). Cubre el TMS completo (envíos, eventos, flota, viajes y tramos, paradas, combustible, ofertas e incidencias de carriers), el ERP (clientes, tarifas, facturas, pagos, liquidaciones a carriers y costos) y el CRM (contactos, contratos y oportunidades). Inyecta defectos registrados en un manifiesto y separa cerca del 3 % de las filas de ERP y CRM como archivos tardíos.
- **Ingesta batch.** Publicación del TMS simulado en `tms` y extracción incremental a `raw` por watermark de cada tabla. Carga de los archivos ERP y CRM a `raw` con registro por hash, en dos pasos: primero la carpeta principal y después los tardíos. Repetir una ingesta sin cambios en la fuente no inserta filas, y `raw` es append-only. El DAG diario `dag_ingest_batch` ejecuta las dos vías en Airflow.
- **Transformación con dbt.** Staging de envíos con validación y cuarentena de las filas rechazadas, con regla y motivo; staging de clientes, carriers, rutas y tipos de servicio; las dimensiones `dim_customer`, `dim_carrier`, `dim_route`, `dim_service_type` y `dim_date`; el hecho `fact_shipments` y el mart diario `mart_operations`, con los conteos de OTIF, OTD e In Full. Los tests de dbt incluyen conciliaciones entre capas.
- **BI.** Dashboard de operación en Superset con el OTIF mensual, versionado en YAML e importable con `make import-superset`, sobre una conexión de solo lectura.
- **CI.** GitHub Actions ejecuta ruff, black, pytest, `dbt parse` y la verificación de estructura del repositorio. sqlfluff corre en local con `make lint` y en pre-commit.
- **Mediciones.** Tiempo, memoria y disco del generador y de la ingesta batch, en [`benchmarks/results/`](benchmarks/results/).

### En curso / siguiente

- **Ingesta de APIs.** API simulada con autenticación y paginación; cliente HTTP con reintentos, backoff, límite de tasa y detección de cambios de esquema; fuentes externas de clima, geocodificación y tasa de cambio; aterrizaje de JSON en MongoDB.
- **Core operativo.** Dimensiones SCD2 con miembros inferidos para datos tardíos; hechos de eventos, viajes y combustible; tabla puente envío–viaje con reparto por peso y distancia; DAG de transformación en Airflow.
- **Core financiero.** Hechos de facturación, liquidaciones a carriers, costos de envío, incidencias y ofertas, sobre los datos ERP que ya llegan a `raw`.

### Pendiente

- Marts comerciales y de gobierno, KPIs financieros y scorecard de carriers.
- Línea base de calidad (DQ Score) y de frescura, y benchmark de consultas.
- Dashboards comercial y de gobierno, roles y RLS en Superset, acceso con Cloudflare Tunnel y Access, y el anexo documental de Power BI.
- Gobierno de datos: catálogo y linaje en `meta`, clasificación y enmascaramiento de PII, retención por capas y matriz de accesos.
- Despliegue en Oracle Cloud, CD, respaldos y observabilidad.
- Extensión: streaming de telemetría GPS con Redpanda y modelo de predicción de ETA.

### Indicadores

**Por medir** significa que todavía no hay una medición publicada que respalde la cifra.

| Indicador | Cálculo | Medición |
|---|---|---|
| OTIF, OTD, In Full | `mart_operations` y dashboard de operación | por medir |
| Costo por envío, margen bruto | pendiente | por medir |
| Deadhead y utilización de flota | pendiente | por medir |
| Calidad del dato (DQ Score) | pendiente | por medir |
| Rendimiento de consultas | consulta de referencia en `benchmarks/queries/` | por medir |
| Escala del generador y de la ingesta batch | generación, publicación, extracción y carga | medida en [`benchmarks/results/`](benchmarks/results/) |

El catálogo completo, con fórmula y grano, está en [`docs/02_kpi_catalog.md`](docs/02_kpi_catalog.md).

## Estructura

```text
docs/          contexto, catálogo de KPIs, diccionario, arquitectura, gobierno, runbook, ADRs
dbt/           modelos staging, cuarentena, core y marts; tests
src/nexora/    generador sintético, conectores de ingesta y configuración
airflow/       DAGs y plugins
infra/         inicialización de Postgres, imágenes, Superset, túnel
dashboards/    definiciones versionadas de Superset
benchmarks/    consultas y resultados medidos
tests/         unitarios e integración
```

## Documentación

- [Contexto de negocio](docs/01_business_context.md)
- [Catálogo de KPIs](docs/02_kpi_catalog.md)
- [Diccionario de datos](docs/03_data_dictionary.md)
- [Arquitectura](docs/04_architecture.md)
- [Reglas de calidad](docs/06_quality_rules.md)
- [Gobierno de datos](docs/05_governance_policy.md)
- [Runbook](docs/07_runbook.md)

## Licencia

MIT. Ver [`LICENSE`](LICENSE).

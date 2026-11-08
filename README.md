# Nexora Data Platform

Plataforma de analítica y gobierno de datos para una operación logística: ingesta de fuentes transaccionales y APIs, un data warehouse dimensional, indicadores de negocio, dashboards y controles de calidad y gobierno.

> **Aviso:** Nexora Logística es una empresa ficticia creada para este proyecto. Todos los datos son sintéticos y se generan con una semilla fija. No se utiliza información de empresas ni de personas reales.

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
│ CRM (Excel)  │      │ Streaming     │        │ quarantine       │     │              │
│ APIs externas│      └───────────────┘        │ core (dimensional)│     │ SQL + RLS    │
└──────────────┘                               │ marts (consumo)  │     └──────────────┘
                                               │ meta (gobierno)  │
                                               └──────────────────┘
        Orquestación: Apache Airflow  ·  Transformación: dbt  ·  Calidad: reglas + cuarentena
```

Las capas y sus contratos están descritos en [`docs/04_architecture.md`](docs/04_architecture.md). Las decisiones, en [`docs/adr/`](docs/adr/).

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
```

Airflow queda en `http://localhost:8080`; Superset, en `http://localhost:8088` cuando se levanta el perfil de BI (`make up-bi`). Ningún servicio se expone fuera de la máquina local.

## Estado

El proyecto está en construcción por fases. La tabla de indicadores refleja el estado real de cada medición: **por medir** significa que todavía no existe una ejecución que la respalde.

| Indicador | Estado |
|---|---|
| OTIF, OTD, In Full | por medir |
| Costo por envío, margen bruto | por medir |
| Deadhead y utilización de flota | por medir |
| Calidad del dato (DQ Score) | por medir |
| Rendimiento de consultas | por medir |

El catálogo completo, con fórmula y grano, está en [`docs/02_kpi_catalog.md`](docs/02_kpi_catalog.md).

## Estructura

```text
docs/          contexto, catálogo de KPIs, diccionario, arquitectura, gobierno, runbook, ADRs
dbt/           modelos staging, core y marts; snapshots y tests
src/nexora/    generador, conectores, calidad y utilidades
airflow/       DAGs y plugins
infra/         inicialización de Postgres, imágenes, Superset, túnel
dashboards/    definiciones versionadas de Superset; anexo documental de Power BI
benchmarks/    consultas, runner y resultados medidos
tests/         unitarios, integración y contrato
```

## Documentación

- [Contexto de negocio](docs/01_business_context.md)
- [Catálogo de KPIs](docs/02_kpi_catalog.md)
- [Arquitectura](docs/04_architecture.md)
- [Reglas de calidad](docs/06_quality_rules.md)
- [Gobierno de datos](docs/05_governance_policy.md)
- [Runbook](docs/07_runbook.md)

## Licencia

MIT. Ver [`LICENSE`](LICENSE).

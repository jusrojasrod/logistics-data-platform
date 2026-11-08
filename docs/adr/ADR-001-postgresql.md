# ADR-001 — PostgreSQL como motor principal

**Estado:** aceptada · **Fecha:** 2026-11-07

## Contexto

Se necesita un motor relacional gratuito, portable y con SQL avanzado para simular el sistema transaccional de origen y para alojar el warehouse. El proyecto debe poder ejecutarse en un equipo de desarrollo y en una instancia gratuita de nube.

## Decisión

Usar PostgreSQL 16 para ambos roles: base operativa simulada y data warehouse, con esquemas separados (`tms`, `raw`, `stg`, `core`, `marts`, `meta`).

## Alternativas consideradas

| Alternativa | Motivo de descarte |
|---|---|
| SQL Server | Licencia y peso para el entorno objetivo |
| DuckDB | Excelente para analítica local, pero no simula un OLTP concurrente |
| MySQL | Menos soporte de funciones de ventana avanzadas y tipos JSONB |

## Consecuencias

- Un solo motor reduce costo operativo y de aprendizaje.
- Se aprovechan `MERGE`, particionado declarativo, índices parciales, `pg_stat_statements` y `EXPLAIN (ANALYZE, BUFFERS)`.
- El modelo SQL es portable con cambios menores a otros dialectos.
- Los límites de memoria obligan a dimensionar bien los contenedores.

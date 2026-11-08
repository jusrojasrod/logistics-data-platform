# ADR-003 — dbt para las transformaciones

**Estado:** aceptada · **Fecha:** 2026-11-07

## Contexto

Las transformaciones deben ser versionadas, testeables y documentadas, con linaje automático y capacidad de ejecución incremental. El SQL suelto en scripts no permite mantener la calidad ni explicar dependencias.

## Decisión

Usar dbt-core con el adaptador de PostgreSQL. Staging como vistas, core y marts como tablas, hechos grandes como modelos incrementales, y snapshots para las dimensiones tipo 2.

## Alternativas consideradas

| Alternativa | Motivo de descarte |
|---|---|
| SQL en scripts con Airflow | Sin linaje, sin tests declarativos, sin documentación |
| Stored procedures | Difícil de versionar y testear; lógica oculta en la base |
| Spark | Sobredimensionado para el volumen y el hardware objetivo |

## Consecuencias

- Linaje técnico y documentación generados desde el propio proyecto.
- Tests declarativos (`unique`, `not_null`, `relationships`, `accepted_values`) más tests singulares.
- La cuarentena no se resuelve con tests: requiere modelos explícitos de separación.
- El equipo debe mantener `schema.yml` actualizado; forma parte de la definición de terminado.

# ADR-005 — Apache Superset como herramienta de BI

**Estado:** aceptada · **Fecha:** 2026-11-07

## Contexto

Se requiere BI sin costo de licencia, con seguridad a nivel de fila, SQL libre, dashboards versionables y soporte para la arquitectura del entorno objetivo.

## Decisión

Usar Apache Superset 6 como herramienta principal. Se conecta al warehouse con un usuario de solo lectura sobre `core` y `marts`. La seguridad a nivel de fila se implementa con filtros por usuario y rol.

## Alternativas consideradas

| Alternativa | Motivo de descarte |
|---|---|
| Metabase | La seguridad por fila y columna requiere edición de pago |
| Power BI | Licencia y dependencia de un servicio externo; queda como anexo documental |
| Looker Studio | Limitado para modelado y control de acceso fino |
| Lightdash | Imágenes solo para una arquitectura; dificulta el despliegue objetivo |

## Consecuencias

- Costo cero y RLS disponible en la edición abierta.
- Los dashboards se exportan y versionan como artefactos.
- Curva de aprendizaje mayor que un BI de escritorio; no hay lenguaje tipo DAX.
- La capa semántica vive en el warehouse y en dbt, no en la herramienta: eso evita dependencia.

# ADR-002 — Arquitectura por capas

**Estado:** aceptada · **Fecha:** 2026-11-07

## Contexto

Las fuentes entregan datos sucios y heterogéneos. Se necesita trazabilidad entre lo que llegó y lo que se publica, capacidad de reproceso y una separación clara entre validar y modelar.

## Decisión

Organizar el almacenamiento en capas con contratos explícitos: `raw` (inmutable), `stg` (validado), `quarantine` (rechazado), `core` (dimensional) y `marts` (consumo), más `meta` para gobierno.

## Alternativas consideradas

| Alternativa | Motivo de descarte |
|---|---|
| Una sola capa transformada | Sin trazabilidad ni reproceso selectivo |
| Dos capas (raw y presentación) | La validación queda mezclada con el modelado |
| Sin cuarentena | Los rechazos se pierden o se silencian |

## Consecuencias

- Cada fila se puede seguir desde la fuente hasta el dashboard.
- Los errores de calidad son auditables y reprocesables.
- Hay más objetos que mantener y un costo de almacenamiento mayor.
- Las reglas de acceso se simplifican: el consumo solo lee `marts`.

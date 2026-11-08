-- Capas del warehouse. Cada esquema tiene un contrato documentado en docs/04_architecture.md.
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS quarantine;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS tms;

COMMENT ON SCHEMA raw IS 'Aterrizaje inmutable, append-only';
COMMENT ON SCHEMA stg IS 'Limpieza, tipado y validación';
COMMENT ON SCHEMA quarantine IS 'Filas rechazadas con regla y motivo';
COMMENT ON SCHEMA core IS 'Dimensiones, hechos y tabla puente';
COMMENT ON SCHEMA marts IS 'Consumo analítico';
COMMENT ON SCHEMA meta IS 'Catálogo, calidad, linaje y accesos';
COMMENT ON SCHEMA tms IS 'OLTP simulado (fuente)';

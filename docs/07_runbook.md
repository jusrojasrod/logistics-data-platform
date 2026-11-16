# Runbook

## 1. Servicios

| Servicio | Contenedor | Puerto local | Notas |
|---|---|---|---|
| PostgreSQL | `nexora-postgres` | 5432 | Warehouse y metadatos |
| MongoDB | `nexora-mongo` | 27017 | JSON y telemetría |
| Airflow API | `nexora-airflow-api` | 8080 | Interfaz web y API |
| Airflow scheduler | `nexora-airflow-scheduler` | — | Programación |
| Airflow dag-processor | `nexora-airflow-dag-processor` | — | Parseo de DAGs |
| Airflow triggerer | `nexora-airflow-triggerer` | — | Tareas diferidas |
| Superset | `nexora-superset` | 8088 | Solo con perfil BI |
| Redis | `nexora-redis` | — | Caché y cola |
| Redpanda | `nexora-redpanda` | 9092 | Solo con perfil streaming |

## 2. Operación diaria

```bash
make up            # levantar
make ps            # estado y salud
make logs SERVICE=airflow-scheduler
make down          # detener sin borrar datos
make reset         # borrar contenedores y volúmenes
```

Airflow tarda cerca de un minuto en migrar la base la primera vez. Si `make up` falla, revisar `docker compose logs airflow-init`.

## 3. Diagnóstico por síntoma

| Síntoma | Revisar | Acción |
|---|---|---|
| Un DAG no aparece | `docker compose logs airflow-dag-processor` | Error de importación o ruta de DAGs |
| Tarea falla por conexión | Variables de entorno del contenedor | Verificar DSN y salud de Postgres |
| No hay datos nuevos | `meta.batch_watermarks` frente a `max(updated_at)` de `tms.<tabla>` (§4.1) | Si `tms` no tiene filas posteriores al watermark, falta publicar; si las tiene, ejecutar de nuevo la extracción |
| Cuarentena crece | `select rule_id, count(*) from quarantine.*` | Identificar regla y fuente |
| Dashboard lento | `pg_stat_statements` | Revisar consulta y caché |
| Superset no arranca | `docker compose logs superset` | Migración pendiente o secreto vacío |

## 4. Reproceso

1. Identificar la ventana afectada.
2. Reejecutar la ingesta: la extracción de `tms` continúa desde el watermark de cada tabla (§4.1) y los archivos ERP se deciden por su hash (§4.2).
3. Reconstruir los modelos afectados: `dbt build --select <modelo>+`.
4. Verificar conteos contra la fuente.
5. Registrar el incidente y la causa.

Las dos vías son idempotentes: la extracción de `tms` por watermark y la carga de archivos por hash registrado en `meta.ingested_files`. Repetir una ingesta sin cambios en la fuente no inserta filas. `raw` es append-only: nada de lo que sigue borra filas de `raw`.

Consultas desde el host (no hay `psql` en el host; se usa el del contenedor):

```bash
PSQL="docker exec -i nexora-postgres psql -U nexora -d nexora -v ON_ERROR_STOP=1 -At"
```

### 4.1 Extracción de `tms` desde el watermark

```bash
python -m nexora.ingestion.batch.loaders --source tms
```

Cada ejecución recorre las catorce tablas de `tms` en el orden de publicación y extrae, por tabla, las filas de `tms.<tabla>` con `updated_at` posterior a su watermark; inserta `0` filas si no hay nuevas. Estado de los watermarks y filas pendientes de una tabla (`<tabla>` es cualquiera de las de `tms`, por ejemplo `shipment_events`):

```bash
$PSQL -c "select * from meta.batch_watermarks order by 1"
$PSQL -c "select count(*) from tms.<tabla> t left join meta.batch_watermarks w on w.source_table = 'tms.<tabla>' where w.watermark_ts is null or t.updated_at > w.watermark_ts"
```

Si una extracción falla, su transacción se revierte: la tabla afectada no recibe filas y su watermark no cambia, así que basta con ejecutarla de nuevo. Las tablas ya extraídas en esa ejecución conservan su avance.

Si falta una tabla en `tms`, la ejecución se detiene con `LookupError: no existe la tabla de origen tms.<tabla>`. Las tablas anteriores de esa misma ejecución ya quedaron extraídas, con sus filas en `raw` y su watermark; la que falta y las siguientes no se intentan, y no se crea nada para ellas en `raw` ni en `meta`. Se corrige publicando la carpeta completa y repitiendo la extracción; las tablas ya extraídas no traen filas repetidas:

```bash
bash scripts/seed_db.sh demo
python -m nexora.ingestion.batch.loaders --source tms
```

Retroceder un watermark vuelve a extraer la ventana entre el valor nuevo y el actual:

```bash
$PSQL -c "update meta.batch_watermarks set watermark_ts = '<instante ISO 8601>' where source_table = 'tms.shipments'"
$PSQL -c "delete from meta.batch_watermarks where source_table = 'tms.shipments'"   # vuelve a extraer la tabla completa
```

Solo procede si las filas de esa ventana no están en `raw`. Como `raw` es append-only, una ventana que ya está en `raw` entra de nuevo con otro `run_id`: un mismo `shipment_id` queda en dos `run_id` y los tests `unique` de `shipment_id` en `stg_tms__shipments_valid` y `fact_shipments` fallan en `dbt build`. Antes y después de retroceder, esta consulta debe devolver `0`:

```bash
$PSQL -c "select count(*) from (select shipment_id from raw.shipments group by 1 having count(distinct run_id) > 1) s"
```

Generar y medir el perfil `benchmark` sin publicarlo. Solo se genera a archivos: el publicador rechaza su carpeta con código de salida 2 y no abre conexión, y `scripts/seed_db.sh` no acepta ese perfil. La salida va a una carpeta ignorada por git y se borra al terminar:

```bash
/usr/bin/time -v python -m nexora.generators.cli --scale benchmark --out data/tmp/benchmark 2> data/tmp/time_benchmark.txt
grep -E 'Elapsed \(wall clock\)|Maximum resident set size|Exit status' data/tmp/time_benchmark.txt
du -sb data/tmp/benchmark
rm -rf data/tmp/benchmark
```

Las cifras medidas se registran en `benchmarks/results/` con su fecha, comando y salida.

### 4.2 Archivos ERP

```bash
python -m nexora.ingestion.batch.loaders --source files --input data/demo
```

Un archivo cuyo hash ya está en `meta.ingested_files` se omite con `0` filas, aunque tenga otro nombre; un archivo modificado tiene un hash nuevo y se carga. Para volver a cargar a propósito un archivo ya registrado, se borra su fila del registro y se ejecuta de nuevo la carga:

```bash
$PSQL -c "select target_table, source_file, source_file_hash, row_count from meta.ingested_files order by ingested_at"
$PSQL -c "delete from meta.ingested_files where target_table = 'customers' and source_file_hash = '<hash>'"
```

La recarga añade otra copia de los clientes en `raw.customers`; `stg_erp__customers` conserva una fila por `customer_id`, la de `ingested_at` más reciente.

## 5. Archivo y restauración

- A los 6 meses, `raw` pasa a Parquet en almacenamiento de objetos.
- El DAG de archivo verifica el conteo antes y después de exportar.
- La restauración se prueba al menos una vez por trimestre:

```bash
python scripts/restore_from_archive.py --partition 2025-01
```

Si la restauración no cuadra, el archivo se considera inválido y se repite la exportación.

## 6. Respaldos

| Qué | Frecuencia | Retención |
|---|---|---|
| `pg_dump` del warehouse | Semanal | 30 días |
| Metadatos de Superset | Semanal | 30 días |
| Definiciones de dashboards | En cada cambio | Indefinida (git) |

## 7. Seguridad operativa

- SSH con llave; contraseña deshabilitada.
- Cortafuegos cerrado salvo SSH desde IP conocida.
- Sin puertos públicos: el acceso a BI va por túnel con autenticación.
- Rotación de secretos documentada y ejecutada al menos cada trimestre.
- `.env` con permisos restringidos y fuera de git.

### 7.1 Rotación de contraseñas locales de los roles

Las contraseñas de `airflow`, `superset`, `etl_writer`, `bi_reader`, `dq_runner` y `tms_writer` salen de `<ROL>_PASSWORD` en `.env` y se aplican solo al crear el volumen de PostgreSQL. Cambiar `.env` con el volumen ya creado no cambia la contraseña en la base. El valor va dentro de URLs de conexión: sin `@`, `:` ni `/`.

**Opción A, con borrado de datos.** Editar `.env` y recrear el volumen. `make reset` borra el warehouse y los metadatos de Airflow y Superset:

```bash
make reset
make up
```

**Opción B, sin borrar datos.** Editar `<ROL>_PASSWORD` en `.env` y aplicar el mismo valor con `ALTER ROLE`. La contraseña se pasa como variable de entorno y `\getenv` la lee dentro de `psql`, así no queda escrita en el comando SQL. Ejemplo para `etl_writer`:

```bash
set -a; . ./.env; set +a
NEW_PASSWORD="$ETL_WRITER_PASSWORD" docker exec -i -e NEW_PASSWORD nexora-postgres \
    psql -U nexora -d nexora -v ON_ERROR_STOP=1 <<'SQL'
\getenv new_password NEW_PASSWORD
ALTER ROLE etl_writer PASSWORD :'new_password';
SQL
make up
```

`make up` recrea los contenedores cuya configuración cambió, de modo que Airflow toma las nuevas `AIRFLOW_PASSWORD` y `ETL_WRITER_PASSWORD`. Después de rotar:

| Rol | Qué más hacer |
|---|---|
| `airflow`, `etl_writer` | `make up` |
| `superset` | `make up-bi` |
| `bi_reader` | `make import-superset`, que fija la contraseña de la conexión de Superset desde `.env` |
| `tms_writer` | Nada más: el publicador lee `TMS_WRITER_PASSWORD` del entorno o de `.env` en cada ejecución |
| `dq_runner` | Nada más: ningún servicio del stack se conecta hoy con este rol |

## 8. Instancia gratuita: inactividad

Los proveedores gratuitos pueden reclamar instancias ociosas. Mitigaciones:

1. Pipeline diario y latido sintético moderado.
2. Monitoreo de disponibilidad con alerta.
3. Si la instancia se detiene, reiniciar; los volúmenes se conservan.
4. Documentar el estado en el registro de incidentes.

## 9. Contactos y escalamiento

| Nivel | Quién | Cuándo |
|---|---|---|
| 1 | Responsable técnico | Cualquier alerta |
| 2 | Responsable de datos | Incidente alto o crítico |
| 3 | Responsable de negocio | Afectación a indicadores publicados |

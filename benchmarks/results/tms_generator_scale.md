# Escala del generador TMS

Línea base medida del generador de datos sintéticos, del publicador de TMS y del conector de lote. Se miden dos perfiles con la misma semilla, el mismo número de meses de historia y la misma fecha de corte: `local` (200 000 envíos base), generado, publicado en `tms` y extraído a `raw`, y `benchmark` (2 000 000 de envíos base), que solo se genera a archivos.

No se fijan umbrales: las cifras describen esta máquina y este conjunto de datos. Repetir la medición exige el mismo hardware, el mismo volumen y los mismos comandos.

## Entorno

| Elemento | Valor |
|---|---|
| Fecha de la medición | 2026-11-15 (UTC-5), cuatro corridas en serie entre `2026-11-15T22:06:11-05:00` y `2026-11-15T22:30:00-05:00` |
| Máquina | 7 939 MB de RAM (2 615 MB disponibles al empezar y 2 418 MB al terminar), 4 096 MB de swap, 20 núcleos lógicos; disco `/` con 932 G libres al empezar y 926 G al terminar |
| Servicios arriba | Postgres 16, Mongo 7, Airflow 3.4 (api, scheduler, dag-processor, triggerer) y el perfil de BI; nada más ejecutándose durante la medición |
| Postgres | `127.0.0.1:5432`, base `nexora`, `mem_limit: 1g`, sesiones en UTC |
| Generador | semilla 42, 24 meses de historia y fecha de corte 2026-11-09, iguales en los dos perfiles |
| Instrumento | `/usr/bin/time -v` (GNU time); RSS máximo = `Maximum resident set size` |

## Volumen medido

| Perfil | Envíos base | `shipments.csv` | `shipment_events.csv` | CSV en disco |
|---|---|---|---|---|
| `benchmark` | 2 000 000 | 2 040 000 filas | 20 506 780 filas | 3 634 233 068 B (3,63 GB) |
| `local` | 200 000 | 204 000 filas | 2 051 118 filas | 379 486 443 B (379,5 MB) |

`shipments.csv` incluye las copias duplicadas (2 %), por eso supera el número de envíos base. La publicación y la extracción recorren las 14 tablas TMS; la extracción dejó en `raw` el mismo número de filas que había en `tms` (204 000 envíos, 2 051 118 eventos).

## Resultados

| Métrica | Valor | Fecha | Artefacto |
|---|---|---|---|
| `benchmark`: generación, tiempo | 4:51.40 (min:s) | 2026-11-15 | `data/tmp/time_benchmark.txt` |
| `benchmark`: generación, RSS máximo | 218 648 KB (213,5 MiB) | 2026-11-15 | `data/tmp/time_benchmark.txt` |
| `benchmark`: generación, disco | 3 634 233 068 B (3,63 GB) | 2026-11-15 | `du -sb data/tmp/benchmark` |
| `local`: generación, tiempo | 0:30.37 (min:s) | 2026-11-15 | `data/tmp/time_local_generate.txt` |
| `local`: generación, RSS máximo | 45 556 KB (44,5 MiB) | 2026-11-15 | `data/tmp/time_local_generate.txt` |
| `local`: generación, disco | 379 486 443 B (379,5 MB) | 2026-11-15 | `du -sb data/tmp/local` |
| `local`: publicación, tiempo | 0:59.74 (min:s) | 2026-11-15 | `data/tmp/time_local_publish.txt` |
| `local`: publicación, RSS máximo | 204 916 KB (200,1 MiB) | 2026-11-15 | `data/tmp/time_local_publish.txt` |
| `local`: publicación, disco | 670 892 032 B (639,8 MiB) | 2026-11-15 | `it_scale_tms`, 14 tablas, `pg_total_relation_size` |
| `local`: extracción, tiempo | 0:26.95 (min:s) | 2026-11-15 | `data/tmp/time_local_extract.txt` |
| `local`: extracción, RSS máximo | 58 524 KB (57,2 MiB) | 2026-11-15 | `data/tmp/time_local_extract.txt` |
| `local`: extracción, disco | 950 099 968 B (906,1 MiB) | 2026-11-15 | `it_scale_raw`, 14 tablas, `pg_total_relation_size` |
| `local`: extracción, metadatos de watermark | 32 768 B (32 KiB) | 2026-11-15 | `it_scale_meta`, 1 tabla, `pg_total_relation_size` |

Los esquemas `it_scale_*` son temporales: se crean vacíos, se miden y se borran con `drop schema … cascade` al terminar. `tms`, `raw` y `meta` principales no se tocan en ninguna de las mediciones.

## Salidas literales de `/usr/bin/time -v`

```text
data/tmp/time_benchmark.txt
	Elapsed (wall clock) time (h:mm:ss or m:ss): 4:51.40
	Maximum resident set size (kbytes): 218648
	Exit status: 0
```

```text
data/tmp/time_local_generate.txt
	Elapsed (wall clock) time (h:mm:ss or m:ss): 0:30.37
	Maximum resident set size (kbytes): 45556
	Exit status: 0
```

```text
data/tmp/time_local_publish.txt
	Elapsed (wall clock) time (h:mm:ss or m:ss): 0:59.74
	Maximum resident set size (kbytes): 204916
	Exit status: 0
```

```text
data/tmp/time_local_extract.txt
	Elapsed (wall clock) time (h:mm:ss or m:ss): 0:26.95
	Maximum resident set size (kbytes): 58524
	Exit status: 0
```

## Comandos

Desde `repo/`, con `repo/.venv` activo y el stack arriba. `PSQL` usa el contenedor de Postgres porque no hay cliente `psql` en el host.

```bash
PSQL="docker exec -i nexora-postgres psql -U nexora -d nexora -v ON_ERROR_STOP=1 -At"
```

Esquemas temporales de la corrida:

```bash
$PSQL -c "create schema it_scale_tms; create schema it_scale_raw; create schema it_scale_meta"
```

Generación a archivos, con el disco ocupado por cada carpeta:

```bash
/usr/bin/time -v python -m nexora.generators.cli --scale benchmark --out data/tmp/benchmark 2> data/tmp/time_benchmark.txt
du -sb data/tmp/benchmark

/usr/bin/time -v python -m nexora.generators.cli --scale local --out data/tmp/local 2> data/tmp/time_local_generate.txt
du -sb data/tmp/local
```

Publicación en `tms` y extracción a `raw`. La contraseña viaja por `PGPASSWORD` desde `.env` y no aparece en la línea de comandos:

```bash
set -a; . ./.env; set +a
DSN="host=127.0.0.1 port=${POSTGRES_PORT:-5432} dbname=nexora user=${POSTGRES_USER:-nexora}"

PGPASSWORD="$POSTGRES_PASSWORD" /usr/bin/time -v python -m nexora.generators.tms_publisher \
    --input data/tmp/local --schema it_scale_tms --dsn "$DSN" 2> data/tmp/time_local_publish.txt

PGPASSWORD="$POSTGRES_PASSWORD" /usr/bin/time -v python -m nexora.ingestion.batch.loaders \
    --source tms --tms-schema it_scale_tms --raw-schema it_scale_raw \
    --meta-schema it_scale_meta --dsn "$DSN" 2> data/tmp/time_local_extract.txt
```

Disco de cada esquema y líneas de tiempo, RSS y salida:

```bash
$PSQL -c "select n.nspname, sum(pg_total_relation_size(c.oid)) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('it_scale_tms','it_scale_raw') and c.relkind = 'r' group by 1 order by 1"
$PSQL -c "select n.nspname, sum(pg_total_relation_size(c.oid)) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'it_scale_meta' and c.relkind = 'r' group by 1"

grep -E 'Elapsed \(wall clock\)|Maximum resident set size|Exit status' data/tmp/time_*.txt
```

Limpieza de los esquemas temporales:

```bash
$PSQL -c "drop schema it_scale_tms cascade; drop schema it_scale_raw cascade; drop schema it_scale_meta cascade"
```

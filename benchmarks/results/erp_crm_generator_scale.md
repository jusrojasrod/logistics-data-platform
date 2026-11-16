# Escala del generador ERP/CRM

Medición del generador de datos sintéticos con las fuentes ERP y CRM (tarifas, contactos, contratos, leads, facturas, líneas de factura, pagos, liquidaciones de carriers y costos de envío) y del conector de archivos. Se miden dos perfiles con la misma semilla, el mismo número de meses de historia y la misma fecha de corte: `local` (200 000 envíos base), generado y cargado desde archivos a `raw`, y `benchmark` (2 000 000 de envíos base), que solo se genera a archivos.

Cada perfil escribe sus CSV principales en la carpeta de salida y las filas tardías en `late/`. La carga de `local` se hace en dos pasos: primero la carpeta y después `late/`.

No se fijan umbrales: las cifras describen esta máquina y este conjunto de datos. Repetir la medición exige el mismo hardware, el mismo volumen y los mismos comandos.

## Entorno

| Elemento | Valor |
|---|---|
| Fecha de la medición | 2026-11-16 (UTC-5), corridas en serie entre `2026-11-16T15:38:31-05:00` y `2026-11-16T15:46:09-05:00` |
| Máquina | 7 939 MB de RAM (2 928 MB disponibles al empezar y 2 322 MB al terminar), 4 096 MB de swap, 20 núcleos lógicos; disco `/` con 932 G libres al empezar y 927 G al terminar |
| Servicios arriba | Postgres 16.4, Mongo 7.0, Airflow 3.4 (api, scheduler, dag-processor, triggerer); sin el perfil de BI ni el de streaming |
| Postgres | `127.0.0.1:5432`, base `nexora`, límite de memoria del contenedor 1 073 741 824 B (1 GiB) |
| Generador | semilla 42, 24 meses de historia y fecha de corte 2026-11-09, iguales en los dos perfiles |
| Instrumento | `/usr/bin/time -v` (GNU time); RSS máximo = `Maximum resident set size` |

## Volumen medido

Filas por tabla ERP/CRM según `late_files` de `defect_manifest.json` (total = principal + tardío):

| Tabla | `benchmark` total | `benchmark` tardías | `local` total | `local` tardías |
|---|---|---|---|---|
| `tariffs` | 1 800 | 47 | 1 800 | 47 |
| `contacts` | 450 | 14 | 450 | 14 |
| `contracts` | 150 | 4 | 150 | 4 |
| `leads` | 500 | 17 | 500 | 17 |
| `invoices` | 1 845 497 | 55 328 | 184 609 | 5 448 |
| `invoice_lines` | 3 690 994 | 110 665 | 369 218 | 10 907 |
| `payments` | 1 599 949 | 47 925 | 160 110 | 4 718 |
| `carrier_settlements` | 906 205 | 27 148 | 90 952 | 2 760 |
| `shipment_costs` | 2 998 455 | 90 317 | 299 088 | 8 859 |

| Perfil | CSV en disco (carpeta completa) | Nueve CSV principales ERP/CRM | `late/` |
|---|---|---|---|
| `benchmark` | 4 537 439 291 B (4,54 GB) | 876 097 916 B (876,1 MB) | 27 106 680 B (27,1 MB) |
| `local` | 469 557 907 B (469,6 MB) | 87 400 546 B (87,4 MB) | 2 669 301 B (2,7 MB) |

La carpeta completa incluye las 15 tablas TMS y de clientes, los nueve CSV principales ERP/CRM, `late/` y el manifiesto. Tras las dos cargas de `local`, cada tabla de `it_scale_raw` tiene el total de la tabla de arriba, y `it_scale_meta.ingested_files` tiene 19 filas (10 archivos principales, incluido `customers.csv`, y 9 tardíos).

## Resultados

| Métrica | Valor | Fecha | Artefacto |
|---|---|---|---|
| `benchmark`: generación, tiempo | 6:16.20 (min:s) | 2026-11-16 | `data/tmp/time_benchmark.txt` |
| `benchmark`: generación, RSS máximo | 222 628 KB (217,4 MiB) | 2026-11-16 | `data/tmp/time_benchmark.txt` |
| `benchmark`: generación, disco | 4 537 439 291 B (4,54 GB) | 2026-11-16 | `du -sb data/tmp/benchmark` |
| `benchmark`: generación, disco de `late/` | 27 106 680 B (27,1 MB) | 2026-11-16 | `du -sb data/tmp/benchmark/late` |
| `local`: generación, tiempo | 0:37.32 (min:s) | 2026-11-16 | `data/tmp/time_local_generate.txt` |
| `local`: generación, RSS máximo | 58 972 KB (57,6 MiB) | 2026-11-16 | `data/tmp/time_local_generate.txt` |
| `local`: generación, disco | 469 557 907 B (469,6 MB) | 2026-11-16 | `du -sb data/tmp/local` |
| `local`: generación, disco de `late/` | 2 669 301 B (2,7 MB) | 2026-11-16 | `du -sb data/tmp/local/late` |
| `local`: carga de la carpeta, tiempo | 0:04.94 (min:s) | 2026-11-16 | `data/tmp/time_local_load.txt` |
| `local`: carga de la carpeta, RSS máximo | 66 988 KB (65,4 MiB) | 2026-11-16 | `data/tmp/time_local_load.txt` |
| `local`: carga de `late/`, tiempo | 0:00.34 (min:s) | 2026-11-16 | `data/tmp/time_local_load_late.txt` |
| `local`: carga de `late/`, RSS máximo | 49 600 KB (48,4 MiB) | 2026-11-16 | `data/tmp/time_local_load_late.txt` |
| `local`: carga, disco en `raw` | 263 192 576 B (251,0 MiB) | 2026-11-16 | `it_scale_raw`, 10 tablas, `pg_total_relation_size` |
| `local`: carga, disco del registro de archivos | 32 768 B (32 KiB) | 2026-11-16 | `it_scale_meta`, 1 tabla, `pg_total_relation_size` |

El disco en `raw` se mide después de las dos cargas y suma las diez tablas del conector de archivos (`customers` y las nueve ERP/CRM). Los esquemas `it_scale_*` son temporales: se crean vacíos, se miden y se borran con `drop schema … cascade` al terminar. `tms`, `raw` y `meta` principales no se tocan en ninguna de las mediciones.

## Salidas literales de `/usr/bin/time -v`

```text
data/tmp/time_benchmark.txt
	Elapsed (wall clock) time (h:mm:ss or m:ss): 6:16.20
	Maximum resident set size (kbytes): 222628
	Exit status: 0
```

```text
data/tmp/time_local_generate.txt
	Elapsed (wall clock) time (h:mm:ss or m:ss): 0:37.32
	Maximum resident set size (kbytes): 58972
	Exit status: 0
```

```text
data/tmp/time_local_load.txt
	Elapsed (wall clock) time (h:mm:ss or m:ss): 0:04.94
	Maximum resident set size (kbytes): 66988
	Exit status: 0
```

```text
data/tmp/time_local_load_late.txt
	Elapsed (wall clock) time (h:mm:ss or m:ss): 0:00.34
	Maximum resident set size (kbytes): 49600
	Exit status: 0
```

Disco de los esquemas temporales:

```text
it_scale_meta|32768
it_scale_raw|263192576
```

## Comandos

Desde `repo/`, con `repo/.venv` activo y el stack arriba. `PSQL` usa el contenedor de Postgres porque no hay cliente `psql` en el host.

```bash
PSQL="docker exec -i nexora-postgres psql -U nexora -d nexora -v ON_ERROR_STOP=1 -At"
```

Generación de `benchmark`, con el disco de la carpeta y de `late/` y el resumen del manifiesto:

```bash
/usr/bin/time -v python -m nexora.generators.cli --scale benchmark --out data/tmp/benchmark 2> data/tmp/time_benchmark.txt
du -sb data/tmp/benchmark
du -sb data/tmp/benchmark/late
python -c "import json; m=json.load(open('data/tmp/benchmark/defect_manifest.json')); print(m['scale'], m['shipments'], [(e['table'], e['rows'], e['late_rows']) for e in m['late_files']])"
```

Esquemas temporales y generación de `local`:

```bash
$PSQL -c "create schema it_scale_raw; create schema it_scale_meta"

/usr/bin/time -v python -m nexora.generators.cli --scale local --out data/tmp/local 2> data/tmp/time_local_generate.txt
du -sb data/tmp/local
du -sb data/tmp/local/late
```

Carga de la carpeta y después de `late/`. La contraseña viaja por `PGPASSWORD` desde `.env` y no aparece en la línea de comandos:

```bash
set -a; . ./.env; set +a
DSN="host=127.0.0.1 port=${POSTGRES_PORT:-5432} dbname=nexora user=${POSTGRES_USER:-nexora}"

PGPASSWORD="$POSTGRES_PASSWORD" /usr/bin/time -v python -m nexora.ingestion.batch.loaders \
    --source files --input data/tmp/local --raw-schema it_scale_raw \
    --meta-schema it_scale_meta --dsn "$DSN" 2> data/tmp/time_local_load.txt

PGPASSWORD="$POSTGRES_PASSWORD" /usr/bin/time -v python -m nexora.ingestion.batch.loaders \
    --source files --input data/tmp/local/late --raw-schema it_scale_raw \
    --meta-schema it_scale_meta --dsn "$DSN" 2> data/tmp/time_local_load_late.txt
```

Disco de cada esquema y líneas de tiempo, RSS y salida:

```bash
$PSQL -c "select n.nspname, sum(pg_total_relation_size(c.oid)) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('it_scale_raw', 'it_scale_meta') and c.relkind = 'r' group by 1 order by 1"

grep -E 'Elapsed \(wall clock\)|Maximum resident set size|Exit status' data/tmp/time_*.txt
```

Limpieza de los esquemas temporales:

```bash
$PSQL -c "drop schema it_scale_raw cascade; drop schema it_scale_meta cascade"
```

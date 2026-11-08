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
| No hay datos nuevos | `meta.pipeline_runs` y frescura | Revisar watermark y ejecutar de nuevo |
| Cuarentena crece | `select rule_id, count(*) from quarantine.*` | Identificar regla y fuente |
| Dashboard lento | `pg_stat_statements` | Revisar consulta y caché |
| Superset no arranca | `docker compose logs superset` | Migración pendiente o secreto vacío |

## 4. Reproceso

1. Identificar la ventana afectada.
2. Reejecutar la ingesta con el mismo `run_id` del archivo, o desde el watermark anterior.
3. Reconstruir los modelos afectados: `dbt build --select <modelo>+`.
4. Verificar conteos contra la fuente.
5. Registrar el incidente y la causa.

Las cargas son idempotentes por clave natural y por hash de archivo: reprocesar no duplica.

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

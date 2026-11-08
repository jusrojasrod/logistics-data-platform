# Dashboards de Superset

Las definiciones se versionan como artefactos exportados, no se editan a mano en la interfaz.

## Estructura

| Carpeta | Contenido |
|---|---|
| `datasets/` | Definición de datasets, columnas calculadas y métricas |
| `charts/` | Gráficos por dashboard |
| `dashboards/` | Layout, filtros y permisos |
| `rls/` | Filtros de seguridad a nivel de fila por rol |

## Flujo

1. Construir el dataset sobre `marts`, nunca sobre `raw`.
2. Nombrar las métricas igual que el catálogo de KPIs.
3. Exportar con `make export-superset`.
4. Revisar el diff antes de versionar: nombres, descripciones y consultas.
5. Importar en otro entorno con `superset import-dashboards`.

## Reglas

- Un dataset solo lee `marts` o `core`.
- Ninguna métrica se define dos veces.
- Todo dashboard declara su rol y su filtro de fila.
- Los colores y el formato se ajustan una vez, en el tema, no por gráfico.

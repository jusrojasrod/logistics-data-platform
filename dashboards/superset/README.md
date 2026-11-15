# Dashboards de Superset

Los YAML de esta carpeta son la fuente de verdad versionada de los dashboards. La instancia de Superset se reconstruye importándolos; los cambios hechos en la interfaz solo cuentan cuando se exportan y vuelven a esta carpeta como YAML.

## Estructura

| Carpeta | Contenido |
|---|---|
| `databases/` | Conexión de solo lectura a la base `nexora` con el rol `bi_reader`; la contraseña va enmascarada |
| `datasets/` | Definición de datasets, columnas calculadas y métricas |
| `charts/` | Gráficos por dashboard |
| `dashboards/` | Layout y filtros |
| `rls/` | Filtros de seguridad a nivel de fila por rol (vacía por ahora) |

## Flujo: importar → revisar → exportar

1. Importar los YAML en la instancia local con el perfil BI levantado:

   ```bash
   make up-bi
   make import-superset
   ```

   `scripts/import_superset.sh` empaqueta `databases/`, `datasets/`, `charts/` y `dashboards/` en un bundle temporal, lo importa con `superset import-dashboards` y después fija la URI de la base `nexora`, porque el YAML la guarda con la contraseña enmascarada.
2. Revisar o editar el dashboard en la interfaz: dataset sobre `marts`, métricas con el mismo nombre que el catálogo de KPIs.
3. Exportar con `make export-superset`. Deja el bundle en `dashboards/superset/dashboards.zip`.
4. Descomprimir el bundle y copiar sus YAML a la carpeta que corresponda (`databases/`, `datasets/`, `charts/`, `dashboards/`), con la contraseña enmascarada.
5. Revisar el diff de los YAML antes de versionar: nombres, descripciones, consultas y ausencia de credenciales.

## Bundle y credenciales

- El zip exportado (`dashboards/superset/*.zip`) es un artefacto local: lo ignora git y no se versiona.
- La contraseña de `bi_reader` no se guarda en los YAML. Se fija al importar con la variable de entorno `BI_READER_PASSWORD`; si no está definida, se usa la contraseña de desarrollo del rol, la misma de `infra/postgres/init/02_roles.sql`.

  ```bash
  BI_READER_PASSWORD=<contraseña> make import-superset
  ```

## Reglas

- Un dataset solo lee `marts`; nunca `raw` ni `stg`.
- Ninguna métrica se define dos veces.
- Los filtros de fila por rol se declaran en `rls/` cuando el dashboard los necesite; el dashboard de operación todavía no los tiene.
- Los colores y el formato se ajustan una vez, en el tema, no por gráfico.

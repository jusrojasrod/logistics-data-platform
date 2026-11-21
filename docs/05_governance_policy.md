# Política de gobierno de datos

## 1. Principios

1. **El dato tiene dueño.** Cada dominio tiene un responsable de negocio y un responsable técnico.
2. **Definiciones únicas.** Cada indicador se define en el catálogo y se reutiliza.
3. **Privacidad por diseño.** Lo sensible se clasifica y se enmascara por defecto.
4. **Mínimo privilegio.** Cada rol accede solo a lo que necesita.
5. **Trazabilidad.** Todo dato tiene linaje y toda transformación está versionada.
6. **Calidad medible.** La calidad se monitorea; no se asume.
7. **Cambio controlado.** Los cambios pasan por revisión y pruebas.

## 2. Roles

| Rol | Responsabilidad |
|---|---|
| Data Owner | Aprueba definiciones, accesos y políticas del dominio |
| Data Steward | Mantiene catálogo, diccionario y reglas de calidad |
| Data Custodian | Administra infraestructura, respaldos y seguridad técnica |
| Data Consumer | Consume dashboards según su rol |
| Data Engineer | Construye pipelines y modelos |

## 3. Clasificación

| Nivel | Descripción | Ejemplo |
|---|---|---|
| Público | Sin restricción | Catálogo de rutas |
| Interno | Uso interno | Métricas agregadas |
| Confidencial | Acceso restringido | Tarifas, márgenes |
| Restringido | Dato personal o sensible | Documento de conductor, contacto de cliente |

## 4. Datos personales

Campos marcados como PII:

- Nombre, documento, teléfono y correo del conductor.
- Nombre, documento, teléfono, correo y dirección de contacto del cliente.

Campos PII del conductor que existen hoy, en `tms.drivers` y `raw.drivers`:

| Campo | Contenido |
|---|---|
| `driver_name` | Nombre del conductor |
| `driver_document` | Documento de identidad |
| `driver_phone` | Teléfono |
| `driver_email` | Correo electrónico |

Hoy los leen solo `tms_writer`, que crea y escribe las tablas de `tms`, y `etl_writer`, que lee `tms` y es dueño de las tablas de `raw`. `bi_reader` y `dq_runner` no tienen permiso de lectura sobre `tms` ni sobre `raw`. Las tablas de conductores todavía no tienen capas de consumo: el enmascaramiento de estos campos llega con ellas, como el del resto de PII.

Campos PII del contacto del cliente que existen hoy, en `raw.contacts`, cargada desde los archivos del CRM `contacts.csv` y `late/contacts_late.csv`:

| Campo | Contenido |
|---|---|
| `customer_contact_name` | Nombre del contacto |
| `contact_document` | Documento de identidad del contacto |
| `customer_phone` | Teléfono |
| `customer_email` | Correo electrónico |

`contact_document` se clasifica como PII igual que el documento del conductor. La tabla no tiene dirección del contacto: solo la ciudad del cliente, que no es PII. Hoy la lee solo `etl_writer`, que la crea en la primera carga y es su dueño; `tms_writer` no tiene acceso a `raw`, y `bi_reader` y `dq_runner` no tienen permiso de lectura sobre `raw`. `raw.contacts` todavía no tiene capas de consumo: el enmascaramiento de estos campos llega con ellas, como el de los campos del conductor. Los archivos de origen están en `data/`, fuera del control de versiones.

Tratamiento:

- Enmascaramiento en las capas de consumo (`***`, hash o últimos cuatro caracteres).
- Acceso solo para roles autorizados.
- Registro de auditoría de consultas.
- Retención limitada y borrado seguro.

Todos los valores son ficticios y se generan con una semilla fija.

## 5. Retención

| Dato | Caliente | Archivo | Total |
|---|---|---|---|
| `raw` | 6 meses en PostgreSQL | 18 meses en Parquet | 24 meses |
| `stg`, `core` | 24 meses | — | 24 meses |
| Agregados de `marts` | Indefinida | — | Indefinida |
| Cuarentena | 90 días | — | 90 días |
| Logs de pipeline | 90 días | — | 90 días |
| Respaldos | 30 días | — | 30 días |
| PII | 24 meses máximo | — | 24 meses |

La restauración desde archivo está descrita en el runbook y se prueba al menos una vez.

## 6. Matriz de accesos

| Rol | Operación | Comercial | Finanzas | Gobierno | PII |
|---|---|---|---|---|---|
| Admin | Total | Total | Total | Total | Total |
| Operaciones | Total | Lectura | Sin acceso | Lectura | Enmascarado |
| Comercial | Lectura | Total | Lectura | Lectura | Enmascarado |
| Finanzas | Lectura | Lectura | Total | Lectura | Enmascarado |
| Gobierno | Lectura | Lectura | Lectura | Total | Lectura auditada |
| Carrier externo | Solo sus envíos | Sin acceso | Sin acceso | Sin acceso | Sin acceso |

La seguridad a nivel de fila se implementa en Superset con filtros por usuario.

## 7. Linaje

- **Técnico:** el grafo de dependencias de dbt.
- **De negocio:** relación indicador → modelo → tabla → fuente.
- **De columna:** documentado en el diccionario para los campos críticos.

## 8. Ciclo de vida y cambios

- Todo cambio en un modelo pasa por revisión.
- Los tests y la construcción de dbt corren antes de integrar.
- Un cambio que afecte definiciones o arquitectura se registra como decisión.
- Se versionan el modelo, los dashboards y los roles.

## 9. Incidentes

| Severidad | Ejemplo | Respuesta |
|---|---|---|
| Crítica | Dato publicado incorrecto o acceso indebido | Contención inmediata y comunicación |
| Alta | Pipeline diario caído o frescura fuera de SLO | Diagnóstico el mismo día |
| Media | Cuarentena por encima del umbral | Revisión en 48 h |
| Baja | Documentación desactualizada | Siguiente ciclo |

Cada incidente se registra con causa raíz y acciones. Los críticos llevan post-mortem.

## 10. Token de la API y secretos

| Variable | Uso | Valor versionado |
|---|---|---|
| `NEXORA_API_TOKEN` | Token Bearer del mock TMS; lo exigen los recursos `/v1/*` y lo envía el cliente HTTP | Solo un valor de desarrollo en `.env.example`; nunca un token real |
| `MONGO_APP_PASSWORD` | Contraseña del usuario de aplicación `nexora_app` de MongoDB | Valor de desarrollo en `.env.example` |
| `NEXORA_MONGO_APP_DSN` | DSN de aplicación de MongoDB, con `authSource=admin` | Valor de desarrollo en `.env.example` |
| `NEXORA_PG_DSN` | Cadena de conexión de PostgreSQL para `meta.api_runs` y `meta.datasets` | Sin contraseña; la de `etl_writer` se toma de `ETL_WRITER_PASSWORD` |

`.env` no está versionado. Ningún token ni contraseña real se guarda en el repositorio; los valores de `.env.example` son de desarrollo.

### 10.1 Uso del token

- El mock exige `Authorization: Bearer <token>` en todo `/v1/*`. Sin token o con un token distinto responde `401` con `WWW-Authenticate: Bearer`.
- `/health` queda sin token para el healthcheck del contenedor, pero la aplicación no se crea si `NEXORA_API_TOKEN` está vacía: el servicio no puede arrancar sin token configurado.
- El cliente lee el token de la configuración y lo envía en la cabecera `Authorization`. No lo registra ni aparece en su representación; la CLI no imprime cabeceras y sus mensajes de error no exponen la DSN.
- El token viaja por variable de entorno en los contenedores del mock y de Airflow, definida en el compose a partir de `.env`.

### 10.2 Rotación del token

1. Editar `NEXORA_API_TOKEN` en `.env`, sin escribirlo en ningún archivo versionado.
2. Recrear los contenedores que leen la variable: `make up` para el stack, o `docker compose up -d mock-api` si solo cambia el mock. El mock y el cliente toman el valor al arrancar.
3. Comprobar que `/health` sigue respondiendo y que una petición a `/v1/shipments` sin token devuelve `401`. Si hay una extracción en curso, repetirla con un `run_id` nuevo: con la clave `(run_id, endpoint, page)` repetida, el mismo contenido no inserta nada y otro contenido falla en lugar de reescribir.

La rotación de las contraseñas de los roles de PostgreSQL está en [`07_runbook.md`](07_runbook.md) §7.1. La contraseña del usuario de aplicación de MongoDB se toma de `MONGO_APP_PASSWORD` y la DSN de `NEXORA_MONGO_APP_DSN`; ambos se leen del entorno.

### 10.3 Usuario de aplicación de MongoDB

- `infra/mongo/init/01_app_user.js` crea `nexora_app` en la base `admin` con `readWrite` solo en las bases `raw` y `meta`. El usuario root queda para administración.
- El init es idempotente: si el usuario ya existe, ajusta sus roles a ese mismo par de bases. Crea `raw.api_payloads` y `meta.api_schema_drift` con el validador de los seis metadatos y el índice único `(run_id, endpoint, page)` en la primera colección (§11.1 del diccionario).
- En un volumen ya creado, `bash scripts/mongo_app_user.sh` aplica el mismo init sin `make reset` y sin borrar datos; la contraseña se resuelve desde la configuración del compose y no se imprime.
- El cliente, el runner, la CLI y las pruebas de integración se conectan con ese usuario; las pruebas usan bases aisladas y no crean usuarios ni usan root.

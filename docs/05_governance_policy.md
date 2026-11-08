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

- Documento, teléfono y correo del conductor.
- Nombre, teléfono, correo y dirección de contacto del cliente.

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

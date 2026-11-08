# Contexto de negocio

> Nexora Logística es una empresa ficticia. Los datos del proyecto son sintéticos y se generan con una semilla fija.

## 1. La operación

Nexora Logística mueve mercancía entre ciudades con dos modelos de ejecución:

- **Flota propia:** vehículos y conductores de la empresa.
- **Carriers terceros:** transportadores contratados por viaje o por ruta.

La operación cubre recogida en origen, tránsito —con una o varias paradas— y entrega en destino. Cada envío tiene un cliente, un tipo de servicio, una ventana prometida y un conjunto de eventos que documentan lo que pasó.

## 2. Sistemas de origen

| Sistema | Qué contiene | Formato |
|---|---|---|
| TMS | Envíos, paradas, eventos, viajes, carriers, vehículos, conductores, ofertas | PostgreSQL |
| ERP | Facturas, tarifas, pagos, liquidaciones a carriers, costos de flota | CSV y Excel |
| CRM | Contactos, contratos, oportunidades | Excel |
| GPS | Telemetría de vehículos | JSON |
| APIs externas | Clima, geocodificación, tasa de cambio | REST |

## 3. El problema

1. **Reportes manuales.** Cada semana alguien consolida archivos y arma el reporte gerencial a mano.
2. **Definiciones divergentes.** OTIF, margen y costo por envío se calculan distinto en cada área.
3. **Datos sucios.** Duplicados, campos vacíos, fechas imposibles y formatos inconsistentes.
4. **Sin trazabilidad.** No se puede explicar de dónde sale un número.
5. **Sin control de acceso.** La información sensible circula por correo.
6. **Integraciones frágiles.** Las APIs se consumen sin reintentos ni validación de esquema.

## 4. Preguntas que debe responder la plataforma

**Operación**

- ¿Cuál es el cumplimiento OTIF por cliente, carrier y ruta?
- ¿Dónde se concentran los retrasos y por qué?
- ¿Cuánto tarda realmente un envío frente a lo prometido?

**Costo y rentabilidad**

- ¿Cuánto se factura, cuánto cuesta la flota propia y cuánto se paga a carriers?
- ¿Cuál es el margen por cliente y por ruta?
- ¿Qué proporción de los kilómetros se recorre en vacío?
- ¿Cómo rinde cada transportador?

**Datos y gobierno**

- ¿Qué tan completos, únicos, válidos y frescos son los datos?
- ¿Quién es el dueño de cada dominio?
- ¿Qué información es sensible y quién puede verla?
- ¿De dónde viene cada indicador?

## 5. Alcance del proyecto

**Incluye**

- Ingesta batch, por API y streaming de las fuentes descritas.
- Warehouse dimensional con granos declarados y dimensiones versionadas.
- Indicadores operativos, financieros y de calidad.
- Dashboards con seguridad a nivel de fila.
- Catálogo, linaje, clasificación, retención y matriz de accesos.
- Despliegue con costo cero en infraestructura gratuita.

**No incluye**

- Datos reales de personas o empresas.
- Integración con sistemas productivos.
- Aplicación móvil o portal de clientes.
- Facturación electrónica ni cumplimiento tributario.

## 6. Restricciones

- Solo datos sintéticos.
- Infraestructura gratuita y de recursos limitados.
- Todo el entorno debe reproducirse con contenedores.
- Ninguna cifra se publica sin medición.

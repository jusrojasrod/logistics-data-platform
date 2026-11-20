"""Conector HTTP de las fuentes de API.

El cliente de este paquete recorre las páginas del mock con autenticación,
reintentos acotados, respeto de ``Retry-After``, límite de tasa y validación
del sobre de paginación. El contenido de cada payload se conserva sin
transformar para que lo aterrice el módulo de destino.
"""

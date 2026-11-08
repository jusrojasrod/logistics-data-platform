"""Configuración de Superset. Los secretos vienen del entorno, nunca del repositorio."""

import os

SQLALCHEMY_DATABASE_URI = os.environ["SUPERSET_DB_URI"]
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# Caché y cola
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_CELERY_DB = "0"
REDIS_RESULTS_DB = "1"
REDIS_CACHE_DB = "2"

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": REDIS_HOST,
    "CACHE_REDIS_PORT": REDIS_PORT,
    "CACHE_REDIS_DB": REDIS_CACHE_DB,
}

DATA_CACHE_CONFIG = {**CACHE_CONFIG, "CACHE_DEFAULT_TIMEOUT": 3600}

CELERY_CONFIG = {
    "broker_url": f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_CELERY_DB}",
    "result_backend": f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_RESULTS_DB}",
    "worker_prefetch_multiplier": 1,
}

RESULTS_BACKEND = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_REDIS_HOST": REDIS_HOST,
    "CACHE_REDIS_PORT": REDIS_PORT,
    "CACHE_REDIS_DB": REDIS_RESULTS_DB,
}

# Límites de consulta
ROW_LIMIT = 5000
SQL_MAX_ROW = 100000
DEFAULT_SQLLAB_LIMIT = 1000
SUPERSET_WEBSERVER_TIMEOUT = 120

FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "DASHBOARD_RBAC": True,
    "HORIZONTAL_FILTER_BAR": True,
    "ESTIMATE_QUERY_COST": False,
}

# Seguridad
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
WTF_CSRF_ENABLED = True
WTF_CSRF_TIME_LIMIT = None

# Correo (opcional, para alertas e informes)
if os.environ.get("SMTP_HOST"):
    SMTP_HOST = os.environ["SMTP_HOST"]
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_STARTTLS = True
    SMTP_SSL = False
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_MAIL_FROM = os.environ.get("SMTP_USER", "no-reply@example.invalid")

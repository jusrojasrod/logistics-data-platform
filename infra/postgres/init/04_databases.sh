#!/bin/bash
# Crea las bases de metadatos de Airflow y Superset. Se ejecuta después de 02_roles.sql.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE airflow  OWNER airflow;
    CREATE DATABASE superset OWNER superset;
EOSQL

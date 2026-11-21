#!/usr/bin/env bash
# Aplica el init de MongoDB a un volumen ya creado: el entrypoint de la imagen
# solo ejecuta /docker-entrypoint-initdb.d cuando el volumen está vacío.
# La contraseña de aplicación la resuelve docker compose desde el entorno o
# desde .env; este script nunca la imprime ni la escribe en un archivo.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

INIT_FILE="infra/mongo/init/01_app_user.js"

if [[ ! -f "$INIT_FILE" ]]; then
    echo "No se encuentra $INIT_FILE" >&2
    exit 1
fi

if [[ -z "$(docker compose ps -q mongo)" ]]; then
    echo "El servicio mongo no está en marcha; ejecuta make up y reintenta." >&2
    exit 1
fi

# La contraseña se resuelve desde la config efectiva del compose y se inyecta
# en exec: un contenedor creado antes del cambio de compose no la tiene en su
# entorno. El JS se envía por stdin por el mismo motivo (el montaje initdb.d
# puede no existir todavía en un contenedor en marcha).
APP_PASSWORD="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["mongo"]["environment"]["MONGO_APP_PASSWORD"])')"

docker compose exec -T -e MONGO_APP_PASSWORD="$APP_PASSWORD" mongo sh -lc '
    : "${MONGO_APP_PASSWORD:?MONGO_APP_PASSWORD no definido en el contenedor de mongo}"
    cat > /tmp/nexora_01_app_user.js
    mongosh --quiet \
        -u "$MONGO_INITDB_ROOT_USERNAME" \
        -p "$MONGO_INITDB_ROOT_PASSWORD" \
        --authenticationDatabase admin \
        --file /tmp/nexora_01_app_user.js
    status=$?
    rm -f /tmp/nexora_01_app_user.js
    exit "$status"
' < "$INIT_FILE"

echo "Usuario de aplicación y colecciones de la vía API provisionados en MongoDB."

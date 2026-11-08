#!/usr/bin/env bash
# Prepara el entorno local y levanta el perfil core.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "Creado .env desde .env.example. Revisa los secretos antes de continuar."
fi

docker compose up -d --wait
docker compose ps

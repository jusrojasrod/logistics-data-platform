#!/usr/bin/env bash
# Exporta dashboards y datasets de Superset para versionarlos.
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="dashboards/superset"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.bi.yml)

"${COMPOSE[@]}" exec -T superset superset export-dashboards -f /tmp/dashboards.zip
docker cp nexora-superset:/tmp/dashboards.zip "${OUT_DIR}/dashboards.zip"

echo "Exportado en ${OUT_DIR}/dashboards.zip"

#!/usr/bin/env bash
# Genera el dataset sintético del perfil indicado.
#
# Uso: scripts/generate_data.sh [demo|local|benchmark] [semilla]
set -euo pipefail

cd "$(dirname "$0")/.."

SCALE="${1:-demo}"
SEED="${2:-42}"

python -m nexora.generators.cli --scale "$SCALE" --seed "$SEED" --out data/

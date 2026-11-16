#!/usr/bin/env bash
# Genera el dataset sintético con el CLI de generación.
#
# Uso: scripts/generate_data.sh [--scale demo|local|benchmark] [--seed N] [--months N] [--out DIR]
#
# Los argumentos se pasan sin cambios a `python -m nexora.generators.cli`.
# El perfil benchmark solo se genera a archivos, para medir la generación.
# Intérprete: el del entorno virtual activo; si no hay, `.venv` del repo;
# si tampoco existe, `python3` del PATH.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    PYTHON="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
else
    PYTHON="python3"
fi

exec "$PYTHON" -m nexora.generators.cli "$@"

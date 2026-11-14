#!/usr/bin/env bash
# Genera el perfil indicado en data/<perfil> y lo carga en el esquema raw.
#
# Uso: scripts/seed_db.sh demo|local
#
# La carga es idempotente por hash SHA-256 de archivo: con la misma semilla
# los CSV son idénticos y una segunda ejecución no inserta filas nuevas.
# raw es append-only; para partir de una base vacía se usa `make reset`.
# Conexión: NEXORA_PG_DSN si está definida; si no, el valor por defecto del loader.
# Intérprete: el del entorno virtual activo; si no hay, `.venv` del repo;
# si tampoco existe, `python3` del PATH.
set -euo pipefail

usage() {
    echo "Uso: $0 demo|local" >&2
}

if [[ $# -ne 1 ]]; then
    usage
    exit 2
fi

SCALE="$1"
case "$SCALE" in
    demo | local) ;;
    *)
        echo "Perfil no válido: '$SCALE'" >&2
        usage
        exit 2
        ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    PYTHON="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
else
    PYTHON="python3"
fi

OUT_DIR="${REPO_ROOT}/data/${SCALE}"

echo "Generando perfil '${SCALE}' en data/${SCALE}"
"$PYTHON" -m nexora.generators.cli --scale "$SCALE" --out "$OUT_DIR"

echo "Cargando data/${SCALE} en raw"
"$PYTHON" -m nexora.ingestion.batch.loaders --input "$OUT_DIR"

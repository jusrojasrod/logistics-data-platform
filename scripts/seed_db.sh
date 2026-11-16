#!/usr/bin/env bash
# Genera el perfil indicado en data/<perfil> y publica todas las entidades TMS
# de la carpeta en el esquema tms: dimensiones, envíos, flota, viajes, tramos,
# envíos por tramo, paradas, eventos, combustible, ofertas e incidencias. No
# carga raw: raw se alimenta con el conector batch de ingesta.
#
# Uso: scripts/seed_db.sh demo|local
#
# El perfil benchmark no se acepta: solo se genera a archivos y no se publica.
# La publicación solo inserta filas nuevas, cada tabla por su clave natural, así
# una segunda ejecución con la misma semilla no inserta nada. Para partir de una
# base vacía se usa `make reset`.
# Conexión: NEXORA_TMS_DSN y TMS_WRITER_PASSWORD, del entorno o de `.env` en la
# raíz del repo (el script se ejecuta desde ahí).
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
cd "$REPO_ROOT"

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

echo "Publicando data/${SCALE} en tms"
"$PYTHON" -m nexora.generators.tms_publisher --input "$OUT_DIR"

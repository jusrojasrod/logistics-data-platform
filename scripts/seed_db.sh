#!/usr/bin/env bash
# Genera el perfil indicado en data/<perfil>, publica todas las entidades TMS
# de la carpeta en el esquema tms (dimensiones, envíos, flota, viajes, tramos,
# envíos por tramo, paradas, eventos, combustible, ofertas e incidencias) y
# carga en raw los archivos ERP y CRM con el conector batch de archivos: primero
# los de la carpeta y después los tardíos de su subcarpeta late/.
#
# Uso: scripts/seed_db.sh demo|local
#
# El perfil benchmark no se acepta: solo se genera a archivos y no se publica.
# La publicación solo inserta filas nuevas, cada tabla por su clave natural, y
# la carga de archivos omite los que ya tienen su hash en meta.ingested_files,
# así una segunda ejecución con la misma semilla no inserta nada. Las tablas
# TMS de raw no se tocan aquí: se alimentan con la extracción por watermark.
# Para partir de una base vacía se usa `make reset`.
# Conexión de la publicación en tms: NEXORA_TMS_DSN y TMS_WRITER_PASSWORD.
# Conexión de la carga en raw: NEXORA_PG_DSN y ETL_WRITER_PASSWORD (etl_writer).
# Ambas del entorno o de `.env` en la raíz del repo (el script se ejecuta desde
# ahí).
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

echo "Cargando los archivos ERP y CRM de data/${SCALE} en raw"
"$PYTHON" -m nexora.ingestion.batch.loaders --source files --input "$OUT_DIR"

echo "Cargando los archivos ERP y CRM tardíos de data/${SCALE}/late en raw"
"$PYTHON" -m nexora.ingestion.batch.loaders --source files --input "$OUT_DIR/late"

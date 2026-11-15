#!/usr/bin/env bash
# Importa en Superset los dashboards versionados en YAML.
#
# Empaqueta dashboards/superset/{databases,datasets,charts,dashboards} en un
# bundle de exportación v1 (metadata.yaml en la raíz), lo copia al contenedor,
# lo importa con `superset import-dashboards` y fija la URI de la base `nexora`,
# porque el YAML versionado lleva la contraseña enmascarada.
#
# Al terminar, comprueba en Superset por uuid que existen la base, el dataset,
# el chart y el dashboard de los YAML e imprime su tipo y nombre.
#
# Uso:
#   bash scripts/import_superset.sh                    # empaqueta e importa
#   bash scripts/import_superset.sh --bundle-only F.zip  # solo genera el zip
#
# Variables opcionales: SUPERSET_CONTAINER, SUPERSET_ADMIN_USER,
# BI_READER_PASSWORD y PYTHON.
set -euo pipefail

cd "$(dirname "$0")/.."

SRC_DIR="dashboards/superset"
FOLDERS=(databases datasets charts dashboards)
CONTAINER="${SUPERSET_CONTAINER:-nexora-superset}"
REMOTE_ZIP="/tmp/nexora_dashboards_import.zip"
PYTHON="${PYTHON:-python3}"
SUPERSET_USER="${SUPERSET_ADMIN_USER:-admin}"
DATABASE_NAME="nexora"

BUNDLE_ONLY=""
if [[ "${1:-}" == "--bundle-only" ]]; then
    BUNDLE_ONLY="${2:?Falta la ruta del zip: --bundle-only <archivo.zip>}"
fi

WORK_DIR="$(mktemp -d)"
REMOTE_COPIED=""
# El zip lleva la contraseña: se borra en local y en el contenedor aunque falle
# cualquier paso posterior.
cleanup() {
    rm -rf "${WORK_DIR}"
    if [[ -n "${REMOTE_COPIED}" ]]; then
        docker exec "${CONTAINER}" rm -f "${REMOTE_ZIP}" > /dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# Directorio raíz del bundle: Superset descarta el primer nivel de cada ruta.
BUNDLE_ROOT="${WORK_DIR}/nexora_dashboards"
mkdir -p "${BUNDLE_ROOT}"

for folder in "${FOLDERS[@]}"; do
    if ! compgen -G "${SRC_DIR}/${folder}/*.yaml" > /dev/null; then
        echo "Error: no hay YAML en ${SRC_DIR}/${folder}" >&2
        exit 1
    fi
    mkdir -p "${BUNDLE_ROOT}/${folder}"
    cp "${SRC_DIR}/${folder}"/*.yaml "${BUNDLE_ROOT}/${folder}/"
done

# Con la URI enmascarada, Superset exige `password` al crear la base; se añade
# solo a la copia temporal del bundle, nunca al YAML versionado.
BI_READER_PASSWORD="${BI_READER_PASSWORD:-bi_reader_dev}" "${PYTHON}" -c \
    'import json, os, sys; open(sys.argv[1], "a").write("password: " + json.dumps(os.environ["BI_READER_PASSWORD"]) + "\n")' \
    "${BUNDLE_ROOT}/databases/nexora_postgres.yaml"

cat > "${BUNDLE_ROOT}/metadata.yaml" <<EOF
version: 1.0.0
type: Dashboard
timestamp: '$(date -u +%Y-%m-%dT%H:%M:%S+00:00)'
EOF

BUNDLE_ZIP="${WORK_DIR}/nexora_dashboards.zip"
"${PYTHON}" -m zipfile -c "${BUNDLE_ZIP}" "${BUNDLE_ROOT}"

if [[ -n "${BUNDLE_ONLY}" ]]; then
    cp "${BUNDLE_ZIP}" "${BUNDLE_ONLY}"
    echo "Bundle generado en ${BUNDLE_ONLY}"
    echo "Aviso: ${BUNDLE_ONLY} contiene la contraseña de la conexión ${DATABASE_NAME}; bórralo tras usarlo y no lo versiones." >&2
    exit 0
fi

REMOTE_COPIED="1"
docker cp "${BUNDLE_ZIP}" "${CONTAINER}:${REMOTE_ZIP}"
docker exec "${CONTAINER}" superset import-dashboards \
    --path "${REMOTE_ZIP}" --username "${SUPERSET_USER}"
docker exec "${CONTAINER}" rm -f "${REMOTE_ZIP}"
REMOTE_COPIED=""

# La URI viaja por variable de entorno para no aparecer en la salida.
BI_READER_URI="postgresql+psycopg2://bi_reader:${BI_READER_PASSWORD:-bi_reader_dev}@postgres:5432/nexora"
export BI_READER_URI
docker exec -e BI_READER_URI "${CONTAINER}" sh -c \
    'superset set-database-uri --database_name "$0" --uri "$BI_READER_URI" --skip_create' \
    "${DATABASE_NAME}"

echo "Importado desde ${SRC_DIR}; URI de la base ${DATABASE_NAME} fijada"

# Comprueba por uuid los cuatro objetos de los YAML versionados.
yaml_uuid() {
    local value
    value="$(sed -n 's/^uuid: *//p' "$1")"
    if [[ -z "${value}" ]]; then
        echo "Error: sin uuid en $1" >&2
        exit 1
    fi
    echo "${value}"
}
DATABASE_UUID="$(yaml_uuid "${SRC_DIR}/databases/nexora_postgres.yaml")"
DATASET_UUID="$(yaml_uuid "${SRC_DIR}/datasets/mart_operations.yaml")"
CHART_UUID="$(yaml_uuid "${SRC_DIR}/charts/otif_monthly.yaml")"
DASHBOARD_UUID="$(yaml_uuid "${SRC_DIR}/dashboards/operations_overview.yaml")"

docker exec -i "${CONTAINER}" /app/.venv/bin/python - \
    "${DATABASE_UUID}" "${DATASET_UUID}" "${CHART_UUID}" "${DASHBOARD_UUID}" <<'PY'
import sys
import uuid

from superset.app import create_app

app = create_app()
with app.app_context():
    from superset.connectors.sqla.models import SqlaTable
    from superset.extensions import db
    from superset.models.core import Database
    from superset.models.dashboard import Dashboard
    from superset.models.slice import Slice

    checks = [
        ("base", Database, lambda o: o.database_name),
        ("dataset", SqlaTable, lambda o: f"{o.schema}.{o.table_name}"),
        ("chart", Slice, lambda o: o.slice_name),
        ("dashboard", Dashboard, lambda o: o.dashboard_title),
    ]
    missing = 0
    for (kind, model, name), value in zip(checks, sys.argv[1:]):
        obj = db.session.query(model).filter_by(uuid=uuid.UUID(value)).one_or_none()
        if obj is None:
            print(f"Error: no existe {kind} con uuid {value}", file=sys.stderr)
            missing += 1
        else:
            print(f"Importado {kind}: {name(obj)}")
    sys.exit(1 if missing else 0)
PY

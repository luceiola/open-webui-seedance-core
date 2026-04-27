#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/config/ark.env}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8801}"
DATA_DIR="${DATA_DIR:-}"

if ! command -v open-webui >/dev/null 2>&1; then
  echo "[ERROR] open-webui command not found in current env."
  echo "Activate conda env and run: pip install -e ."
  exit 1
fi

bash "${ROOT_DIR}/scripts/seedance/preflight.sh" --auto-fix

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  source "${ENV_FILE}"
  set +a
  echo "[INFO] loaded env file: ${ENV_FILE}"
else
  echo "[WARN] env file not found: ${ENV_FILE} (continue without it)"
fi

if [[ -n "${DATA_DIR}" ]]; then
  mkdir -p "${DATA_DIR}"
  export DATA_DIR
  echo "[INFO] DATA_DIR=${DATA_DIR}"
fi

if [[ "${MATERIAL_PACK_TOS_ENABLED:-false}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  if ! python -c "import tos" >/dev/null 2>&1; then
    echo "[ERROR] MATERIAL_PACK_TOS_ENABLED=true but python package 'tos' is missing."
    echo "Run: pip install tos"
    exit 2
  fi
fi

cd "${ROOT_DIR}"
exec open-webui serve --host "${HOST}" --port "${PORT}"

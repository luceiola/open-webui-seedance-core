#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8802}"
export ENV_FILE="${ENV_FILE:-${ROOT_DIR}/config/ark.dev.env}"
export DATA_DIR="${DATA_DIR:-${ROOT_DIR}/.data-dev}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[ERROR] missing dev env file: ${ENV_FILE}"
  echo "Run: cp ${ROOT_DIR}/config/ark.dev.env.example ${ROOT_DIR}/config/ark.dev.env"
  exit 1
fi

echo "[INFO] instance=dev host=${HOST} port=${PORT}"
echo "[INFO] env=${ENV_FILE}"
echo "[INFO] data=${DATA_DIR}"

exec bash "${ROOT_DIR}/scripts/seedance/run_openwebui.sh"

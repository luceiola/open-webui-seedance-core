#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8801}"
export ENV_FILE="${ENV_FILE:-${ROOT_DIR}/config/ark.env}"
export DATA_DIR="${DATA_DIR:-${ROOT_DIR}/.data-prod}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[ERROR] missing prod env file: ${ENV_FILE}"
  echo "Create it first (example keys: ARK/TOS in config/ark.env)."
  exit 1
fi

echo "[INFO] instance=prod host=${HOST} port=${PORT}"
echo "[INFO] env=${ENV_FILE}"
echo "[INFO] data=${DATA_DIR}"

exec bash "${ROOT_DIR}/scripts/seedance/run_openwebui.sh"

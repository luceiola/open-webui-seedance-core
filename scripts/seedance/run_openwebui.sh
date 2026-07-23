#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/config/ark.env}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8801}"
DATA_DIR="${DATA_DIR:-}"

if ! command -v python >/dev/null 2>&1; then
  echo "[ERROR] python not found in current env."
  echo "Activate conda env that contains runtime dependencies."
  exit 1
fi
if ! command -v open-webui >/dev/null 2>&1; then
  echo "[ERROR] open-webui command not found in current env."
  echo "Activate conda env and install project dependencies."
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

if [[ -n "${TASK_ARTIFACTS_ROOT:-}" ]]; then
  if [[ ! -d "${TASK_ARTIFACTS_ROOT}" ]]; then
    echo "[ERROR] TASK_ARTIFACTS_ROOT does not exist: ${TASK_ARTIFACTS_ROOT}"
    exit 2
  fi
  if [[ ! -r "${TASK_ARTIFACTS_ROOT}" || ! -w "${TASK_ARTIFACTS_ROOT}" || ! -x "${TASK_ARTIFACTS_ROOT}" ]]; then
    echo "[ERROR] TASK_ARTIFACTS_ROOT is not readable and writable: ${TASK_ARTIFACTS_ROOT}"
    exit 2
  fi
  TASK_ARTIFACTS_PROBE="${TASK_ARTIFACTS_ROOT}/.open-webui-artifact-probe-$$"
  if ! (umask 077 && : > "${TASK_ARTIFACTS_PROBE}" && rm -f "${TASK_ARTIFACTS_PROBE}"); then
    rm -f "${TASK_ARTIFACTS_PROBE}" 2>/dev/null || true
    echo "[ERROR] TASK_ARTIFACTS_ROOT write probe failed: ${TASK_ARTIFACTS_ROOT}"
    exit 2
  fi
  echo "[INFO] task artifacts=${TASK_ARTIFACTS_ROOT}"
fi

if [[ "${MATERIAL_PACK_TOS_ENABLED:-false}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  if ! python -c "import tos" >/dev/null 2>&1; then
    echo "[ERROR] MATERIAL_PACK_TOS_ENABLED=true but python package 'tos' is missing."
    echo "Run: pip install tos"
    exit 2
  fi
fi

cd "${ROOT_DIR}"

# Always run backend from current repo to keep dev/prod isolated even when a shared
# environment has open_webui editable-installed from another path.
export PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}"
echo "[INFO] PYTHONPATH=${ROOT_DIR}/backend${PYTHONPATH:+:...}"
exec open-webui serve --host "${HOST}" --port "${PORT}"

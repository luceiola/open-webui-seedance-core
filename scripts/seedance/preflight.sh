#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTO_FIX=0

resolve_python_from_entrypoint() {
  local entrypoint="$1"
  local shebang
  local first_token
  local second_token

  shebang="$(head -n 1 "${entrypoint}" 2>/dev/null || true)"
  [[ "${shebang}" == '#!'* ]] || return 1

  read -r first_token second_token _ <<<"${shebang#\#!}"
  [[ -n "${first_token:-}" ]] || return 1

  if [[ "${first_token}" == */env ]]; then
    local cmd="${second_token:-python}"
    if command -v "${cmd}" >/dev/null 2>&1; then
      command -v "${cmd}"
      return 0
    fi
    return 1
  fi

  if [[ -x "${first_token}" ]]; then
    echo "${first_token}"
    return 0
  fi

  return 1
}

usage() {
  cat <<'EOF'
Usage:
  bash scripts/seedance/preflight.sh [--auto-fix]

Options:
  --auto-fix   Try to auto-install missing `greenlet` in current python env.
  -h, --help   Show help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto-fix)
      AUTO_FIX=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

cd "${ROOT_DIR}"

if [[ ! -f "backend/open_webui/main.py" ]]; then
  echo "[ERROR] Not in open-webui-seedance-core root: ${ROOT_DIR}"
  exit 2
fi

if ! command -v python >/dev/null 2>&1; then
  echo "[ERROR] python not found in PATH. Activate your conda env first."
  exit 3
fi

PYTHON_BIN="$(command -v python)"
if command -v open-webui >/dev/null 2>&1; then
  OPEN_WEBUI_BIN="$(command -v open-webui)"
  if OPEN_WEBUI_PYTHON="$(resolve_python_from_entrypoint "${OPEN_WEBUI_BIN}")"; then
    if [[ "${OPEN_WEBUI_PYTHON}" != "${PYTHON_BIN}" ]]; then
      echo "[WARN] python in PATH (${PYTHON_BIN}) differs from open-webui runtime (${OPEN_WEBUI_PYTHON})."
      echo "[WARN] using open-webui runtime for preflight checks."
    fi
    PYTHON_BIN="${OPEN_WEBUI_PYTHON}"
  fi
fi

if ! IMPORT_ERR="$(
  PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON_BIN}" -c "import open_webui" 2>&1
)"; then
  echo "[ERROR] open_webui is not importable with local backend path."
  echo "[ERROR] python=${PYTHON_BIN}"
  echo "Check python runtime dependencies in current env."
  if [[ -n "${IMPORT_ERR}" ]]; then
    echo "${IMPORT_ERR}"
  fi
  exit 4
fi

if ! "${PYTHON_BIN}" -c "import greenlet" >/dev/null 2>&1; then
  if [[ "${AUTO_FIX}" == "1" ]]; then
    echo "[WARN] greenlet missing, installing with ${PYTHON_BIN} -m pip..."
    "${PYTHON_BIN}" -m pip install greenlet
  else
    echo "[ERROR] missing dependency: greenlet"
    echo "Run: ${PYTHON_BIN} -m pip install greenlet"
    exit 5
  fi
fi

FRONTEND_LINK="${ROOT_DIR}/backend/open_webui/frontend"
FRONTEND_BUILD="${ROOT_DIR}/build"
if [[ ! -e "${FRONTEND_LINK}" ]]; then
  if [[ -d "${FRONTEND_BUILD}" ]]; then
    ln -s ../../build "${FRONTEND_LINK}"
    echo "[OK] created frontend symlink: backend/open_webui/frontend -> ../../build"
  else
    echo "[ERROR] frontend build not found: ${FRONTEND_BUILD}"
    echo "Run: npm install --force && npm run pyodide:fetch && npm run build"
    exit 6
  fi
fi

echo "[OK] preflight passed"

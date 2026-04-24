#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH_SOURCE_DIR="${ROOT_DIR}/.venv/lib/python3.11/site-packages/open_webui/routers"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/sync_openwebui_patches.sh [--conda-env ENV] [--python PYTHON]

Options:
  --conda-env ENV   Use `conda run -n ENV python` to find target open_webui path.
  --python PYTHON   Use a specific python binary to find target open_webui path.
  -h, --help        Show this help.

Notes:
  - Patch source is this repo path:
      .venv/lib/python3.11/site-packages/open_webui/routers/material_packages.py
  - This script copies patched router files into the target runtime environment.
EOF
}

CONDA_ENV=""
PYTHON_BIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --conda-env)
      CONDA_ENV="${2:-}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -n "${CONDA_ENV}" && -n "${PYTHON_BIN}" ]]; then
  echo "[ERROR] Use either --conda-env or --python, not both." >&2
  exit 1
fi

if [[ ! -f "${PATCH_SOURCE_DIR}/material_packages.py" ]]; then
  echo "[ERROR] Missing patch source file:" >&2
  echo "  ${PATCH_SOURCE_DIR}/material_packages.py" >&2
  exit 1
fi

resolve_target_router_dir() {
  local py_cmd="$1"
  "${py_cmd}" - <<'PY'
import pathlib
import open_webui
print(pathlib.Path(open_webui.__file__).resolve().parent / "routers")
PY
}

if [[ -n "${CONDA_ENV}" ]]; then
  if ! command -v conda >/dev/null 2>&1; then
    echo "[ERROR] conda not found in PATH." >&2
    exit 1
  fi
  TARGET_ROUTER_DIR="$(conda run --no-capture-output -n "${CONDA_ENV}" python - <<'PY'
import pathlib
import open_webui
print(pathlib.Path(open_webui.__file__).resolve().parent / "routers")
PY
)"
else
  if [[ -z "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python"
  fi
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "[ERROR] Python binary not found: ${PYTHON_BIN}" >&2
    exit 1
  fi
  TARGET_ROUTER_DIR="$(resolve_target_router_dir "${PYTHON_BIN}")"
fi

TARGET_ROUTER_DIR="$(echo "${TARGET_ROUTER_DIR}" | tail -n 1 | tr -d '\r')"

if [[ -z "${TARGET_ROUTER_DIR}" || ! -d "${TARGET_ROUTER_DIR}" ]]; then
  echo "[ERROR] Invalid target router dir: ${TARGET_ROUTER_DIR}" >&2
  exit 1
fi

timestamp="$(date +%Y%m%d%H%M%S)"

sync_one() {
  local name="$1"
  local src="${PATCH_SOURCE_DIR}/${name}"
  local dst="${TARGET_ROUTER_DIR}/${name}"
  if [[ ! -f "${src}" ]]; then
    echo "[WARN] Skip ${name}: source not found (${src})"
    return 0
  fi
  if [[ "${src}" == "${dst}" ]]; then
    echo "[INFO] Skip ${name}: source and target are the same file"
    return 0
  fi
  if [[ -f "${dst}" ]]; then
    cp "${dst}" "${dst}.bak.${timestamp}"
  fi
  install -m 0644 "${src}" "${dst}"
  echo "[OK] synced ${name}"
}

echo "[INFO] root: ${ROOT_DIR}"
echo "[INFO] source: ${PATCH_SOURCE_DIR}"
echo "[INFO] target: ${TARGET_ROUTER_DIR}"

sync_one "material_packages.py"
sync_one "files.py"

echo "[DONE] Patch sync complete."

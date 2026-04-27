#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTO_FIX=0

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

if ! python -c "import open_webui" >/dev/null 2>&1; then
  echo "[ERROR] open_webui is not importable in current env."
  echo "Run: pip install -e ."
  exit 4
fi

if ! python -c "import greenlet" >/dev/null 2>&1; then
  if [[ "${AUTO_FIX}" == "1" ]]; then
    echo "[WARN] greenlet missing, installing..."
    pip install greenlet
  else
    echo "[ERROR] missing dependency: greenlet"
    echo "Run: pip install greenlet"
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

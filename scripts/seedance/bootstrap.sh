#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v python3.11 >/dev/null 2>&1; then
  echo "[ERROR] python3.11 not found. Please install Python 3.11 first."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[INFO] creating virtualenv (.venv) with python3.11"
  python3.11 -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate

echo "[INFO] python: $(python --version)"
echo "[INFO] pip: $(pip --version)"

echo "[INFO] installing/upgrading open-webui"
pip install -U open-webui

echo "[INFO] done"
echo "[NEXT] source .venv/bin/activate"
echo "[NEXT] open-webui serve --host 127.0.0.1 --port 8080"

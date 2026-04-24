#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-}"

if ! command -v jq >/dev/null 2>&1; then
  echo "[ERROR] jq is required"
  exit 1
fi

if [[ -z "${BASE_URL}" ]]; then
  for candidate in "http://127.0.0.1:8801" "http://127.0.0.1:8080"; do
    if curl -fsS --max-time 3 "${candidate}/openapi.json" >/dev/null 2>&1; then
      BASE_URL="${candidate}"
      break
    fi
  done
fi

if [[ -z "${BASE_URL}" ]]; then
  echo "[FAIL] OpenAPI endpoint is unreachable on 127.0.0.1:8801 and 127.0.0.1:8080"
  echo "Hint: pass BASE_URL explicitly, e.g. bash scripts/check_material_routes.sh http://127.0.0.1:8801"
  exit 2
fi

ROUTES="$(curl -fsS "${BASE_URL}/openapi.json" | jq -r '.paths | keys[]' | grep '/api/v1/material-packages' || true)"

if [[ -z "${ROUTES}" ]]; then
  echo "[FAIL] material-packages routes not found in ${BASE_URL}/openapi.json"
  echo "Hint: restart open-webui from the patched environment."
  exit 3
fi

echo "[OK] material-packages routes detected:"
echo "${ROUTES}"

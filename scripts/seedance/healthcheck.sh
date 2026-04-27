#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-}"

if ! command -v curl >/dev/null 2>&1; then
  echo "[ERROR] curl is required"
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "[ERROR] jq is required"
  exit 1
fi

if [[ -z "${BASE_URL}" ]]; then
  for candidate in "http://127.0.0.1:8801" "http://127.0.0.1:8080"; do
    if curl --noproxy '*' -fsS --max-time 3 "${candidate}/api/version" >/dev/null 2>&1; then
      BASE_URL="${candidate}"
      break
    fi
  done
fi

if [[ -z "${BASE_URL}" ]]; then
  echo "[FAIL] service is unreachable on 127.0.0.1:8801 and 127.0.0.1:8080"
  exit 2
fi

root_code="$(curl --noproxy '*' -sS -o /dev/null -w "%{http_code}" "${BASE_URL}/")"
version_code="$(curl --noproxy '*' -sS -o /dev/null -w "%{http_code}" "${BASE_URL}/api/version")"
openapi_code="$(curl --noproxy '*' -sS -o /dev/null -w "%{http_code}" "${BASE_URL}/openapi.json")"

if [[ "${root_code}" != "200" ]]; then
  echo "[FAIL] ${BASE_URL}/ returns HTTP ${root_code} (expect 200)"
  exit 3
fi
if [[ "${version_code}" != "200" ]]; then
  echo "[FAIL] ${BASE_URL}/api/version returns HTTP ${version_code} (expect 200)"
  exit 3
fi
if [[ "${openapi_code}" != "200" ]]; then
  echo "[FAIL] ${BASE_URL}/openapi.json returns HTTP ${openapi_code} (expect 200)"
  exit 3
fi

routes="$(curl --noproxy '*' -fsS "${BASE_URL}/openapi.json" | jq -r '.paths | keys[]' | grep '/api/v1/material-packages' || true)"
if [[ -z "${routes}" ]]; then
  echo "[FAIL] material-packages routes not found in ${BASE_URL}/openapi.json"
  exit 4
fi

echo "[OK] healthcheck passed at ${BASE_URL}"
echo "${routes}"

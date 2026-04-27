#!/usr/bin/env bash
set -euo pipefail

check_one() {
  local name="$1"
  local port="$2"
  local base="http://127.0.0.1:${port}"

  local code
  code="$(curl --noproxy '*' -sS -o /dev/null -w "%{http_code}" "${base}/api/version" || true)"
  if [[ "${code}" == "200" ]]; then
    echo "[OK] ${name} (${port}) up"
  else
    echo "[--] ${name} (${port}) down (code=${code:-NA})"
  fi
}

check_one "prod" "${PROD_PORT:-8801}"
check_one "dev" "${DEV_PORT:-8802}"

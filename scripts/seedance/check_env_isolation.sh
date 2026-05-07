#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROD_ENV="${1:-${ROOT_DIR}/config/ark.env}"
DEV_ENV="${2:-${ROOT_DIR}/config/ark.dev.env}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/seedance/check_env_isolation.sh [prod_env] [dev_env]

Defaults:
  prod_env=config/ark.env
  dev_env=config/ark.dev.env
EOF
}

if [[ "${PROD_ENV}" == "-h" || "${PROD_ENV}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "${PROD_ENV}" ]]; then
  echo "[FAIL] prod env file not found: ${PROD_ENV}"
  exit 2
fi

if [[ ! -f "${DEV_ENV}" ]]; then
  echo "[FAIL] dev env file not found: ${DEV_ENV}"
  exit 2
fi

get_env_value() {
  local file="$1"
  local key="$2"
  awk -F= -v k="${key}" '
    BEGIN { found = 0 }
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
      key = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (key == k) {
        val = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
        gsub(/^"|"$/, "", val)
        gsub(/^'\''|'\''$/, "", val)
        print val
        found = 1
        exit
      }
    }
    END { if (!found) print "" }
  ' "${file}"
}

prod_tos_prefix="$(get_env_value "${PROD_ENV}" "TOS_PREFIX")"
dev_tos_prefix="$(get_env_value "${DEV_ENV}" "TOS_PREFIX")"
prod_media_prefix="$(get_env_value "${PROD_ENV}" "MEDIA_ASSET_TOS_PREFIX")"
dev_media_prefix="$(get_env_value "${DEV_ENV}" "MEDIA_ASSET_TOS_PREFIX")"

echo "[INFO] prod env: ${PROD_ENV}"
echo "[INFO] dev env:  ${DEV_ENV}"
echo "[INFO] TOS_PREFIX(prod)=${prod_tos_prefix:-<empty>}"
echo "[INFO] TOS_PREFIX(dev)= ${dev_tos_prefix:-<empty>}"
echo "[INFO] MEDIA_ASSET_TOS_PREFIX(prod)=${prod_media_prefix:-<empty>}"
echo "[INFO] MEDIA_ASSET_TOS_PREFIX(dev)= ${dev_media_prefix:-<empty>}"

failed=0

if [[ -z "${prod_tos_prefix}" || -z "${dev_tos_prefix}" ]]; then
  echo "[FAIL] TOS_PREFIX must be set in both env files."
  failed=1
elif [[ "${prod_tos_prefix}" == "${dev_tos_prefix}" ]]; then
  echo "[FAIL] TOS_PREFIX is identical for prod/dev; isolate them before release."
  failed=1
else
  echo "[OK] TOS_PREFIX is isolated."
fi

if [[ -n "${prod_media_prefix}" || -n "${dev_media_prefix}" ]]; then
  if [[ -z "${prod_media_prefix}" || -z "${dev_media_prefix}" ]]; then
    echo "[FAIL] MEDIA_ASSET_TOS_PREFIX must be set in both files when either side uses it."
    failed=1
  elif [[ "${prod_media_prefix}" == "${dev_media_prefix}" ]]; then
    echo "[FAIL] MEDIA_ASSET_TOS_PREFIX is identical for prod/dev."
    failed=1
  else
    echo "[OK] MEDIA_ASSET_TOS_PREFIX is isolated."
  fi
else
  echo "[WARN] MEDIA_ASSET_TOS_PREFIX not set in both env files; media-assets may fallback to default prefix."
fi

if [[ "${failed}" -ne 0 ]]; then
  exit 3
fi

echo "[DONE] env isolation check passed"

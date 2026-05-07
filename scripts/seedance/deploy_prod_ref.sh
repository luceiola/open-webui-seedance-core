#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REF=""
REMOTE="origin"
SKIP_HEALTHCHECK=0
RESTART_SYSTEMD=""
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8801}"
BASE_URL=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/seedance/deploy_prod_ref.sh --ref <tag-or-commit> [options]

Required:
  --ref <ref>               Release tag or commit to deploy to prod.

Options:
  --remote <name>           Git remote for fetch (default: origin).
  --skip-healthcheck        Skip healthcheck after deployment.
  --restart-systemd <name>  Restart prod systemd service (e.g. openwebui-seedance-prod).
  --host <host>             Host for manual start hint (default: 0.0.0.0).
  --port <port>             Port for manual start hint (default: 8801).
  --base-url <url>          Base URL for healthcheck (default auto by port).
  -h, --help                Show help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      REF="${2:-}"
      shift 2
      ;;
    --remote)
      REMOTE="${2:-}"
      shift 2
      ;;
    --skip-healthcheck)
      SKIP_HEALTHCHECK=1
      shift
      ;;
    --restart-systemd)
      RESTART_SYSTEMD="${2:-}"
      shift 2
      ;;
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --base-url)
      BASE_URL="${2:-}"
      shift 2
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

if [[ -z "${REF}" ]]; then
  echo "[ERROR] --ref is required"
  usage
  exit 2
fi

cd "${ROOT_DIR}"
echo "[INFO] mode=prod repo=${ROOT_DIR}"

PREV_REF="$(git rev-parse --short HEAD)"
PREV_DESC="$(git describe --tags --always 2>/dev/null || true)"

echo "[STEP] git fetch --tags ${REMOTE}"
git fetch --tags "${REMOTE}"

echo "[STEP] checkout ref: ${REF}"
git checkout "${REF}"

NEW_REF="$(git rev-parse --short HEAD)"
NEW_DESC="$(git describe --tags --always 2>/dev/null || true)"
echo "[INFO] previous=${PREV_REF} (${PREV_DESC}) -> current=${NEW_REF} (${NEW_DESC})"

echo "[STEP] preflight"
bash "${ROOT_DIR}/scripts/seedance/preflight.sh" --auto-fix

if [[ -n "${RESTART_SYSTEMD}" ]]; then
  echo "[STEP] restart prod systemd: ${RESTART_SYSTEMD}"
  sudo systemctl restart "${RESTART_SYSTEMD}"
  sudo systemctl status "${RESTART_SYSTEMD}" -l --no-pager | sed -n '1,25p'
else
  echo "[STEP] no systemd restart requested"
  echo "[NEXT] manual prod start command:"
  echo "  HOST=${HOST} PORT=${PORT} ENV_FILE=${ROOT_DIR}/config/ark.env DATA_DIR=${ROOT_DIR}/.data-prod bash scripts/seedance/run_openwebui.sh"
fi

if [[ "${SKIP_HEALTHCHECK}" != "1" ]]; then
  echo "[STEP] healthcheck"
  if [[ -n "${BASE_URL}" ]]; then
    bash "${ROOT_DIR}/scripts/seedance/healthcheck.sh" "${BASE_URL}"
  else
    bash "${ROOT_DIR}/scripts/seedance/healthcheck.sh" "http://127.0.0.1:${PORT}"
  fi
else
  echo "[STEP] skip healthcheck"
fi

echo "[DONE] prod deploy finished"
echo "[HINT] rollback command:"
echo "  bash scripts/seedance/deploy_prod_ref.sh --ref ${PREV_REF} --restart-systemd ${RESTART_SYSTEMD:-<prod-service>}"

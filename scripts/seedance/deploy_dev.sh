#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRANCH="main"
REMOTE="origin"
SKIP_PULL=0
SKIP_HEALTHCHECK=0
RESTART_SYSTEMD=""
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8802}"
BASE_URL=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/seedance/deploy_dev.sh [options]

Options:
  --skip-pull               Skip git pull.
  --skip-healthcheck        Skip healthcheck after deployment.
  --branch <name>           Git branch for pull (default: main).
  --remote <name>           Git remote for pull (default: origin).
  --restart-systemd <name>  Restart dev systemd service (e.g. openwebui-seedance-dev).
  --host <host>             Host for manual start hint (default: 0.0.0.0).
  --port <port>             Port for manual start hint (default: 8802).
  --base-url <url>          Base URL for healthcheck (default auto by port).
  -h, --help                Show help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-pull)
      SKIP_PULL=1
      shift
      ;;
    --skip-healthcheck)
      SKIP_HEALTHCHECK=1
      shift
      ;;
    --branch)
      BRANCH="${2:-}"
      shift 2
      ;;
    --remote)
      REMOTE="${2:-}"
      shift 2
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

cd "${ROOT_DIR}"
echo "[INFO] mode=dev repo=${ROOT_DIR}"

if [[ "${SKIP_PULL}" != "1" ]]; then
  echo "[STEP] git pull --ff-only ${REMOTE} ${BRANCH}"
  git pull --ff-only "${REMOTE}" "${BRANCH}"
else
  echo "[STEP] skip git pull"
fi

echo "[STEP] preflight"
bash "${ROOT_DIR}/scripts/seedance/preflight.sh" --auto-fix

if [[ -n "${RESTART_SYSTEMD}" ]]; then
  echo "[STEP] restart dev systemd: ${RESTART_SYSTEMD}"
  sudo systemctl restart "${RESTART_SYSTEMD}"
  sudo systemctl status "${RESTART_SYSTEMD}" -l --no-pager | sed -n '1,25p'
else
  echo "[STEP] no systemd restart requested"
  echo "[NEXT] manual dev start command:"
  echo "  HOST=${HOST} PORT=${PORT} ENV_FILE=${ROOT_DIR}/config/ark.dev.env DATA_DIR=${ROOT_DIR}/.data-dev bash scripts/seedance/run_openwebui.sh"
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

echo "[DONE] dev deploy flow finished"

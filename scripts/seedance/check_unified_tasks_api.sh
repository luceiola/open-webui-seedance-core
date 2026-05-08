#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8801}"
OPENAPI_URL="${BASE_URL%/}/openapi.json"
TMP_JSON="$(mktemp)"

cleanup() {
  rm -f "${TMP_JSON}"
}
trap cleanup EXIT

echo "[INFO] Fetching OpenAPI: ${OPENAPI_URL}"
curl --noproxy '*' -fsS "${OPENAPI_URL}" > "${TMP_JSON}"

python - "${TMP_JSON}" <<'PY'
import json
import sys

doc = json.load(open(sys.argv[1], "r", encoding="utf-8"))
paths = doc.get("paths", {})

required = {
    "/api/v1/tasks/": {"get"},
    "/api/v1/tasks/users": {"get"},
    "/api/v1/tasks/providers": {"get"},
    "/api/v1/tasks/{task_id}": {"get", "delete"},
    "/api/v1/tasks/{task_id}/preview": {"get"},
    "/api/v1/tasks/{task_id}/download": {"get"},
    "/api/v1/tasks/{task_id}/archive/retry": {"post"},
    "/api/v1/tasks/{task_id}/cancel": {"post"},
}

errors = []
for path, methods in required.items():
    candidate = path
    if candidate not in paths and path.endswith("/"):
        candidate = path[:-1]
    if candidate not in paths:
        errors.append(f"Missing path: {path}")
        continue

    available_methods = {k.lower() for k, v in paths[candidate].items() if isinstance(v, dict)}
    for method in methods:
        if method.lower() not in available_methods:
            errors.append(f"Missing method: {method.upper()} {path}")

if errors:
    print("[ERROR] Unified tasks API check failed:")
    for row in errors:
        print(f"  - {row}")
    sys.exit(1)

print("[OK] Unified tasks API paths/methods are present in OpenAPI.")
PY

echo "[DONE] Unified tasks API check passed"

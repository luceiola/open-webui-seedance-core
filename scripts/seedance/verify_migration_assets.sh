#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

missing=0

check_file() {
  local path="$1"
  if [[ ! -e "${path}" ]]; then
    echo "[MISS] ${path}"
    missing=$((missing + 1))
  else
    echo "[ OK ] ${path}"
  fi
}

echo "[INFO] Verifying migrated Seedance assets under: ${ROOT_DIR}"

# 1) Core backend integration
check_file "backend/open_webui/routers/material_packages.py"

if ! rg -n "material_packages" "backend/open_webui/main.py" >/dev/null 2>&1; then
  echo "[MISS] backend/open_webui/main.py missing material_packages import/include"
  missing=$((missing + 1))
else
  echo "[ OK ] backend/open_webui/main.py has material_packages import/include"
fi

# 2) Tool templates
check_file "templates/seedance_material_package_tool.py"
check_file "templates/seedance_material_package_tool_v2.import.json"
check_file "templates/seedance_video_tool.py"
check_file "templates/prompts/seedance_system_prompt.txt"
check_file "templates/prompts/seedance_video_description_prompt.txt"
check_file "templates/skills/seedance-execution-skill/SKILL.md"
check_file "templates/skills/seedance-user-guide-skill/SKILL.md"

# 3) Docs and scripts migrated from legacy repo
check_file "docs/seedance/16-v1.0-需求文档.md"
check_file "docs/seedance/17-v1.0-开发TODO.md"
check_file "docs/seedance/archive/2026-04-pre-v1/README.md"
check_file "scripts/seedance/bootstrap.sh"
check_file "scripts/seedance/check_material_routes.sh"
check_file "scripts/seedance/run_openwebui.sh"
check_file "scripts/seedance/sanitize_material_zip.py"
check_file "scripts/seedance/sync_openwebui_patches.sh"

echo
if [[ "${missing}" -gt 0 ]]; then
  echo "[FAIL] Missing ${missing} migrated items."
  exit 1
fi

echo "[PASS] Migration asset verification succeeded."

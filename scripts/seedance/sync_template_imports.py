#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_REGISTRY = REPO_ROOT / 'templates' / 'versions' / 'registry.json'
BUILD_SCRIPT = REPO_ROOT / 'scripts' / 'seedance' / 'build_standalone_tools.py'


def _build_standalone_files(check_only: bool = False) -> list[str]:
    cmd = [sys.executable, str(BUILD_SCRIPT)]
    if check_only:
        cmd.append('--check')
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False, capture_output=True, text=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0:
        stderr = (result.stderr or '').strip()
        reason = stderr or '\n'.join(lines) or 'unknown error'
        raise RuntimeError(f'Failed to build standalone tools: {reason}')
    return lines


def sync_import_files(check_only: bool = False, build_standalone: bool = True) -> list[str]:
    messages: list[str] = []

    if build_standalone:
        messages.extend(_build_standalone_files(check_only=False))

    versions = json.loads(VERSIONS_REGISTRY.read_text(encoding='utf-8'))
    components = versions.get('components') if isinstance(versions, dict) else None
    if not isinstance(components, dict):
        raise RuntimeError('Invalid versions registry: components must be an object')

    for component_id, meta in components.items():
        if not isinstance(meta, dict):
            continue
        tool_meta = meta.get('tool') if isinstance(meta.get('tool'), dict) else {}
        tool_file = REPO_ROOT / str(tool_meta.get('file') or '')
        standalone_file = REPO_ROOT / str(tool_meta.get('standalone_file') or '')
        import_file = REPO_ROOT / str(tool_meta.get('import_file') or '')
        if not import_file.exists():
            continue

        content_source = standalone_file if standalone_file.exists() else tool_file
        if not content_source.exists():
            continue

        tool_content = content_source.read_text(encoding='utf-8')
        import_obj = json.loads(import_file.read_text(encoding='utf-8'))
        if not isinstance(import_obj, list) or not import_obj or not isinstance(import_obj[0], dict):
            continue

        current = import_obj[0].get('content')
        if current == tool_content:
            messages.append(f'[{component_id}] already synced ({content_source.relative_to(REPO_ROOT)})')
            continue

        if check_only:
            messages.append(f'[{component_id}] mismatch')
            continue

        import_obj[0]['content'] = tool_content
        import_file.write_text(json.dumps(import_obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        messages.append(f'[{component_id}] synced ({content_source.relative_to(REPO_ROOT)})')

    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description='Sync templates/*_tool.py into *_import.json content field')
    parser.add_argument('--check', action='store_true', help='check only, do not write files')
    parser.add_argument('--skip-build', action='store_true', help='skip standalone bundle build step')
    args = parser.parse_args()

    messages = sync_import_files(check_only=bool(args.check), build_standalone=not bool(args.skip_build))
    for line in messages:
        print(line)

    if args.check and any(msg.endswith('mismatch') for msg in messages):
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())

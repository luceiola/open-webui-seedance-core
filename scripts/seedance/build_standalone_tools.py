#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_REGISTRY = REPO_ROOT / 'templates' / 'versions' / 'registry.json'
SHARED_TOOLKIT = REPO_ROOT / 'templates' / 'shared' / 'toolkit.py'
SHARED_TEMPLATE_REGISTRY = REPO_ROOT / 'templates' / 'shared' / 'template_registry.py'
DIST_DIR = REPO_ROOT / 'templates' / 'dist'

SHARED_IMPORT_PATTERN = re.compile(r'^from shared\.(?:toolkit|template_registry) import \($', flags=re.MULTILINE)


def _load_versions_registry() -> dict[str, Any]:
    payload = json.loads(VERSIONS_REGISTRY.read_text(encoding='utf-8'))
    components = payload.get('components')
    if not isinstance(components, dict):
        raise RuntimeError('Invalid versions registry: components must be an object')
    return components


def _bootstrap_block(*, include_template_registry: bool) -> str:
    toolkit_source = SHARED_TOOLKIT.read_text(encoding='utf-8')
    template_registry_source = SHARED_TEMPLATE_REGISTRY.read_text(encoding='utf-8') if include_template_registry else ''

    lines: list[str] = []
    lines.append('# --- BEGIN STANDALONE SHARED BOOTSTRAP (auto-generated) ---')
    lines.append('import types as _seed_types')
    lines.append('_seed_shared_pkg = sys.modules.get("shared")')
    lines.append('if _seed_shared_pkg is None:')
    lines.append('    _seed_shared_pkg = _seed_types.ModuleType("shared")')
    lines.append('    _seed_shared_pkg.__path__ = []')
    lines.append('    sys.modules["shared"] = _seed_shared_pkg')
    lines.append('if "shared.toolkit" not in sys.modules:')
    lines.append('    _seed_toolkit_mod = _seed_types.ModuleType("shared.toolkit")')
    lines.append(f'    exec({toolkit_source!r}, _seed_toolkit_mod.__dict__)')
    lines.append('    sys.modules["shared.toolkit"] = _seed_toolkit_mod')

    if include_template_registry:
        lines.append('if "shared.template_registry" not in sys.modules:')
        lines.append('    _seed_template_registry_mod = _seed_types.ModuleType("shared.template_registry")')
        lines.append(f'    exec({template_registry_source!r}, _seed_template_registry_mod.__dict__)')
        lines.append('    sys.modules["shared.template_registry"] = _seed_template_registry_mod')

    lines.append('# --- END STANDALONE SHARED BOOTSTRAP ---')
    return '\n'.join(lines) + '\n\n'


def _build_standalone_content(source_text: str) -> str:
    include_template_registry = 'from shared.template_registry import (' in source_text
    needs_shared_bootstrap = (
        'from shared.toolkit import (' in source_text or include_template_registry
    )

    if not needs_shared_bootstrap:
        return source_text

    match = SHARED_IMPORT_PATTERN.search(source_text)
    if not match:
        return source_text

    insert_at = match.start()
    bootstrap = _bootstrap_block(include_template_registry=include_template_registry)
    return source_text[:insert_at] + bootstrap + source_text[insert_at:]


def build_standalone_tools(*, check_only: bool = False) -> list[str]:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    components = _load_versions_registry()

    messages: list[str] = []
    for component_id, meta in components.items():
        if not isinstance(meta, dict):
            continue

        tool_meta = meta.get('tool') if isinstance(meta.get('tool'), dict) else {}
        source_file = REPO_ROOT / str(tool_meta.get('file') or '')
        standalone_file = REPO_ROOT / str(tool_meta.get('standalone_file') or '')

        if not source_file.exists():
            messages.append(f'[{component_id}] skip: source missing')
            continue

        if not standalone_file:
            standalone_file = DIST_DIR / source_file.name

        source_text = source_file.read_text(encoding='utf-8')
        standalone_content = _build_standalone_content(source_text)

        if check_only:
            if not standalone_file.exists():
                messages.append(f'[{component_id}] standalone missing')
                continue
            current = standalone_file.read_text(encoding='utf-8')
            if current != standalone_content:
                messages.append(f'[{component_id}] standalone mismatch')
            else:
                messages.append(f'[{component_id}] standalone up-to-date')
            continue

        standalone_file.parent.mkdir(parents=True, exist_ok=True)
        standalone_file.write_text(standalone_content, encoding='utf-8')
        messages.append(f'[{component_id}] standalone built -> {standalone_file.relative_to(REPO_ROOT)}')

    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description='Build standalone tool files with shared modules embedded.')
    parser.add_argument('--check', action='store_true', help='Check only, do not write output files.')
    args = parser.parse_args()

    messages = build_standalone_tools(check_only=bool(args.check))
    for line in messages:
        print(line)

    if args.check and any('mismatch' in msg or 'missing' in msg for msg in messages):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_REGISTRY = REPO_ROOT / "templates" / "versions" / "registry.json"
ROUTING_REGISTRY = REPO_ROOT / "config" / "seedance_routing_registry.yaml"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_import_sync(component_id: str, tool_file: Path, import_file: Path, errors: list[str]) -> None:
    if not tool_file.exists():
        errors.append(f"[{component_id}] missing tool file: {tool_file}")
        return
    if not import_file.exists():
        errors.append(f"[{component_id}] missing import file: {import_file}")
        return

    tool_content = tool_file.read_text(encoding="utf-8")
    import_obj = _load_json(import_file)
    if not isinstance(import_obj, list) or not import_obj:
        errors.append(f"[{component_id}] import file format must be non-empty JSON list: {import_file}")
        return

    item = import_obj[0] if isinstance(import_obj[0], dict) else {}
    import_content = item.get("content")
    if not isinstance(import_content, str):
        errors.append(f"[{component_id}] missing string field `content` in import file: {import_file}")
        return

    if import_content != tool_content:
        errors.append(
            f"[{component_id}] import content mismatch: {import_file} is out of sync with {tool_file}"
        )


def _check_skill_prompt_headers(component_id: str, meta: dict[str, Any], errors: list[str]) -> None:
    skill_meta = meta.get("skill", {}) if isinstance(meta.get("skill"), dict) else {}
    prompt_meta = meta.get("system_prompt", {}) if isinstance(meta.get("system_prompt"), dict) else {}
    tool_meta = meta.get("tool", {}) if isinstance(meta.get("tool"), dict) else {}

    skill_file = REPO_ROOT / str(skill_meta.get("file") or "")
    prompt_file = REPO_ROOT / str(prompt_meta.get("file") or "")
    tool_file = REPO_ROOT / str(tool_meta.get("file") or "")

    if not skill_file.exists():
        errors.append(f"[{component_id}] missing skill file: {skill_file}")
    else:
        skill_text = skill_file.read_text(encoding="utf-8")
        expected_skill_version = str(skill_meta.get("version") or "").strip()
        if expected_skill_version and f"version: {expected_skill_version}" not in skill_text:
            errors.append(
                f"[{component_id}] skill version marker mismatch: expect `{expected_skill_version}` in {skill_file}"
            )
        if "routing_registry: config/seedance_routing_registry.yaml" not in skill_text:
            errors.append(f"[{component_id}] skill missing routing_registry marker: {skill_file}")
        if "version_registry: templates/versions/registry.json" not in skill_text:
            errors.append(f"[{component_id}] skill missing version_registry marker: {skill_file}")

    if not prompt_file.exists():
        errors.append(f"[{component_id}] missing prompt file: {prompt_file}")
    else:
        prompt_text = prompt_file.read_text(encoding="utf-8")
        expected_prompt_version = str(prompt_meta.get("version") or "").strip()
        if expected_prompt_version and f"[policy_version={expected_prompt_version}]" not in prompt_text:
            errors.append(
                f"[{component_id}] prompt version marker mismatch: expect `{expected_prompt_version}` in {prompt_file}"
            )
        if "[routing_registry=config/seedance_routing_registry.yaml]" not in prompt_text:
            errors.append(f"[{component_id}] prompt missing routing_registry marker: {prompt_file}")
        if "[version_registry=templates/versions/registry.json]" not in prompt_text:
            errors.append(f"[{component_id}] prompt missing version_registry marker: {prompt_file}")

    if tool_file.exists():
        expected_tool_version = str(tool_meta.get("version") or "").strip()
        if expected_tool_version:
            tool_text = tool_file.read_text(encoding="utf-8")
            match = re.search(r"^version:\s*([^\n]+)$", tool_text, flags=re.MULTILINE)
            current_tool_version = str(match.group(1) if match else "").strip()
            if current_tool_version != expected_tool_version:
                errors.append(
                    f"[{component_id}] tool version mismatch: registry={expected_tool_version}, "
                    f"tool={current_tool_version or 'N/A'} ({tool_file})"
                )


def _check_routing_registry(errors: list[str]) -> None:
    if not ROUTING_REGISTRY.exists():
        errors.append(f"missing routing registry: {ROUTING_REGISTRY}")
        return

    try:
        obj = _load_json(ROUTING_REGISTRY)
    except Exception as exc:
        errors.append(f"routing registry parse failed (must be JSON-compatible YAML): {exc}")
        return

    if not isinstance(obj, dict):
        errors.append("routing registry must be an object")
        return

    rules = obj.get("rules")
    if not isinstance(rules, list) or not rules:
        errors.append("routing registry must contain non-empty `rules` list")
        return

    seen: set[str] = set()
    for idx, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            errors.append(f"routing rule #{idx} must be object")
            continue
        rule_id = str(rule.get("rule_id") or "").strip()
        if not rule_id:
            errors.append(f"routing rule #{idx} missing rule_id")
            continue
        if rule_id in seen:
            errors.append(f"duplicate routing rule_id: {rule_id}")
        seen.add(rule_id)


def run_checks() -> list[str]:
    errors: list[str] = []

    if not VERSIONS_REGISTRY.exists():
        return [f"missing versions registry: {VERSIONS_REGISTRY}"]

    versions_obj = _load_json(VERSIONS_REGISTRY)
    components = versions_obj.get("components") if isinstance(versions_obj, dict) else None
    if not isinstance(components, dict) or not components:
        return ["versions registry must contain non-empty object `components`"]

    for component_id, meta in components.items():
        if not isinstance(meta, dict):
            errors.append(f"[{component_id}] component metadata must be object")
            continue

        tool_meta = meta.get("tool") if isinstance(meta.get("tool"), dict) else {}
        tool_file = REPO_ROOT / str(tool_meta.get("file") or "")
        import_file = REPO_ROOT / str(tool_meta.get("import_file") or "")
        _check_import_sync(component_id, tool_file, import_file, errors)
        _check_skill_prompt_headers(component_id, meta, errors)

    _check_routing_registry(errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check templates tool/skill/prompt integrity")
    parser.parse_args()

    errors = run_checks()
    if errors:
        print("[check_templates_integrity] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1

    print("[check_templates_integrity] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

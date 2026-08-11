import ast
import asyncio
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = REPO_ROOT / "templates" / "seedance_material_package_tool.py"
SKILL_PATH = REPO_ROOT / "templates" / "skills" / "seedance-execution-skill" / "SKILL.md"
PROMPT_PATH = REPO_ROOT / "templates" / "prompts" / "seedance_system_prompt.txt"


def _tool_method_docstring(method_name: str) -> str:
    module = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    tools_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Tools"
    )
    method = next(
        node
        for node in tools_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )
    return ast.get_docstring(method) or ""


def _load_tool_module():
    spec = importlib.util.spec_from_file_location("test_seedance_single_call_tool", str(TOOL_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generation_contract_uses_single_tool_call_with_internal_validation():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "直接调用 `generate_video_with_media_assets`" in prompt
    assert "禁止先调用 `resolve_media_asset_references`" in prompt
    assert "生成意图存在时不得先调用该工具" in skill
    assert "同一次调用内先校验引用" in skill

    assert "先调用 `resolve_media_asset_references`，校验失败立即停止" not in prompt
    assert "校验通过后，立即调用 `generate_video_with_media_assets`" not in skill


def test_tool_schema_descriptions_distinguish_validation_from_generation():
    resolve_doc = _tool_method_docstring("resolve_media_asset_references")
    generate_doc = _tool_method_docstring("generate_video_with_media_assets")

    assert "仅在用户明确要求单独校验素材时使用" in resolve_doc
    assert "视频生成请求不要先调用本函数" in resolve_doc
    assert "视频生成请求的唯一提交入口" in generate_doc
    assert "一次调用内先解析并校验" in generate_doc
    assert "无需预先调用校验工具" in generate_doc


def test_generation_entrypoint_stops_before_submit_when_internal_validation_fails(monkeypatch):
    module = _load_tool_module()
    tool = module.Tools()

    async def _fake_resolve(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "data": {
                    "references": ["missing.jpg"],
                    "missing_references": ["missing.jpg"],
                    "ambiguous_references": [],
                    "available_references": ["available.jpg"],
                    "cleaned_prompt": "生成视频",
                    "assets": [],
                },
            },
            ensure_ascii=False,
        )

    async def _unexpected_submit(*args, **kwargs):
        raise AssertionError("generation request must not be submitted after reference validation fails")

    monkeypatch.setattr(tool, "resolve_media_asset_references", _fake_resolve)
    monkeypatch.setattr(tool, "_request", _unexpected_submit)

    payload = json.loads(
        asyncio.run(tool.generate_video_with_media_assets(prompt="使用 %missing.jpg 生成视频"))
    )

    assert payload["ok"] is False
    assert payload["error_code"] == "MissingMediaAssetReferences"
    assert payload["missing_references"] == ["missing.jpg"]

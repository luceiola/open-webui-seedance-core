import asyncio
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[6]
TOOL_PATH = REPO_ROOT / "templates" / "volcengine_media_description_tool.py"


def _load_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(TOOL_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_success_fakes(monkeypatch, tool, *, url: str, content: object = "描述结果"):
    captured: dict[str, object] = {}

    async def _fake_resolve_media(*, reference, media_type, __request__):
        captured["reference"] = reference
        captured["media_type"] = media_type
        return {"ok": True, "url": url, "prompt_resources": []}

    async def _fake_run_au(*, command_args):
        captured["command_args"] = list(command_args)
        return {
            "response": {
                "id": "chatcmpl-media-1",
                "model": "doubao-seed-2-1-turbo-260628",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        }

    monkeypatch.setattr(tool, "_resolve_media", _fake_resolve_media)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run_au)
    return captured


def test_tool_exposes_only_two_public_description_functions():
    module = _load_module("test_volcengine_media_description_public")
    tool = module.Tools()

    public = {
        name
        for name in dir(tool)
        if not name.startswith("_") and callable(getattr(tool, name)) and name != "Valves"
    }
    assert public == {"describe_image", "describe_video"}
    assert tool.valves.AU_API_KEY_ENV == "ARK_API_KEY"


def test_describe_image_uses_image_url_and_default_method(monkeypatch):
    module = _load_module("test_volcengine_media_description_image")
    tool = module.Tools()
    captured = _install_success_fakes(monkeypatch, tool, url="https://media.test/image.png")

    payload = json.loads(asyncio.run(tool.describe_image(image="%folder/image.png")))

    assert payload["ok"] is True
    assert payload["media_type"] == "image"
    assert payload["method"] == "detailed_visual"
    assert payload["content"] == "描述结果"
    assert payload["response_id"] == "chatcmpl-media-1"
    args = captured["command_args"]
    assert args[0] == "ve-multimodal-chat"
    assert "--image-url" in args
    assert "https://media.test/image.png" in args
    assert "--file-url" not in args
    assert "--model" not in args
    assert captured["reference"] == "%folder/image.png"


def test_describe_video_uses_file_url_timeline_and_custom_focus(monkeypatch):
    module = _load_module("test_volcengine_media_description_video")
    tool = module.Tools()
    captured = _install_success_fakes(monkeypatch, tool, url="https://media.test/video.mp4")

    payload = json.loads(
        asyncio.run(
            tool.describe_video(
                video="%video.mp4",
                description_method="video_timeline",
                custom_instruction="重点关注人物服装",
                output_language="zh-CN",
            )
        )
    )

    assert payload["ok"] is True
    assert payload["method"] == "video_timeline"
    args = captured["command_args"]
    assert "--file-url" in args
    assert "https://media.test/video.mp4" in args
    prompt = args[args.index("--prompt") + 1]
    assert "MM:SS" in prompt
    assert "重点关注人物服装" in prompt


def test_image_rejects_video_timeline_before_media_resolution(monkeypatch):
    module = _load_module("test_volcengine_media_description_invalid_timeline")
    tool = module.Tools()

    async def _unexpected_resolve(**kwargs):
        raise AssertionError("media resolution must not run")

    monkeypatch.setattr(tool, "_resolve_media", _unexpected_resolve)
    payload = json.loads(
        asyncio.run(tool.describe_image(image="%image.png", description_method="video_timeline"))
    )

    assert payload["ok"] is False
    assert payload["error_code"] == "InvalidDescriptionMethod"


def test_invalid_method_lists_supported_methods():
    module = _load_module("test_volcengine_media_description_invalid_method")
    tool = module.Tools()

    payload = json.loads(
        asyncio.run(tool.describe_video(video="%video.mp4", description_method="unknown"))
    )

    assert payload["ok"] is False
    assert payload["error_code"] == "InvalidDescriptionMethod"
    assert "quick_overview" in payload["error_message"]


def test_media_resolution_error_is_preserved(monkeypatch):
    module = _load_module("test_volcengine_media_description_missing_ref")
    tool = module.Tools()

    async def _fake_resolve(**kwargs):
        return {
            "ok": False,
            "status_code": 400,
            "error_code": "MissingMediaAssetReferences",
            "error_message": "missing reference",
            "request_id": None,
            "missing_references": ["%missing.png"],
        }

    monkeypatch.setattr(tool, "_resolve_media", _fake_resolve)
    payload = json.loads(asyncio.run(tool.describe_image(image="%missing.png")))

    assert payload["error_code"] == "MissingMediaAssetReferences"
    assert payload["missing_references"] == ["%missing.png"]


def test_direct_http_url_bypasses_media_bridge(monkeypatch):
    module = _load_module("test_volcengine_media_description_direct_url")
    tool = module.Tools()

    class _UnexpectedBridge:
        def __init__(self, **kwargs):
            raise AssertionError("media bridge must not load for a direct URL")

    monkeypatch.setattr(module, "AUMediaReferenceBridge", _UnexpectedBridge)
    resolved = asyncio.run(
        tool._resolve_media(
            reference="https://media.test/direct.png",
            media_type="image",
            __request__=None,
        )
    )

    assert resolved == {
        "ok": True,
        "url": "https://media.test/direct.png",
        "prompt_resources": [],
    }


def test_command_failure_preserves_message_and_request_id(monkeypatch):
    module = _load_module("test_volcengine_media_description_command_error")
    tool = module.Tools()

    async def _fake_resolve(**kwargs):
        return {"ok": True, "url": "https://media.test/image.png", "prompt_resources": []}

    async def _fake_run(**kwargs):
        raise RuntimeError("provider failed request_id=req-media-1")

    monkeypatch.setattr(tool, "_resolve_media", _fake_resolve)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run)
    payload = json.loads(asyncio.run(tool.describe_image(image="%image.png")))

    assert payload["ok"] is False
    assert payload["error_code"] == "CommandExecutionFailed"
    assert payload["request_id"] == "req-media-1"
    assert "provider failed" in payload["error_message"]


def test_structured_content_parts_are_joined(monkeypatch):
    module = _load_module("test_volcengine_media_description_content_parts")
    tool = module.Tools()
    _install_success_fakes(
        monkeypatch,
        tool,
        url="https://media.test/image.png",
        content=[{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}],
    )

    payload = json.loads(asyncio.run(tool.describe_image(image="%image.png")))
    assert payload["content"] == "第一段\n第二段"


def test_invalid_provider_response_is_reported(monkeypatch):
    module = _load_module("test_volcengine_media_description_invalid_response")
    tool = module.Tools()

    async def _fake_resolve(**kwargs):
        return {"ok": True, "url": "https://media.test/video.mp4", "prompt_resources": []}

    async def _fake_run(**kwargs):
        return {"response": {"id": "chatcmpl-empty", "choices": []}}

    monkeypatch.setattr(tool, "_resolve_media", _fake_resolve)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run)
    payload = json.loads(asyncio.run(tool.describe_video(video="%video.mp4")))

    assert payload["ok"] is False
    assert payload["error_code"] == "InvalidProviderResponse"

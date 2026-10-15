import asyncio
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[6]
TOOL_PATH = REPO_ROOT / "templates" / "ai_utility_minimax_h3_tool.py"
PROMPT_PATH = REPO_ROOT / "templates" / "prompts" / "ai_utility_minimax_h3_system_prompt.txt"
SKILL_PATH = REPO_ROOT / "templates" / "skills" / "ai-utility-minimax-h3-execution-skill" / "SKILL.md"


def _load_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(TOOL_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_common_fakes(monkeypatch, module, tool, *, uploads=None, api_response=None):
    captured = {"requests": [], "bridge_payloads": []}

    async def _fake_load_uploads(__files__, __request__):
        _ = __files__, __request__
        return list(uploads or [])

    async def _fake_upload(upload, *, __request__):
        _ = __request__
        return {
            "ok": True,
            "openwebui_file_id": upload["id"],
            "file_id": f"public-{upload['id']}",
            "filename": upload.get("name"),
            "content_type": upload.get("content_type") or "application/octet-stream",
            "media_type": upload.get("media_type"),
        }

    async def _fake_public_request(method, path, *, body=None, headers=None):
        captured["requests"].append(
            {"method": method, "path": path, "body": body, "headers": headers or {}}
        )
        return {"ok": True, "status_code": 202, "data": dict(api_response or {})}

    async def _fake_bridge_upsert(*, requester, payload, __request__):
        _ = requester, __request__
        captured["bridge_payloads"].append(dict(payload))
        return True

    monkeypatch.setattr(tool, "_load_upload_items", _fake_load_uploads)
    monkeypatch.setattr(tool, "_upload_to_public_api", _fake_upload)
    monkeypatch.setattr(tool, "_public_api_request_json", _fake_public_request)
    monkeypatch.setattr(module, "bridge_upsert", _fake_bridge_upsert)
    return captured


def test_ref2v_defaults_to_rh_official_and_uploads_references(monkeypatch):
    module = _load_module("test_ai_utility_h3")
    tool = module.Tools()
    tool.valves.PUBLIC_API_KEY = "test-key"
    captured = _install_common_fakes(
        monkeypatch,
        module,
        tool,
        uploads=[
            {
                "id": "owui-image",
                "name": "reference.png",
                "content_type": "image/png",
                "media_type": "image",
            }
        ],
        api_response={
            "job_id": "h3-ref-1",
            "status": "pending",
            "mode": "ref2v",
            "costs": None,
        },
    )

    raw = asyncio.run(
        tool.create_h3_ref2v_job(
            prompt="参考 %reference.png 生成人物走动",
            confirm_processing=True,
            reference_images=["%reference.png"],
            __files__=[{"id": "owui-image"}],
            __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["job_id"] == "h3-ref-1"
    request = captured["requests"][0]
    assert request["path"] == "/api/v1/minimax-h3/ref2v/jobs"
    assert request["body"]["context_ir"] == "rh-official"
    assert request["body"]["confirm_processing"] is True
    assert request["body"]["reference_image_file_ids"] == ["public-owui-image"]
    assert request["headers"]["Idempotency-Key"]
    assert captured["bridge_payloads"][0]["provider"] == "ai_utility_minimax_h3"
    assert captured["bridge_payloads"][0]["archive_required"] is False


def test_fl2v_pure_text_sends_no_frames(monkeypatch):
    module = _load_module("test_ai_utility_h3_fl2v_text")
    tool = module.Tools()
    tool.valves.PUBLIC_API_KEY = "test-key"
    captured = _install_common_fakes(
        monkeypatch,
        module,
        tool,
        uploads=[],
        api_response={"job_id": "h3-fl2v-1", "status": "pending", "mode": "fl2v"},
    )

    raw = asyncio.run(
        tool.create_h3_fl2v_job(
            prompt="海面日出，镜头缓慢推进",
            confirm_processing=True,
            __metadata__={"chat_id": "chat-1", "message_id": "msg-2"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    body = captured["requests"][0]["body"]
    assert body["first_frame_file_id"] is None
    assert body["last_frame_file_id"] is None
    assert body["context_ir"] == "rh-official"


def test_fl2v_first_and_last_frames_resolve_upload_names(monkeypatch):
    module = _load_module("test_ai_utility_h3_fl2v_frames")
    tool = module.Tools()
    tool.valves.PUBLIC_API_KEY = "test-key"
    captured = _install_common_fakes(
        monkeypatch,
        module,
        tool,
        uploads=[
            {"id": "first", "name": "first.png", "content_type": "image/png", "media_type": "image"},
            {"id": "last", "name": "last.png", "content_type": "image/png", "media_type": "image"},
        ],
        api_response={"job_id": "h3-fl2v-2", "status": "pending", "mode": "fl2v"},
    )

    raw = asyncio.run(
        tool.create_h3_fl2v_job(
            prompt="从 %first.png 自然过渡到 %last.png",
            first_frame="%first.png",
            last_frame="%last.png",
            confirm_processing=True,
            __files__=[{"id": "first"}, {"id": "last"}],
            __metadata__={"chat_id": "chat-1", "message_id": "msg-3"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    body = captured["requests"][0]["body"]
    assert body["first_frame_file_id"] == "public-first"
    assert body["last_frame_file_id"] == "public-last"


def test_ref2v_requires_reference_attachment(monkeypatch):
    module = _load_module("test_ai_utility_h3_ref_required")
    tool = module.Tools()
    tool.valves.PUBLIC_API_KEY = "test-key"
    _install_common_fakes(monkeypatch, module, tool, uploads=[], api_response={})

    raw = asyncio.run(
        tool.create_h3_ref2v_job(
            prompt="生成一段视频",
            confirm_processing=True,
            __files__=[],
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "ReferencesRequired"


def test_fl2v_rejects_additional_reference_media(monkeypatch):
    module = _load_module("test_ai_utility_h3_fl2v_conflict")
    tool = module.Tools()
    tool.valves.PUBLIC_API_KEY = "test-key"
    _install_common_fakes(
        monkeypatch,
        module,
        tool,
        uploads=[
            {"id": "first", "name": "first.png", "content_type": "image/png", "media_type": "image"},
            {"id": "extra", "name": "style.mp4", "content_type": "video/mp4", "media_type": "video"},
        ],
    )

    raw = asyncio.run(
        tool.create_h3_fl2v_job(
            prompt="以 %first.png 为首帧，同时参考 %style.mp4",
            first_frame="%first.png",
            confirm_processing=True,
            __files__=[{"id": "first"}, {"id": "extra"}],
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "H3ModeConflict"


def test_confirmation_is_required_before_paid_request(monkeypatch):
    module = _load_module("test_ai_utility_h3_confirm")
    tool = module.Tools()
    tool.valves.PUBLIC_API_KEY = "test-key"
    captured = _install_common_fakes(monkeypatch, module, tool, uploads=[], api_response={})

    raw = asyncio.run(tool.create_h3_fl2v_job(prompt="文生视频"))
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "ProcessingConfirmationRequired"
    assert captured["requests"] == []


def test_get_job_bridges_context_and_video_costs(monkeypatch):
    module = _load_module("test_ai_utility_h3_costs")
    tool = module.Tools()
    tool.valves.PUBLIC_API_KEY = "test-key"
    costs = {
        "currency": "CNY",
        "context_ir": {"provider": "rh-official", "available": True, "total": "1.00"},
        "video_generation": {"provider": "runninghub", "available": True, "total": "1.50"},
        "known_subtotal": "2.50",
        "total": "2.50",
    }
    captured = _install_common_fakes(
        monkeypatch,
        module,
        tool,
        api_response={"job_id": "h3-cost-1", "status": "completed", "mode": "ref2v", "costs": costs},
    )

    raw = asyncio.run(tool.get_h3_job("ref2v", "h3-cost-1"))
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["costs"]["total"] == "2.50"
    bridge_payload = captured["bridge_payloads"][0]
    assert bridge_payload["status"] == "SUCCEEDED"
    assert bridge_payload["costs"]["context_ir"]["total"] == "1.00"
    assert bridge_payload["costs"]["video_generation"]["total"] == "1.50"
    assert "prompt_text" not in bridge_payload
    assert "generation_params" not in bridge_payload
    assert "tool_name" not in bridge_payload


def test_attach_video_returns_openwebui_download_markdown(monkeypatch):
    module = _load_module("test_ai_utility_h3_attach")
    tool = module.Tools()
    tool.valves.PUBLIC_API_KEY = "test-key"
    bridge_payloads = []

    async def _fake_request(method, path, *, body=None, headers=None):
        _ = method, body, headers
        assert path == "/api/v1/minimax-h3/fl2v/jobs/h3-video-1"
        return {
            "ok": True,
            "status_code": 200,
            "data": {
                "job_id": "h3-video-1",
                "status": "completed",
                "mode": "fl2v",
                "video_url": "/api/v1/minimax-h3/fl2v/jobs/h3-video-1/video",
                "costs": {"currency": "CNY", "total": "0.8"},
            },
        }

    async def _fake_download(path):
        assert path.endswith("/video")
        return b"mp4-data"

    async def _fake_openwebui_upload(content, *, filename, __request__):
        _ = __request__
        assert content == b"mp4-data"
        assert filename == "minimax-h3-h3-video-1.mp4"
        return {
            "ok": True,
            "id": "openwebui-video-1",
            "name": filename,
            "url": "/api/v1/files/openwebui-video-1/content",
        }

    monkeypatch.setattr(tool, "_public_api_request_json", _fake_request)
    monkeypatch.setattr(tool, "_public_api_download", _fake_download)
    monkeypatch.setattr(tool, "_upload_video_to_openwebui", _fake_openwebui_upload)

    async def _fake_bridge_upsert(*, requester, payload, __request__):
        _ = requester, __request__
        bridge_payloads.append(dict(payload))
        return True

    monkeypatch.setattr(module, "bridge_upsert", _fake_bridge_upsert)

    raw = asyncio.run(tool.attach_h3_video("fl2v", "h3-video-1"))
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["openwebui_file_id"] == "openwebui-video-1"
    assert payload["download_url_markdown"] == "[下载视频](/api/v1/files/openwebui-video-1/content)"
    assert bridge_payloads[0]["video_preview_url"] == "/api/v1/files/openwebui-video-1/content"
    assert bridge_payloads[0]["video_download_url"].endswith("?attachment=true")


def test_prompt_and_skill_keep_rh_official_and_mode_routing_contract():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "默认必须传 `context_ir=rh-official`" in prompt
    assert "只有用户明确说 `none` 或 `doubao` 时才覆盖" in prompt
    assert "用户只提供文本、没有附件或参考素材" in prompt
    assert "首帧、尾帧或首尾帧" in prompt
    assert "参考图片、参考视频或参考音频任务" in prompt
    assert "默认 9:16" in skill
    assert "默认值固定为 `rh-official`" in skill

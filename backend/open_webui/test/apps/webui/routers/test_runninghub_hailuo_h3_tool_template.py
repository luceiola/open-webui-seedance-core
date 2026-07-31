import asyncio
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[6]
TOOL_PATH = REPO_ROOT / "templates" / "runninghub_hailuo_h3_tool.py"


def _load_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(TOOL_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_common_fakes(monkeypatch, tool, *, media_result=None, submit_result=None):
    captured: dict[str, object] = {}
    bridge_calls: list[dict[str, object]] = []

    async def _fake_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "runninghub",
            "credential_alias": "k2",
            "routing_group_id": "group-2",
            "api_key": "rh-key-2",
        }

    async def _fake_media(**kwargs):
        captured.setdefault("media_calls", []).append(dict(kwargs))
        if media_result is not None:
            return dict(media_result)
        return {
            "ok": True,
            "images": list(kwargs.get("images") or []),
            "videos": list(kwargs.get("videos") or []),
            "audios": list(kwargs.get("audios") or []),
            "input_images": list(kwargs.get("images") or []),
            "input_videos": list(kwargs.get("videos") or []),
            "input_audios": list(kwargs.get("audios") or []),
            "prompt_resources": [],
            "inferred_prompt_inputs": {"images": [], "videos": [], "audios": []},
        }

    async def _fake_au(*, command_args, timeout_seconds=None, api_key_env="", api_key=""):
        captured["command_args"] = list(command_args)
        captured["api_key"] = api_key
        return submit_result or {"submit": {"taskId": "hailuo-task-1", "status": "QUEUED"}}

    async def _fake_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_credential)
    monkeypatch.setattr(tool, "_resolve_au_media_inputs", _fake_media)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_au)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_upsert)
    monkeypatch.setattr(tool, "_schedule_status_refresh", lambda **kwargs: captured.update(refresh=kwargs))
    return captured, bridge_calls


def test_no_references_routes_to_t2v(monkeypatch):
    module = _load_module("test_hailuo_t2v")
    tool = module.Tools()
    captured, bridge_calls = _install_common_fakes(monkeypatch, tool)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_hailuo_h3(
            prompt="海面日出，镜头缓慢前推",
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["mode"] == "t2v"
    args = captured["command_args"]
    assert args[:3] == ["rh-hailuo-h3-video", "--mode", "t2v"]
    assert "--resolution" in args and "2K" in args
    assert "--duration" in args and "5" in args
    assert "--ratio" in args and "adaptive" in args
    assert "--no-save-videos" in args
    assert captured["api_key"] == "rh-key-2"
    assert payload["video_url"] is None
    assert payload["output_urls"] == []
    assert bridge_calls[0]["generation_params"]["mode"] == "t2v"
    assert bridge_calls[0]["generate_audio"] is None


def test_hailuo_tool_does_not_expose_seedance2_generation():
    module = _load_module("test_hailuo_dedicated_tool")
    tool = module.Tools()

    assert not callable(getattr(tool, "generate_video_with_runninghub_seedance2", None))
    assert callable(tool.generate_video_with_runninghub_hailuo_h3)
    assert callable(tool.list_generation_tasks)
    assert callable(tool.get_generation_task_status)
    assert callable(tool.wait_generation_task)


def test_explicit_first_and_last_frames_route_to_i2v(monkeypatch):
    module = _load_module("test_hailuo_i2v")
    tool = module.Tools()
    media_result = {
        "ok": True,
        "images": ["https://media.test/first.png", "https://media.test/last.png"],
        "videos": [],
        "audios": [],
        "prompt_resources": [
            {"name": "first.png", "url": "https://media.test/first.png"},
            {"name": "last.png", "url": "https://media.test/last.png"},
        ],
    }
    captured, bridge_calls = _install_common_fakes(monkeypatch, tool, media_result=media_result)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_hailuo_h3(
            prompt="以 %first.png 为首帧，以 %last.png 为尾帧，平滑运镜",
            first_frame="%first.png",
            last_frame="%last.png",
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["mode"] == "i2v"
    args = captured["command_args"]
    assert args[:3] == ["rh-hailuo-h3-video", "--mode", "i2v"]
    assert args[args.index("--first-frame") + 1] == "https://media.test/first.png"
    assert args[args.index("--last-frame") + 1] == "https://media.test/last.png"
    assert "--ratio" not in args
    assert bridge_calls[0]["generation_params"]["first_frame"] == "%first.png"
    assert bridge_calls[0]["generation_params"]["last_frame"] == "%last.png"


def test_regular_media_references_route_to_multimodal(monkeypatch):
    module = _load_module("test_hailuo_multimodal")
    tool = module.Tools()
    media_result = {
        "ok": True,
        "images": ["https://media.test/reference.png"],
        "videos": ["https://media.test/reference.mp4"],
        "audios": [],
        "input_images": ["%reference.png"],
        "input_videos": ["%reference.mp4"],
        "input_audios": [],
        "prompt_resources": [],
        "inferred_prompt_inputs": {
            "images": ["%reference.png"],
            "videos": ["%reference.mp4"],
            "audios": [],
        },
    }
    captured, bridge_calls = _install_common_fakes(monkeypatch, tool, media_result=media_result)

    prompt = "参考 %reference.png 和 %reference.mp4 生成一段广告视频"
    raw = asyncio.run(
        tool.generate_video_with_runninghub_hailuo_h3(prompt=prompt, __user__={"id": "u1"})
    )
    payload = json.loads(raw)

    assert payload["mode"] == "multimodal"
    args = captured["command_args"]
    assert args[:3] == ["rh-hailuo-h3-video", "--mode", "multimodal"]
    assert "--image" in args and "https://media.test/reference.png" in args
    assert "--video" in args and "https://media.test/reference.mp4" in args
    assert "%reference.png" not in args
    assert bridge_calls[0]["prompt_text"] == prompt


def test_frame_and_multimodal_inputs_fail_before_routing_or_submit(monkeypatch):
    module = _load_module("test_hailuo_mode_conflict")
    tool = module.Tools()

    async def _unexpected_credential(**kwargs):
        raise AssertionError("credential resolution must not run for a mode conflict")

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _unexpected_credential)
    raw = asyncio.run(
        tool.generate_video_with_runninghub_hailuo_h3(
            prompt="以首帧开场，同时参考另一段视频",
            first_frame="%first.png",
            videos=["%reference.mp4"],
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "HailuoH3ModeConflict"


def test_additional_prompt_reference_conflicts_with_frame_mode(monkeypatch):
    module = _load_module("test_hailuo_prompt_mode_conflict")
    tool = module.Tools()
    raw = asyncio.run(
        tool.generate_video_with_runninghub_hailuo_h3(
            prompt="以 %first.png 为首帧，同时参考 %style.png",
            first_frame="%first.png",
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "HailuoH3ModeConflict"
    assert "%style.png" in payload["error_message"]


def test_missing_real_task_id_does_not_create_task(monkeypatch):
    module = _load_module("test_hailuo_missing_task_id")
    tool = module.Tools()
    _, bridge_calls = _install_common_fakes(
        monkeypatch,
        tool,
        submit_result={"submit": {"status": "QUEUED", "requestId": "req-1"}},
    )

    raw = asyncio.run(
        tool.generate_video_with_runninghub_hailuo_h3(
            prompt="生成一段测试视频",
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["task_id"] is None
    assert payload["status"] == "FAILED"
    assert payload["error_code"] == "MissingTaskId"
    assert bridge_calls == []


def test_response_id_and_camel_case_failure_fields_are_preserved(monkeypatch):
    module = _load_module("test_hailuo_response_id")
    tool = module.Tools()
    captured, bridge_calls = _install_common_fakes(
        monkeypatch,
        tool,
        submit_result={
            "submit": {
                "responseId": "response-hailuo-1",
                "status": "FAILED",
                "errorCode": "H3_BAD_INPUT",
                "errorMessage": "invalid Hailuo input",
                "requestId": "request-hailuo-1",
            }
        },
    )

    raw = asyncio.run(
        tool.generate_video_with_runninghub_hailuo_h3(
            prompt="失败字段测试",
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["task_id"] == "response-hailuo-1"
    assert payload["error_code"] == "H3_BAD_INPUT"
    assert payload["error_message"] == "invalid Hailuo input"
    assert payload["request_id"] == "request-hailuo-1"
    assert bridge_calls[0]["request_id"] == "request-hailuo-1"


def test_hailuo_validation_rejects_invalid_duration_and_resolution(monkeypatch):
    module = _load_module("test_hailuo_validation")
    tool = module.Tools()

    invalid_duration = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_hailuo_h3(
                prompt="test",
                duration=4,
                __user__={"id": "u1"},
            )
        )
    )
    invalid_resolution = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_hailuo_h3(
                prompt="test",
                resolution="1080p",
                __user__={"id": "u1"},
            )
        )
    )

    assert invalid_duration["error_code"] == "InvalidParameter"
    assert invalid_resolution["error_code"] == "InvalidParameter"


def test_multimodal_media_limits_fail_before_submit(monkeypatch):
    module = _load_module("test_hailuo_media_limits")
    tool = module.Tools()
    media_result = {
        "ok": True,
        "images": [f"https://media.test/{index}.png" for index in range(10)],
        "videos": [],
        "audios": [],
        "prompt_resources": [],
    }
    captured, _ = _install_common_fakes(monkeypatch, tool, media_result=media_result)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_hailuo_h3(
            prompt="参考多张图片生成视频",
            images=[f"%image_{index}.png" for index in range(10)],
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "InvalidParameter"
    assert "command_args" not in captured

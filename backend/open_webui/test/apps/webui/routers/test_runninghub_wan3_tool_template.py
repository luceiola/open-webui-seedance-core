import asyncio
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[6]
TOOL_PATH = REPO_ROOT / "templates" / "runninghub_wan3_tool.py"


def _load_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(TOOL_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_fakes(monkeypatch, tool, *, media_result=None, submit_result=None):
    captured = {}
    bridge_calls = []

    async def fake_credential(**kwargs):
        return {
            "ok": True,
            "provider": "runninghub",
            "credential_alias": "k2",
            "routing_group_id": "group-2",
            "api_key": "rh-key-2",
        }

    async def fake_media(**kwargs):
        captured["media"] = dict(kwargs)
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
        }

    async def fake_au(**kwargs):
        captured["command_args"] = list(kwargs["command_args"])
        captured["api_key"] = kwargs.get("api_key")
        return submit_result or {"submit": {"taskId": "wan3-task-1", "status": "QUEUED"}}

    async def fake_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(tool, "_resolve_vendor_credential", fake_credential)
    monkeypatch.setattr(tool, "_resolve_au_media_inputs", fake_media)
    monkeypatch.setattr(tool, "_run_au_vendor_json", fake_au)
    monkeypatch.setattr(tool, "_bridge_upsert_task", fake_upsert)
    monkeypatch.setattr(tool, "_schedule_status_refresh", lambda **kwargs: captured.update(refresh=kwargs))
    return captured, bridge_calls


def _arg_value(args, name):
    return args[args.index(name) + 1]


def test_pure_text_routes_to_reference_to_video_with_720p(monkeypatch):
    tool = _load_module("test_wan3_text").Tools()
    captured, calls = _install_fakes(monkeypatch, tool)

    payload = json.loads(asyncio.run(tool.generate_video_with_runninghub_wan3(prompt="海面日出", __user__={"id": "u1"})))

    assert payload["ok"] is True
    assert payload["mode"] == "reference-to-video"
    args = captured["command_args"]
    assert args[:3] == ["rh-wan3-video", "--mode", "reference-to-video"]
    assert _arg_value(args, "--resolution") == "720P"
    assert _arg_value(args, "--ratio") == "adaptive"
    assert _arg_value(args, "--duration") == "5"
    assert payload["video_url"] is None
    assert calls[0]["task_id"] == "wan3-task-1"


def test_reference_media_routes_to_reference_to_video(monkeypatch):
    tool = _load_module("test_wan3_reference").Tools()
    captured, calls = _install_fakes(
        monkeypatch,
        tool,
        media_result={
            "ok": True,
            "images": ["https://media.test/ref.png"],
            "videos": ["https://media.test/ref.mp4"],
            "audios": [],
            "input_images": ["%ref.png"],
            "input_videos": ["%ref.mp4"],
            "input_audios": [],
            "prompt_resources": [],
        },
    )

    payload = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_wan3(
                prompt="参考 %ref.png 和 %ref.mp4",
                images=["%ref.png"],
                videos=["%ref.mp4"],
                __user__={"id": "u1"},
            )
        )
    )

    args = captured["command_args"]
    assert _arg_value(args, "--mode") == "reference-to-video"
    assert "https://media.test/ref.png" in args
    assert "https://media.test/ref.mp4" in args
    assert "%ref.png" not in args
    assert calls[0]["generation_params"]["input_images"] == ["%ref.png"]


def test_first_last_frames_route_to_image_to_video(monkeypatch):
    tool = _load_module("test_wan3_frames").Tools()
    captured, calls = _install_fakes(
        monkeypatch,
        tool,
        media_result={
            "ok": True,
            "images": ["https://media.test/first.png", "https://media.test/last.png"],
            "videos": [],
            "audios": [],
            "prompt_resources": [],
        },
    )

    payload = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_wan3(
                prompt="平滑转场",
                first_frame="%first.png",
                last_frame="%last.png",
                __user__={"id": "u1"},
            )
        )
    )

    args = captured["command_args"]
    assert payload["mode"] == "image-to-video"
    assert _arg_value(args, "--mode") == "image-to-video"
    assert _arg_value(args, "--first-frame") == "https://media.test/first.png"
    assert _arg_value(args, "--last-frame") == "https://media.test/last.png"
    assert _arg_value(args, "--resolution") == "720P"
    assert calls[0]["generation_params"]["first_frame"] == "%first.png"


def test_frame_and_reference_inputs_fail_before_submit(monkeypatch):
    tool = _load_module("test_wan3_conflict").Tools()

    async def unexpected(**kwargs):
        raise AssertionError("credential resolution must not run")

    monkeypatch.setattr(tool, "_resolve_vendor_credential", unexpected)
    payload = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_wan3(
                prompt="冲突",
                first_frame="%first.png",
                images=["%style.png"],
                __user__={"id": "u1"},
            )
        )
    )
    assert payload["ok"] is False
    assert payload["error_code"] == "Wan3ModeConflict"


def test_missing_task_id_does_not_create_task(monkeypatch):
    tool = _load_module("test_wan3_missing_task").Tools()
    captured, calls = _install_fakes(monkeypatch, tool, submit_result={"submit": {"status": "QUEUED", "requestId": "req-1"}})

    payload = json.loads(asyncio.run(tool.generate_video_with_runninghub_wan3(prompt="测试", __user__={"id": "u1"})))

    assert payload["ok"] is False
    assert payload["error_code"] == "MissingTaskId"
    assert calls == []
    assert captured["api_key"] == "rh-key-2"


def test_camel_case_provider_error_is_preserved(monkeypatch):
    tool = _load_module("test_wan3_error").Tools()
    _, calls = _install_fakes(
        monkeypatch,
        tool,
        submit_result={
            "submit": {
                "taskId": "wan3-task-error",
                "status": "FAILED",
                "errorCode": "WAN_BAD_INPUT",
                "errorMessage": "invalid input",
                "requestId": "req-wan3-1",
            }
        },
    )

    payload = json.loads(asyncio.run(tool.generate_video_with_runninghub_wan3(prompt="失败测试", __user__={"id": "u1"})))

    assert payload["ok"] is True
    assert payload["error_code"] == "WAN_BAD_INPUT"
    assert payload["error_message"] == "invalid input"
    assert payload["request_id"] == "req-wan3-1"
    assert calls[0]["status"] == "FAILED"

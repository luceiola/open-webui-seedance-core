import asyncio
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[6]
TOOL_PATH = REPO_ROOT / "templates" / "runninghub_seedance25_tool.py"


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
        return submit_result or {"submit": {"taskId": "seedance25-task-1", "status": "QUEUED"}}

    async def _fake_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_credential)
    monkeypatch.setattr(tool, "_resolve_au_media_inputs", _fake_media)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_au)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_upsert)
    monkeypatch.setattr(tool, "_schedule_status_refresh", lambda **kwargs: captured.update(refresh=kwargs))
    return captured, bridge_calls


def _arg_value(args, name):
    return args[args.index(name) + 1]


def test_defaults_route_to_t2v_and_are_persisted(monkeypatch):
    module = _load_module("test_seedance25_t2v")
    tool = module.Tools()
    captured, bridge_calls = _install_common_fakes(monkeypatch, tool)

    payload = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_seedance25(
                prompt="海面日出，镜头缓慢前推",
                __user__={"id": "u1"},
            )
        )
    )

    assert payload["ok"] is True
    assert payload["mode"] == "t2v"
    args = captured["command_args"]
    assert args[:3] == ["rh-seedance2.5-video", "--mode", "t2v"]
    assert _arg_value(args, "--resolution") == "720p"
    assert _arg_value(args, "--duration") == "5"
    assert _arg_value(args, "--ratio") == "9:16"
    assert "--generate-audio" in args
    assert "--real-person-mode" in args
    assert "--include-modal-order-hint" in args
    assert "--no-return-last-frame" in args
    assert "--no-web-search" in args
    assert _arg_value(args, "--bitrate-mode") == "standard"
    assert _arg_value(args, "--seed") == "-1"
    assert _arg_value(args, "--output-format") == "mp4"
    assert "--no-wait" in args and "--no-save-videos" in args
    assert payload["video_url"] is None
    assert bridge_calls[0]["model"] == "seedance2.5"
    assert bridge_calls[0]["generation_params"]["generate_audio"] is True
    assert bridge_calls[0]["generation_params"]["real_person_mode"] is True


def test_tool_exposes_only_seedance25_generation_and_shared_task_methods():
    module = _load_module("test_seedance25_dedicated")
    tool = module.Tools()

    assert not callable(getattr(tool, "generate_video_with_runninghub_seedance2", None))
    assert not callable(getattr(tool, "generate_video_with_runninghub_hailuo_h3", None))
    assert callable(tool.generate_video_with_runninghub_seedance25)
    assert callable(tool.list_generation_tasks)
    assert callable(tool.get_generation_task_status)
    assert callable(tool.wait_generation_task)


def test_explicit_frames_route_to_i2v_with_adaptive_ratio(monkeypatch):
    module = _load_module("test_seedance25_i2v")
    tool = module.Tools()
    media_result = {
        "ok": True,
        "images": ["https://media.test/first.png", "https://media.test/last.png"],
        "videos": [],
        "audios": [],
        "prompt_resources": [],
    }
    captured, bridge_calls = _install_common_fakes(monkeypatch, tool, media_result=media_result)

    payload = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_seedance25(
                prompt="以 %first.png 为首帧，以 %last.png 为尾帧",
                first_frame="%first.png",
                last_frame="%last.png",
                ratio="16:9",
                conversion_slots=["firstFrameUrl"],
                __user__={"id": "u1"},
            )
        )
    )

    args = captured["command_args"]
    assert payload["mode"] == "i2v"
    assert payload["ratio"] == "adaptive"
    assert _arg_value(args, "--ratio") == "adaptive"
    assert _arg_value(args, "--first-frame") == "https://media.test/first.png"
    assert _arg_value(args, "--last-frame") == "https://media.test/last.png"
    assert _arg_value(args, "--conversion-slot") == "firstFrameUrl"
    assert bridge_calls[0]["generation_params"]["ratio"] == "adaptive"


def test_regular_references_route_to_multimodal_and_preserve_prompt(monkeypatch):
    module = _load_module("test_seedance25_multimodal")
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
    prompt = "参考 %reference.png 和 %reference.mp4 生成广告视频"

    payload = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_seedance25(
                prompt=prompt,
                image_refs=["主体"],
                video_refs=["运镜"],
                conversion_slots=["image1", "video1"],
                __user__={"id": "u1"},
            )
        )
    )

    args = captured["command_args"]
    assert payload["mode"] == "multimodal"
    assert "https://media.test/reference.png" in args
    assert "https://media.test/reference.mp4" in args
    assert "%reference.png" not in args
    assert bridge_calls[0]["prompt_text"] == prompt
    assert bridge_calls[0]["generation_params"]["image_refs"] == ["主体"]


def test_advanced_overrides_are_forwarded(monkeypatch):
    module = _load_module("test_seedance25_advanced")
    tool = module.Tools()
    captured, _ = _install_common_fakes(monkeypatch, tool)

    payload = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_seedance25(
                prompt="电影感城市延时",
                resolution="4k",
                duration=12,
                ratio="21:9",
                generate_audio=False,
                real_person_mode=False,
                include_modal_order_hint=False,
                return_last_frame=True,
                web_search=True,
                bitrate_mode="high",
                seed=42,
                output_format="mov",
                __user__={"id": "u1"},
            )
        )
    )

    args = captured["command_args"]
    assert payload["ok"] is True
    assert _arg_value(args, "--resolution") == "4k"
    assert _arg_value(args, "--duration") == "12"
    assert _arg_value(args, "--ratio") == "21:9"
    assert "--no-generate-audio" in args
    assert "--no-real-person-mode" in args
    assert "--no-include-modal-order-hint" in args
    assert "--return-last-frame" in args
    assert "--web-search" in args
    assert _arg_value(args, "--bitrate-mode") == "high"
    assert _arg_value(args, "--seed") == "42"
    assert _arg_value(args, "--output-format") == "mov"


def test_conflicting_modes_and_mode_specific_parameters_fail_before_submit(monkeypatch):
    module = _load_module("test_seedance25_conflicts")
    tool = module.Tools()

    mixed = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_seedance25(
                prompt="首帧加参考视频",
                first_frame="%first.png",
                videos=["%reference.mp4"],
                __user__={"id": "u1"},
            )
        )
    )
    invalid_search = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_seedance25(
                prompt="参考 %reference.png 生成",
                images=["%reference.png"],
                web_search=True,
                __user__={"id": "u1"},
            )
        )
    )

    assert mixed["error_code"] == "Seedance25ModeConflict"
    assert invalid_search["error_code"] == "InvalidParameter"
    assert "t2v" in invalid_search["error_message"]


def test_validation_and_media_limits_fail_before_submit(monkeypatch):
    module = _load_module("test_seedance25_validation")
    tool = module.Tools()

    invalid_duration = json.loads(
        asyncio.run(tool.generate_video_with_runninghub_seedance25(prompt="test", duration=31, __user__={"id": "u1"}))
    )
    invalid_seed = json.loads(
        asyncio.run(tool.generate_video_with_runninghub_seedance25(prompt="test", seed=-2, __user__={"id": "u1"}))
    )
    media_result = {
        "ok": True,
        "images": [f"https://media.test/{index}.png" for index in range(31)],
        "videos": [],
        "audios": [],
        "prompt_resources": [],
    }
    captured, _ = _install_common_fakes(monkeypatch, tool, media_result=media_result)
    too_many = json.loads(
        asyncio.run(
            tool.generate_video_with_runninghub_seedance25(
                prompt="参考多图",
                images=[f"%image_{index}.png" for index in range(31)],
                __user__={"id": "u1"},
            )
        )
    )

    assert invalid_duration["error_code"] == "InvalidParameter"
    assert invalid_seed["error_code"] == "InvalidParameter"
    assert too_many["error_code"] == "InvalidParameter"
    assert "command_args" not in captured


def test_missing_task_id_and_failure_fields_are_preserved(monkeypatch):
    module = _load_module("test_seedance25_task_ids")
    tool = module.Tools()
    _, bridge_calls = _install_common_fakes(
        monkeypatch,
        tool,
        submit_result={"submit": {"status": "QUEUED", "requestId": "req-1"}},
    )
    missing = json.loads(
        asyncio.run(tool.generate_video_with_runninghub_seedance25(prompt="测试", __user__={"id": "u1"}))
    )
    assert missing["ok"] is False
    assert missing["error_code"] == "MissingTaskId"
    assert missing["request_id"] == "req-1"
    assert bridge_calls == []

    tool = module.Tools()
    _, bridge_calls = _install_common_fakes(
        monkeypatch,
        tool,
        submit_result={
            "submit": {
                "responseId": "response-25-1",
                "status": "FAILED",
                "errorCode": "S25_BAD_INPUT",
                "errorMessage": "invalid input",
                "requestId": "request-25-1",
            }
        },
    )
    failure = json.loads(
        asyncio.run(tool.generate_video_with_runninghub_seedance25(prompt="失败", __user__={"id": "u1"}))
    )
    assert failure["task_id"] == "response-25-1"
    assert failure["error_code"] == "S25_BAD_INPUT"
    assert failure["error_message"] == "invalid input"
    assert failure["request_id"] == "request-25-1"
    assert bridge_calls[0]["request_id"] == "request-25-1"

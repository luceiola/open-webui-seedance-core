import asyncio
import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
RHS2_TOOL_PATH = REPO_ROOT / "templates" / "runninghub_seedance2_tool.py"
BTN_TOOL_PATH = REPO_ROOT / "templates" / "btn_image2_tool.py"


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runninghub_seedance2_defaults_and_reminders(monkeypatch):
    module = _load_module("test_runninghub_seedance2_defaults", RHS2_TOOL_PATH)
    tool = module.Tools()

    captured: dict[str, object] = {}

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "runninghub",
            "credential_alias": "k2",
            "routing_group_id": "grp_seedance_k2",
            "api_key": "rh-k2",
            "source": "key_routing",
        }

    async def _fake_run_au(*, command_args, timeout_seconds=None, api_key_env="", api_key=""):
        captured["command_args"] = list(command_args)
        captured["timeout_seconds"] = timeout_seconds
        captured["api_key_env"] = api_key_env
        captured["api_key"] = api_key
        return {
            "submit": {
                "taskId": "task-rhs2-001",
                "status": "PENDING",
            }
        }

    bridge_calls: list[dict[str, object]] = []

    async def _fake_bridge_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    refresh_calls: list[dict[str, object]] = []

    def _fake_schedule_refresh(**kwargs):
        refresh_calls.append(dict(kwargs))

    async def _fake_resolve_media_inputs(*, prompt_text, images, videos, audios, chat_id, __request__):
        return {
            "ok": True,
            "images": list(images or []),
            "videos": list(videos or []),
            "audios": list(audios or []),
            "prompt_resources": [],
        }

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_resolve_au_media_inputs", _fake_resolve_media_inputs)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run_au)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_bridge_upsert)
    monkeypatch.setattr(tool, "_schedule_status_refresh", _fake_schedule_refresh)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_seedance2(
            prompt="让人物在城市街道行走",
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["task_id"] == "task-rhs2-001"
    assert payload["model"] == "mini"

    command_args = captured["command_args"]
    assert isinstance(command_args, list)
    assert command_args[0] == "rh-seedance2-mini-video"
    assert "--resolution" in command_args
    assert "720p" in command_args
    assert "--ratio" in command_args
    assert "9:16" in command_args
    assert "--duration" in command_args
    assert "5" in command_args
    assert captured["api_key"] == "rh-k2"

    reminders = payload.get("reminders")
    assert isinstance(reminders, list)
    assert len(reminders) == 3
    assert payload.get("credential_alias") == "k2"
    assert payload.get("routing_group_id") == "grp_seedance_k2"

    assert len(bridge_calls) == 1
    assert bridge_calls[0].get("task_id") == "task-rhs2-001"
    assert bridge_calls[0].get("credential_alias") == "k2"
    assert bridge_calls[0].get("routing_group_id") == "grp_seedance_k2"

    assert len(refresh_calls) == 1
    assert refresh_calls[0].get("task_id") == "task-rhs2-001"
    assert refresh_calls[0].get("credential_alias") == "k2"
    assert refresh_calls[0].get("routing_group_id") == "grp_seedance_k2"


def test_runninghub_seedance2_submit_failure_keeps_reminders(monkeypatch):
    module = _load_module("test_runninghub_seedance2_submit_failure", RHS2_TOOL_PATH)
    tool = module.Tools()

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "runninghub",
            "credential_alias": "k1",
            "routing_group_id": "grp_seedance_k1",
            "api_key": "rh-k1",
            "source": "key_routing",
        }

    async def _fake_run_au(*, command_args, timeout_seconds=None, api_key_env="", api_key=""):
        raise RuntimeError("mocked submit failure")

    async def _fake_resolve_media_inputs(*, prompt_text, images, videos, audios, chat_id, __request__):
        return {
            "ok": True,
            "images": list(images or []),
            "videos": list(videos or []),
            "audios": list(audios or []),
            "prompt_resources": [],
        }

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_resolve_au_media_inputs", _fake_resolve_media_inputs)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run_au)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_seedance2(
            prompt="测试失败路径",
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "CommandExecutionFailed"
    assert "mocked submit failure" in str(payload["error_message"])
    assert isinstance(payload.get("reminders"), list)
    assert len(payload["reminders"]) == 3


def test_runninghub_seedance2_key_routing_failure_returns_error(monkeypatch):
    module = _load_module("test_runninghub_seedance2_key_routing_failure", RHS2_TOOL_PATH)
    tool = module.Tools()

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": False,
            "status_code": 400,
            "error_code": "KEY_ROUTING_ENV_MISSING",
            "error_message": "Credential env is empty for provider=runninghub, alias=k2, env=RH_API_KEY_K2",
            "request_id": None,
        }

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_seedance2(
            prompt="测试 key routing 失败",
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "KEY_ROUTING_ENV_MISSING"
    assert "Credential env is empty" in str(payload["error_message"])
    assert isinstance(payload.get("reminders"), list)
    assert len(payload["reminders"]) == 3


def test_runninghub_seedance2_media_bridge_resolves_before_submit(monkeypatch):
    module = _load_module("test_runninghub_seedance2_media_bridge_resolves", RHS2_TOOL_PATH)
    tool = module.Tools()

    captured: dict[str, object] = {}
    bridge_calls: list[dict[str, object]] = []

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "runninghub",
            "credential_alias": "k2",
            "routing_group_id": "grp_seedance_k2",
            "api_key": "rh-k2",
            "source": "key_routing",
        }

    async def _fake_resolve_media_inputs(*, prompt_text, images, videos, audios, chat_id, __request__):
        assert images == ["%image_001.png"]
        return {
            "ok": True,
            "images": ["https://media.example.com/image_001.png"],
            "videos": [],
            "audios": [],
            "prompt_resources": [{"name": "image_001.png", "url": "https://media.example.com/image_001.png"}],
        }

    async def _fake_run_au(*, command_args, timeout_seconds=None, api_key_env="", api_key=""):
        captured["command_args"] = list(command_args)
        return {"submit": {"taskId": "task-rhs2-bridge", "status": "PENDING"}}

    async def _fake_bridge_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    def _fake_schedule_refresh(**kwargs):
        return None

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_resolve_au_media_inputs", _fake_resolve_media_inputs)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run_au)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_bridge_upsert)
    monkeypatch.setattr(tool, "_schedule_status_refresh", _fake_schedule_refresh)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_seedance2(
            prompt="人物向前走",
            images=["%image_001.png"],
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    command_args = captured["command_args"]
    assert isinstance(command_args, list)
    assert command_args.count("--image") == 1
    assert "https://media.example.com/image_001.png" in command_args
    assert "%image_001.png" not in command_args
    assert len(bridge_calls) == 1
    assert bridge_calls[0].get("references") == ["https://media.example.com/image_001.png"]


def test_runninghub_seedance2_media_bridge_missing_reference(monkeypatch):
    module = _load_module("test_runninghub_seedance2_media_bridge_missing", RHS2_TOOL_PATH)
    tool = module.Tools()

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "runninghub",
            "credential_alias": "k2",
            "routing_group_id": "grp_seedance_k2",
            "api_key": "rh-k2",
            "source": "key_routing",
        }

    async def _fake_resolve_media_inputs(*, prompt_text, images, videos, audios, chat_id, __request__):
        return {
            "ok": False,
            "status_code": 400,
            "error_code": "MissingMediaAssetReferences",
            "error_message": "Failed to resolve some image references",
            "missing_references": ["%image_001.png"],
            "ambiguous_references": [],
            "available_references": ["folder/image_002.png"],
            "unresolved_inputs": [{"input": "%image_001.png", "reason": "missing_reference"}],
        }

    async def _fake_run_au(*, command_args, timeout_seconds=None, api_key_env="", api_key=""):
        raise AssertionError("_run_au_vendor_json should not be called when media resolution fails")

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_resolve_au_media_inputs", _fake_resolve_media_inputs)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run_au)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_seedance2(
            prompt="人物向前走",
            images=["%image_001.png"],
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "MissingMediaAssetReferences"
    assert payload["missing_references"] == ["%image_001.png"]


def test_runninghub_seedance2_queued_submit_does_not_use_reference_image_as_video(monkeypatch):
    module = _load_module("test_runninghub_seedance2_queued_without_video", RHS2_TOOL_PATH)
    tool = module.Tools()

    bridge_calls: list[dict[str, object]] = []

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "runninghub",
            "credential_alias": "k2",
            "routing_group_id": "grp_seedance_k2",
            "api_key": "rh-k2",
            "source": "key_routing",
        }

    async def _fake_resolve_media_inputs(*, prompt_text, images, videos, audios, chat_id, __request__):
        return {
            "ok": True,
            "images": ["https://example.com/reference/image_001.png"],
            "videos": [],
            "audios": [],
            "prompt_resources": [{"name": "image_001.png", "url": "https://example.com/reference/image_001.png"}],
        }

    async def _fake_run_au(*, command_args, timeout_seconds=None, api_key_env="", api_key=""):
        return {
            "submit": {
                "taskId": "task-rhs2-queued",
                "status": "QUEUED",
                "request": {
                    "images": [
                        "https://example.com/reference/image_001.png",
                    ]
                },
            }
        }

    async def _fake_bridge_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    def _fake_schedule_refresh(**kwargs):
        return None

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_resolve_au_media_inputs", _fake_resolve_media_inputs)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run_au)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_bridge_upsert)
    monkeypatch.setattr(tool, "_schedule_status_refresh", _fake_schedule_refresh)

    raw = asyncio.run(
        tool.generate_video_with_runninghub_seedance2(
            prompt="排队中的任务不应误报 video_url",
            images=["%image_001.png"],
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["status"] == "QUEUED"
    assert payload["video_url"] is None
    assert payload["video_url_markdown"] == "暂无"
    assert payload["output_urls"] == []

    assert len(bridge_calls) == 1
    assert bridge_calls[0].get("video_url") is None


def test_runninghub_seedance2_prompt_reference_auto_maps_to_media_params(monkeypatch):
    module = _load_module("test_runninghub_seedance2_prompt_reference_auto_maps", RHS2_TOOL_PATH)
    tool = module.Tools()

    async def _fake_bridge_resolve_media_inputs(self, *, values, media_type, workdir=""):
        normalized = []
        for value in values or []:
            text = str(value or "").strip()
            if not text:
                continue
            if text.startswith("%"):
                text = text[1:]
            normalized.append(text)

        expected_by_ref = {
            "sample_image.png": "image",
            "sample_video.mp4": "video",
            "sample_audio.wav": "audio",
        }

        resolved_values: list[str] = []
        prompt_resources: list[dict[str, str]] = []
        unresolved_inputs: list[dict[str, str]] = []

        for ref in normalized:
            expected_type = expected_by_ref.get(ref)
            if expected_type is None:
                unresolved_inputs.append({"input": f"%{ref}", "reason": "missing_reference"})
                continue
            if expected_type != media_type:
                unresolved_inputs.append({"input": f"%{ref}", "reason": "missing_reference"})
                continue
            url = f"https://media.example.com/{ref}"
            resolved_values.append(url)
            prompt_resources.append({"name": ref, "url": url})

        if unresolved_inputs:
            return {
                "ok": False,
                "status_code": 400,
                "error_code": "MissingMediaAssetReferences",
                "error_message": f"Failed to resolve some {media_type} references",
                "resolved_values": [],
                "prompt_resources": [],
                "missing_references": [item["input"] for item in unresolved_inputs],
                "ambiguous_references": [],
                "available_references": list(expected_by_ref.keys()),
                "unresolved_inputs": unresolved_inputs,
            }

        return {
            "ok": True,
            "status_code": 200,
            "resolved_values": resolved_values,
            "prompt_resources": prompt_resources,
            "missing_references": [],
            "ambiguous_references": [],
            "available_references": list(expected_by_ref.keys()),
            "unresolved_inputs": [],
        }

    monkeypatch.setattr(
        module.AUMediaReferenceBridge,
        "resolve_media_inputs",
        _fake_bridge_resolve_media_inputs,
    )

    resolved = asyncio.run(
        tool._resolve_au_media_inputs(
            prompt_text="Use %sample_image.png with %sample_video.mp4 and %sample_audio.wav",
            images=[],
            videos=[],
            audios=[],
            chat_id="chat-1",
            __request__=None,
        )
    )

    assert resolved["ok"] is True
    assert resolved["input_images"] == ["%sample_image.png"]
    assert resolved["input_videos"] == ["%sample_video.mp4"]
    assert resolved["input_audios"] == ["%sample_audio.wav"]
    assert resolved["inferred_prompt_inputs"]["images"] == ["%sample_image.png"]
    assert resolved["inferred_prompt_inputs"]["videos"] == ["%sample_video.mp4"]
    assert resolved["inferred_prompt_inputs"]["audios"] == ["%sample_audio.wav"]
    assert resolved["images"] == ["https://media.example.com/sample_image.png"]
    assert resolved["videos"] == ["https://media.example.com/sample_video.mp4"]
    assert resolved["audios"] == ["https://media.example.com/sample_audio.wav"]


def test_runninghub_seedance2_async_refresh_does_not_overwrite_generation_params(monkeypatch):
    module = _load_module("test_runninghub_seedance2_async_refresh_keep_params", RHS2_TOOL_PATH)
    tool = module.Tools()

    upsert_calls: list[dict[str, object]] = []

    async def _fake_query_task_via_au(
        *,
        task_id,
        wait,
        poll_interval_seconds,
        wait_timeout_seconds,
        max_polls,
        api_key_env,
        api_key="",
    ):
        return {
            "final": {
                "taskId": task_id,
                "status": "SUCCESS",
                "results": [
                    {
                        "name": "videoUrl",
                        "url": "https://media.example.com/output.mp4",
                        "outputType": "mp4",
                    }
                ],
            }
        }

    async def _fake_bridge_upsert_task(**kwargs):
        upsert_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(tool, "_query_task_via_au", _fake_query_task_via_au)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_bridge_upsert_task)

    async def _run_refresh() -> None:
        tool._schedule_status_refresh(
            task_id="task-refresh-1",
            model="seedance2-mini",
            chat_id="chat-1",
            prompt_text="remove subtitles from %sample_video.mp4",
            references=["https://media.example.com/sample_video.mp4"],
            prompt_resources=[{"name": "sample_video.mp4", "url": "https://media.example.com/sample_video.mp4"}],
            duration=5,
            ratio="9:16",
            generate_audio=False,
            poll_interval_seconds=5,
            wait_timeout_seconds=30,
            max_polls=3,
            api_key_env="AU_ROUTED_API_KEY",
            api_key="rh-k1",
            credential_alias="k1",
            routing_group_id="grp-k1",
            __request__=None,
        )
        await asyncio.sleep(0.05)

    asyncio.run(_run_refresh())

    assert len(upsert_calls) == 1
    assert upsert_calls[0].get("task_id") == "task-refresh-1"
    assert upsert_calls[0].get("video_url") == "https://media.example.com/output.mp4"
    assert "generation_params" not in upsert_calls[0]


def test_btn_image2_gen_defaults_and_task_bridge(monkeypatch):
    module = _load_module("test_btn_image2_gen_defaults", BTN_TOOL_PATH)
    tool = module.Tools()

    captured: dict[str, object] = {}

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "btn_image2",
            "credential_alias": "k3",
            "routing_group_id": "grp_seedance_k3",
            "api_key": "img-k3",
            "source": "key_routing",
        }

    bridge_calls: list[dict[str, object]] = []

    async def _fake_bridge_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    def _fake_schedule_btn_job(**kwargs):
        captured.update(dict(kwargs))
        return True

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_bridge_upsert)
    monkeypatch.setattr(tool, "_schedule_btn_job", _fake_schedule_btn_job)

    raw = asyncio.run(
        tool.generate_image_with_btn_image2_gen(
            prompt="一张竖屏运动海报",
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["status"] == "QUEUED"
    assert payload["image_url"] is None
    assert payload["output_images"] == 0
    assert payload["saved_image_count"] == 0

    command_args = captured["command_args"]
    assert isinstance(command_args, list)
    assert command_args[0] == "btn-image2-gen"
    assert "--size" in command_args
    assert "1024x1792" in command_args
    assert "--quality" in command_args
    assert "auto" in command_args
    assert "--response-format" not in command_args
    assert "--save-images" in command_args
    assert "--full-json" not in command_args
    assert "--output" not in command_args
    assert captured["api_key"] == "img-k3"
    assert captured["credential_alias"] == "k3"
    assert captured["routing_group_id"] == "grp_seedance_k3"
    assert isinstance(captured.get("output_json_path"), str)
    assert str(captured["output_json_path"]).endswith("/result.json")
    assert captured["output_json_path"] == payload["json_file"]

    assert len(bridge_calls) == 1
    assert bridge_calls[0].get("status") == "QUEUED"
    assert bridge_calls[0].get("credential_alias") == "k3"
    assert bridge_calls[0].get("routing_group_id") == "grp_seedance_k3"
    generation_params = bridge_calls[0].get("generation_params")
    assert isinstance(generation_params, dict)
    assert generation_params.get("json_file") == payload["json_file"]
    assert "response_format" not in generation_params
    assert payload.get("credential_alias") == "k3"
    assert payload.get("routing_group_id") == "grp_seedance_k3"

    assert not hasattr(tool, "list_generation_tasks")
    assert not hasattr(tool, "get_generation_task_status")
    assert not hasattr(tool, "wait_generation_task")


def test_btn_image2_gen_key_routing_failure_returns_error(monkeypatch):
    module = _load_module("test_btn_image2_gen_key_routing_failure", BTN_TOOL_PATH)
    tool = module.Tools()

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": False,
            "status_code": 400,
            "error_code": "KEY_ROUTING_ENV_MISSING",
            "error_message": "Credential env is empty for provider=btn_image2, alias=k3, env=IMAGE_2_API_KEY_K3",
            "request_id": None,
        }

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)

    raw = asyncio.run(
        tool.generate_image_with_btn_image2_gen(
            prompt="测试 key routing 失败",
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "KEY_ROUTING_ENV_MISSING"
    assert "Credential env is empty" in str(payload["error_message"])


def test_btn_image2_edit_supports_multi_image_refs(monkeypatch):
    module = _load_module("test_btn_image2_edit_multi_refs", BTN_TOOL_PATH)
    tool = module.Tools()

    captured: dict[str, object] = {}
    bridge_calls: list[dict[str, object]] = []

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "btn_image2",
            "credential_alias": "k1",
            "routing_group_id": "grp_seedance_k1",
            "api_key": "img-k1",
            "source": "key_routing",
        }

    async def _fake_resolve_au_image_inputs(*, prompt_text, images, image_refs, chat_id, __request__):
        assert "第一张图" in prompt_text
        assert images == ["%image_001.png", "%image_002.png"]
        assert image_refs == ["主体参考", "风格参考"]
        return {
            "ok": True,
            "images": [
                "https://media.example.com/image_001.png",
                "https://media.example.com/image_002.png",
            ],
            "prompt_resources": [
                {"name": "image_001.png", "url": "https://media.example.com/image_001.png"},
                {"name": "image_002.png", "url": "https://media.example.com/image_002.png"},
            ],
            "input_images": ["%image_001.png", "%image_002.png"],
            "image_refs": ["主体参考", "风格参考"],
            "inferred_prompt_inputs": [],
            "inferred_image_ref_inputs": [],
        }

    async def _fake_bridge_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    def _fake_schedule_btn_job(**kwargs):
        captured.update(dict(kwargs))
        return True

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_resolve_au_image_inputs", _fake_resolve_au_image_inputs)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_bridge_upsert)
    monkeypatch.setattr(tool, "_schedule_btn_job", _fake_schedule_btn_job)

    raw = asyncio.run(
        tool.edit_image_with_btn_image2(
            prompt="以第一张图为主体，第二张图为风格",
            images=["%image_001.png", "%image_002.png"],
            image_refs=["主体参考", "风格参考"],
            include_image_order_hint=False,
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["status"] == "QUEUED"
    assert payload["image_url"] is None
    assert payload["output_images"] == 0
    assert payload["saved_image_count"] == 0

    command_args = captured["command_args"]
    assert isinstance(command_args, list)
    assert command_args[0] == "btn-image2-edit"
    assert command_args.count("--image") == 2
    assert command_args.count("--image-ref") == 2
    assert "https://media.example.com/image_001.png" in command_args
    assert "https://media.example.com/image_002.png" in command_args
    assert "%image_001.png" not in command_args
    assert "%image_002.png" not in command_args
    assert "--no-include-image-order-hint" in command_args
    assert "--save-images" in command_args
    assert "--full-json" not in command_args
    assert "--output" not in command_args
    assert captured["api_key"] == "img-k1"
    assert isinstance(captured.get("output_json_path"), str)
    assert str(captured["output_json_path"]).endswith("/result.json")
    assert captured["output_json_path"] == payload["json_file"]
    assert payload.get("credential_alias") == "k1"
    assert payload.get("routing_group_id") == "grp_seedance_k1"

    assert len(bridge_calls) == 1
    assert bridge_calls[0].get("references") == [
        "https://media.example.com/image_001.png",
        "https://media.example.com/image_002.png",
    ]


def test_btn_image2_edit_media_bridge_missing_reference(monkeypatch):
    module = _load_module("test_btn_image2_edit_media_bridge_missing", BTN_TOOL_PATH)
    tool = module.Tools()

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "btn_image2",
            "credential_alias": "k1",
            "routing_group_id": "grp_seedance_k1",
            "api_key": "img-k1",
            "source": "key_routing",
        }

    async def _fake_resolve_au_image_inputs(*, prompt_text, images, image_refs, chat_id, __request__):
        assert "引用缺失" in prompt_text
        assert image_refs == []
        return {
            "ok": False,
            "status_code": 400,
            "error_code": "MissingMediaAssetReferences",
            "error_message": "Failed to resolve some image references",
            "missing_references": ["%image_001.png"],
            "ambiguous_references": [],
            "available_references": ["folder/image_002.png"],
            "unresolved_inputs": [{"input": "%image_001.png", "reason": "missing_reference"}],
        }

    async def _fake_run_au(*, command_args, timeout_seconds=None, api_key_env="", api_key=""):
        raise AssertionError("_run_au_vendor_json should not be called when media resolution fails")

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_resolve_au_image_inputs", _fake_resolve_au_image_inputs)
    monkeypatch.setattr(tool, "_run_au_vendor_json", _fake_run_au)

    raw = asyncio.run(
        tool.edit_image_with_btn_image2(
            prompt="测试图片引用缺失",
            images=["%image_001.png"],
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error_code"] == "MissingMediaAssetReferences"
    assert payload["missing_references"] == ["%image_001.png"]


def test_btn_image2_auto_maps_prompt_and_image_ref_tokens(monkeypatch):
    module = _load_module("test_btn_image2_auto_maps_prompt_and_refs", BTN_TOOL_PATH)
    tool = module.Tools()

    async def _fake_bridge_resolve_media_inputs(self, *, values, media_type, workdir=""):
        assert media_type == "image"
        mapping = {
            "%ref_from_image_ref.png": "https://media.example.com/ref_from_image_ref.png",
            "%ref_from_prompt.png": "https://media.example.com/ref_from_prompt.png",
        }
        resolved_values: list[str] = []
        prompt_resources: list[dict[str, str]] = []
        unresolved_inputs: list[dict[str, str]] = []

        for item in values or []:
            text = str(item or "").strip()
            if text in mapping:
                resolved_values.append(mapping[text])
                prompt_resources.append({"name": text.lstrip("%"), "url": mapping[text]})
            else:
                unresolved_inputs.append({"input": text, "reason": "missing_reference"})

        if unresolved_inputs:
            return {
                "ok": False,
                "status_code": 400,
                "error_code": "MissingMediaAssetReferences",
                "error_message": "Failed to resolve some image references",
                "resolved_values": [],
                "prompt_resources": [],
                "missing_references": [item["input"] for item in unresolved_inputs],
                "ambiguous_references": [],
                "available_references": list(mapping.keys()),
                "unresolved_inputs": unresolved_inputs,
            }

        return {
            "ok": True,
            "status_code": 200,
            "resolved_values": resolved_values,
            "prompt_resources": prompt_resources,
            "missing_references": [],
            "ambiguous_references": [],
            "available_references": list(mapping.keys()),
            "unresolved_inputs": [],
        }

    monkeypatch.setattr(
        module.AUMediaReferenceBridge,
        "resolve_media_inputs",
        _fake_bridge_resolve_media_inputs,
    )

    resolved = asyncio.run(
        tool._resolve_au_image_inputs(
            prompt_text="把 %ref_from_prompt.png 处理成动漫风",
            images=[],
            image_refs=["%ref_from_image_ref.png", "主体参考"],
            chat_id="chat-1",
            __request__=None,
        )
    )

    assert resolved["ok"] is True
    assert resolved["input_images"] == ["%ref_from_image_ref.png", "%ref_from_prompt.png"]
    assert resolved["image_refs"] == ["主体参考"]
    assert resolved["inferred_image_ref_inputs"] == ["%ref_from_image_ref.png"]
    assert resolved["inferred_prompt_inputs"] == ["%ref_from_prompt.png"]
    assert resolved["images"] == [
        "https://media.example.com/ref_from_image_ref.png",
        "https://media.example.com/ref_from_prompt.png",
    ]


def test_btn_image2_edit_accepts_percent_refs_from_image_refs(monkeypatch):
    module = _load_module("test_btn_image2_edit_accepts_percent_image_refs", BTN_TOOL_PATH)
    tool = module.Tools()

    captured: dict[str, object] = {}
    bridge_calls: list[dict[str, object]] = []

    async def _fake_resolve_credential(*, __user__=None, preferred_alias=""):
        return {
            "ok": True,
            "provider": "btn_image2",
            "credential_alias": "k2",
            "routing_group_id": "grp_seedance_k2",
            "api_key": "img-k2",
            "source": "key_routing",
        }

    async def _fake_resolve_au_image_inputs(*, prompt_text, images, image_refs, chat_id, __request__):
        assert images == []
        assert image_refs == ["%测试3.png"]
        assert "%测试3.png" in prompt_text
        return {
            "ok": True,
            "images": ["https://media.example.com/test3.png"],
            "prompt_resources": [{"name": "测试3.png", "url": "https://media.example.com/test3.png"}],
            "input_images": ["%测试3.png"],
            "image_refs": [],
            "inferred_prompt_inputs": [],
            "inferred_image_ref_inputs": ["%测试3.png"],
        }

    async def _fake_bridge_upsert(**kwargs):
        bridge_calls.append(dict(kwargs))
        return True

    def _fake_schedule_btn_job(**kwargs):
        captured.update(dict(kwargs))
        return True

    monkeypatch.setattr(tool, "_resolve_vendor_credential", _fake_resolve_credential)
    monkeypatch.setattr(tool, "_resolve_au_image_inputs", _fake_resolve_au_image_inputs)
    monkeypatch.setattr(tool, "_bridge_upsert_task", _fake_bridge_upsert)
    monkeypatch.setattr(tool, "_schedule_btn_job", _fake_schedule_btn_job)

    raw = asyncio.run(
        tool.edit_image_with_btn_image2(
            prompt="%测试3.png 人物在吃包子",
            image_refs=["%测试3.png"],
            __request__=None,
            __user__={"id": "u1"},
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    command_args = captured["command_args"]
    assert isinstance(command_args, list)
    assert command_args[0] == "btn-image2-edit"
    assert command_args.count("--image") == 1
    assert "https://media.example.com/test3.png" in command_args
    assert "--image-ref" not in command_args
    assert "%测试3.png" not in command_args

    assert len(bridge_calls) == 1
    generation_params = bridge_calls[0].get("generation_params")
    assert isinstance(generation_params, dict)
    assert generation_params.get("input_images") == ["%测试3.png"]
    assert generation_params.get("inferred_image_ref_inputs") == ["%测试3.png"]

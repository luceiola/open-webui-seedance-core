"""
title: RunningHub Seedance 2.5 Tool
author: local-dev
version: 0.1.1
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from pydantic import Field


_TOOL_DIR = Path(__file__).resolve().parent


def _load_hailuo_tool_class():
    try:
        from runninghub_hailuo_h3_tool import Tools as HailuoTools

        return HailuoTools
    except Exception:
        pass

    candidates = [
        _TOOL_DIR / "runninghub_hailuo_h3_tool.py",
        Path.cwd() / "templates" / "runninghub_hailuo_h3_tool.py",
        Path.cwd().parent / "templates" / "runninghub_hailuo_h3_tool.py",
    ]
    source_path = next((path for path in candidates if path.exists() and path.is_file()), None)
    if source_path is None:
        raise ImportError("Unable to locate templates/runninghub_hailuo_h3_tool.py")

    module_name = "_openwebui_runninghub_hailuo_h3_tool_base"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, str(source_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load RunningHub Hailuo tool base: {source_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.Tools


_HailuoTools = _load_hailuo_tool_class()


class Tools(_HailuoTools):
    # Reuse deterministic media routing and the shared task lifecycle without exposing Hailuo generation.
    generate_video_with_runninghub_hailuo_h3 = None

    class Valves(_HailuoTools.Valves):
        DEFAULT_RESOLUTION: str = Field(default="720p")
        DEFAULT_RATIO: str = Field(default="9:16")
        DEFAULT_DURATION_SECONDS: int = Field(default=5)
        DEFAULT_GENERATE_AUDIO: bool = Field(default=True)
        DEFAULT_REAL_PERSON_MODE: bool = Field(default=True)
        DEFAULT_INCLUDE_MODAL_ORDER_HINT: bool = Field(default=True)
        DEFAULT_RETURN_LAST_FRAME: bool = Field(default=False)
        DEFAULT_WEB_SEARCH: bool = Field(default=False)
        DEFAULT_BITRATE_MODE: str = Field(default="standard")
        DEFAULT_SEED: int = Field(default=-1)
        DEFAULT_OUTPUT_FORMAT: str = Field(default="mp4")
        DEFAULT_WAIT_TIMEOUT_SECONDS: int = Field(default=900, ge=10, le=7200)

    _PROVIDER = "runninghub_seedance25"
    _SKILL_NAME = "runninghub-seedance25"
    _TOOL_NAME = "runninghub_seedance25_tool.generate_video_with_runninghub_seedance25"
    _COMMAND = "rh-seedance2.5-video"
    _ALLOWED_RESOLUTIONS = {"480p", "720p", "1080p", "2k", "4k"}
    _ALLOWED_RATIOS = {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}
    _ALLOWED_BITRATE_MODES = {"standard", "high"}
    _ALLOWED_OUTPUT_FORMATS = {"mp4", "mov"}
    _I2V_CONVERSION_SLOTS = {"all", "firstFrameUrl", "lastFrameUrl"}
    _MULTIMODAL_CONVERSION_SLOTS = {
        "all",
        *(f"image{index}" for index in range(1, 31)),
        *(f"video{index}" for index in range(1, 11)),
    }

    def _reminders(self) -> list[str]:
        return [
            "无参考素材时使用文生视频；普通参考素材使用多模态模式。",
            "只有用户明确提出首帧或尾帧时，才使用首尾帧图生视频模式。",
            "默认 720p、5 秒、9:16；多模态包含视频时自动使用自适应比例和自动时长。",
            "默认生成音频并开启真人模式；如需静音或关闭真人模式请明确声明。",
        ]

    def _mode_error(self, message: str, reminders: list[str]) -> str:
        return json.dumps(
            {
                "ok": False,
                "status_code": 400,
                "error_code": "Seedance25ModeConflict",
                "error_message": message,
                "request_id": None,
                "reminders": reminders,
            },
            ensure_ascii=False,
        )

    def _invalid_parameter(self, message: str, reminders: list[str]) -> str:
        return json.dumps(
            {
                "ok": False,
                "status_code": 400,
                "error_code": "InvalidParameter",
                "error_message": message,
                "request_id": None,
                "reminders": reminders,
            },
            ensure_ascii=False,
        )

    def _validate_conversion_slots(self, mode: str, values: list[str]) -> Optional[str]:
        if not values:
            return None
        if mode == "t2v":
            return "conversion_slots are not supported in t2v mode"
        allowed = self._I2V_CONVERSION_SLOTS if mode == "i2v" else self._MULTIMODAL_CONVERSION_SLOTS
        invalid = [value for value in values if value not in allowed]
        if invalid:
            return f"invalid conversion_slots for {mode}: {', '.join(invalid)}"
        return None

    async def generate_video_with_runninghub_seedance25(
        self,
        prompt: str,
        first_frame: str = "",
        last_frame: str = "",
        images: Optional[list[str]] = None,
        videos: Optional[list[str]] = None,
        audios: Optional[list[str]] = None,
        image_refs: Optional[list[str]] = None,
        video_refs: Optional[list[str]] = None,
        audio_refs: Optional[list[str]] = None,
        include_modal_order_hint: Optional[bool] = None,
        resolution: str = "",
        duration: Optional[int] = None,
        ratio: str = "",
        generate_audio: Optional[bool] = None,
        real_person_mode: Optional[bool] = None,
        conversion_slots: Optional[list[str]] = None,
        return_last_frame: Optional[bool] = None,
        web_search: Optional[bool] = None,
        bitrate_mode: str = "",
        seed: Optional[int] = None,
        output_format: str = "",
        webhook_url: str = "",
        chat_id: str = "",
        poll_interval_seconds: Optional[int] = None,
        wait_timeout_seconds: Optional[int] = None,
        max_polls: Optional[int] = None,
        api_key_env: str = "",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        reminders = self._reminders()
        prompt_text = str(prompt or "").strip()
        if not prompt_text:
            return self._invalid_parameter("prompt is required", reminders)
        if len(prompt_text) > 20480:
            return self._invalid_parameter("prompt must not exceed 20480 characters", reminders)

        raw_first_frame = str(first_frame or "").strip()
        raw_last_frame = str(last_frame or "").strip()
        raw_images = self._sanitize_list(images)
        raw_videos = self._sanitize_list(videos)
        raw_audios = self._sanitize_list(audios)
        raw_image_refs = self._sanitize_list(image_refs)
        raw_video_refs = self._sanitize_list(video_refs)
        raw_audio_refs = self._sanitize_list(audio_refs)
        resolved_conversion_slots = list(dict.fromkeys(self._sanitize_list(conversion_slots)))
        has_frame_inputs = bool(raw_first_frame or raw_last_frame)
        has_explicit_multimodal_inputs = bool(raw_images or raw_videos or raw_audios)

        prompt_refs = self._normalized_prompt_refs(prompt_text)
        frame_ref_keys = {
            self._normalize_media_ref_key(value)
            for value in (raw_first_frame, raw_last_frame)
            if self._normalize_media_ref_key(value)
        }
        final_mode, mode_error = self._route_generation_mode(
            has_frame_inputs=has_frame_inputs,
            has_multimodal_inputs=has_explicit_multimodal_inputs,
            prompt_refs=prompt_refs,
            frame_ref_keys=frame_ref_keys,
        )
        if mode_error or final_mode is None:
            return self._mode_error(mode_error or "unable to determine Seedance 2.5 generation mode", reminders)

        if final_mode != "multimodal" and (raw_image_refs or raw_video_refs or raw_audio_refs):
            return self._mode_error("media reference descriptions are only supported in multimodal mode", reminders)

        resolved_resolution = str(resolution or self.valves.DEFAULT_RESOLUTION).strip()
        if resolved_resolution not in self._ALLOWED_RESOLUTIONS:
            return self._invalid_parameter(
                f"resolution must be one of: {', '.join(sorted(self._ALLOWED_RESOLUTIONS))}", reminders
            )

        requested_duration = self.valves.DEFAULT_DURATION_SECONDS if duration is None else int(duration)
        if requested_duration != -1 and not 4 <= requested_duration <= 30:
            return self._invalid_parameter("duration must be -1 or an integer between 4 and 30 seconds", reminders)
        resolved_duration = requested_duration

        requested_ratio = str(ratio or self.valves.DEFAULT_RATIO).strip()
        if requested_ratio not in self._ALLOWED_RATIOS:
            return self._invalid_parameter(
                f"ratio must be one of: {', '.join(sorted(self._ALLOWED_RATIOS))}", reminders
            )
        resolved_ratio = "adaptive" if final_mode == "i2v" else requested_ratio

        resolved_generate_audio = self.valves.DEFAULT_GENERATE_AUDIO if generate_audio is None else bool(generate_audio)
        resolved_real_person_mode = (
            self.valves.DEFAULT_REAL_PERSON_MODE if real_person_mode is None else bool(real_person_mode)
        )
        resolved_include_order_hint = (
            self.valves.DEFAULT_INCLUDE_MODAL_ORDER_HINT
            if include_modal_order_hint is None
            else bool(include_modal_order_hint)
        )
        resolved_return_last_frame = (
            self.valves.DEFAULT_RETURN_LAST_FRAME if return_last_frame is None else bool(return_last_frame)
        )
        resolved_web_search = self.valves.DEFAULT_WEB_SEARCH if web_search is None else bool(web_search)
        if resolved_web_search and final_mode != "t2v":
            return self._invalid_parameter("web_search is only supported in t2v mode", reminders)

        resolved_bitrate_mode = str(bitrate_mode or self.valves.DEFAULT_BITRATE_MODE).strip()
        if resolved_bitrate_mode not in self._ALLOWED_BITRATE_MODES:
            return self._invalid_parameter("bitrate_mode must be one of: high, standard", reminders)
        resolved_seed = self.valves.DEFAULT_SEED if seed is None else int(seed)
        if not -1 <= resolved_seed <= 2147483647:
            return self._invalid_parameter("seed must be between -1 and 2147483647", reminders)
        resolved_output_format = str(output_format or self.valves.DEFAULT_OUTPUT_FORMAT).strip()
        if resolved_output_format not in self._ALLOWED_OUTPUT_FORMATS:
            return self._invalid_parameter("output_format must be one of: mov, mp4", reminders)
        slots_error = self._validate_conversion_slots(final_mode, resolved_conversion_slots)
        if slots_error:
            return self._invalid_parameter(slots_error, reminders)

        credential = await self._resolve_vendor_credential(__user__=__user__)
        if not credential.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": int(credential.get("status_code") or 500),
                    "error_code": credential.get("error_code") or "KEY_ROUTING_RESOLVE_FAILED",
                    "error_message": credential.get("error_message")
                    or credential.get("error")
                    or "Failed to resolve key routing credential",
                    "request_id": credential.get("request_id"),
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

        resolved_api_key = str(credential.get("api_key") or "").strip()
        resolved_credential_alias = str(credential.get("credential_alias") or "").strip() or None
        resolved_routing_group_id = str(credential.get("routing_group_id") or "").strip() or None

        resolved_first_frame: Optional[str] = None
        resolved_last_frame: Optional[str] = None
        resolved_images: list[str] = []
        resolved_videos: list[str] = []
        resolved_audios: list[str] = []
        submitted_input_images: list[str] = []
        submitted_input_videos: list[str] = []
        submitted_input_audios: list[str] = []
        inferred_prompt_inputs: dict[str, list[str]] = {"images": [], "videos": [], "audios": []}
        prompt_resources: list[dict[str, Any]] = []

        if has_frame_inputs:
            frame_slots = [
                (slot, value)
                for slot, value in (("first_frame", raw_first_frame), ("last_frame", raw_last_frame))
                if value
            ]
            frame_bridge = await self._resolve_au_media_inputs(
                prompt_text="",
                images=[value for _, value in frame_slots],
                videos=[],
                audios=[],
                chat_id=str(chat_id or "").strip(),
                __request__=__request__,
            )
            if not frame_bridge.get("ok"):
                return self._media_bridge_error(frame_bridge, reminders)
            resolved_frames = list(frame_bridge.get("images") or [])
            if len(resolved_frames) != len(frame_slots):
                return self._mode_error("failed to resolve all first/last-frame inputs", reminders)
            for (slot, _), value in zip(frame_slots, resolved_frames):
                if slot == "first_frame":
                    resolved_first_frame = value
                else:
                    resolved_last_frame = value
            prompt_resources = list(frame_bridge.get("prompt_resources") or [])
        else:
            media_bridge = await self._resolve_au_media_inputs(
                prompt_text=prompt_text,
                images=raw_images,
                videos=raw_videos,
                audios=raw_audios,
                chat_id=str(chat_id or "").strip(),
                __request__=__request__,
            )
            if not media_bridge.get("ok"):
                return self._media_bridge_error(media_bridge, reminders)
            resolved_images = list(media_bridge.get("images") or [])
            resolved_videos = list(media_bridge.get("videos") or [])
            resolved_audios = list(media_bridge.get("audios") or [])
            submitted_input_images = list(media_bridge.get("input_images") or raw_images)
            submitted_input_videos = list(media_bridge.get("input_videos") or raw_videos)
            submitted_input_audios = list(media_bridge.get("input_audios") or raw_audios)
            prompt_resources = list(media_bridge.get("prompt_resources") or [])
            inferred = media_bridge.get("inferred_prompt_inputs")
            if isinstance(inferred, dict):
                inferred_prompt_inputs = {
                    "images": list(inferred.get("images") or []),
                    "videos": list(inferred.get("videos") or []),
                    "audios": list(inferred.get("audios") or []),
                }
            if final_mode == "multimodal" and not (resolved_images or resolved_videos or resolved_audios):
                return self._mode_error("multimodal mode requires at least one resolved media reference", reminders)

        if len(resolved_images) > 30 or len(resolved_videos) > 10 or len(resolved_audios) > 10:
            return self._invalid_parameter(
                "multimodal inputs support at most 30 images, 10 videos, and 10 audios", reminders
            )

        video_parameter_normalized = False
        if final_mode == "multimodal" and resolved_videos:
            video_parameter_normalized = resolved_ratio != "adaptive" or resolved_duration != -1
            resolved_ratio = "adaptive"
            resolved_duration = -1

        resolved_poll_interval = int(poll_interval_seconds or self.valves.DEFAULT_POLL_INTERVAL_SECONDS)
        resolved_wait_timeout = int(wait_timeout_seconds or self.valves.DEFAULT_WAIT_TIMEOUT_SECONDS)
        resolved_max_polls = int(max_polls or self.valves.DEFAULT_MAX_POLLS)

        args: list[str] = [
            self._COMMAND,
            "--mode",
            final_mode,
            "--prompt",
            prompt_text,
            "--resolution",
            resolved_resolution,
            "--duration",
            str(resolved_duration),
            "--ratio",
            resolved_ratio,
            "--bitrate-mode",
            resolved_bitrate_mode,
            "--seed",
            str(resolved_seed),
            "--output-format",
            resolved_output_format,
        ]
        args.append("--generate-audio" if resolved_generate_audio else "--no-generate-audio")
        args.append("--real-person-mode" if resolved_real_person_mode else "--no-real-person-mode")
        args.append("--include-modal-order-hint" if resolved_include_order_hint else "--no-include-modal-order-hint")
        args.append("--return-last-frame" if resolved_return_last_frame else "--no-return-last-frame")
        args.append("--web-search" if resolved_web_search else "--no-web-search")
        if resolved_first_frame:
            args.extend(["--first-frame", resolved_first_frame])
        if resolved_last_frame:
            args.extend(["--last-frame", resolved_last_frame])
        for value in resolved_images:
            args.extend(["--image", value])
        for value in resolved_videos:
            args.extend(["--video", value])
        for value in resolved_audios:
            args.extend(["--audio", value])
        for value in raw_image_refs:
            args.extend(["--image-ref", value])
        for value in raw_video_refs:
            args.extend(["--video-ref", value])
        for value in raw_audio_refs:
            args.extend(["--audio-ref", value])
        for value in resolved_conversion_slots:
            args.extend(["--conversion-slot", value])
        webhook_text = str(webhook_url or "").strip()
        if webhook_text:
            args.extend(["--webhook-url", webhook_text])
        args.append("--no-wait")
        args.extend(["--poll-interval-seconds", str(max(1, resolved_poll_interval))])
        args.extend(["--wait-timeout-seconds", str(max(1, resolved_wait_timeout))])
        args.extend(["--max-polls", str(max(1, resolved_max_polls))])
        args.append("--no-save-videos")

        try:
            payload = await self._run_au_vendor_json(
                command_args=args,
                timeout_seconds=int(self.valves.SUBPROCESS_TIMEOUT_SECONDS),
                api_key_env=api_key_env,
                api_key=resolved_api_key,
            )
        except Exception as exc:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 500,
                    "error_code": "CommandExecutionFailed",
                    "error_message": str(exc),
                    "request_id": None,
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

        _, submit_status, final_status = self._extract_task_id_and_status(payload)
        task_id = self._extract_hailuo_task_id(payload)
        error_code, error_message = self._extract_hailuo_failure(payload)
        request_id = self._extract_hailuo_request_id(payload)
        if not task_id:
            error_code = error_code or "MissingTaskId"
            error_message = error_message or "au command returned no task_id; the generation task was not created"

        references = [
            value
            for value in (
                resolved_first_frame,
                resolved_last_frame,
                *resolved_images,
                *resolved_videos,
                *resolved_audios,
            )
            if value
        ]
        generation_params = {
            "model": "seedance2.5",
            "command": self._COMMAND,
            "mode": final_mode,
            "resolution": resolved_resolution,
            "requested_duration": requested_duration,
            "requested_ratio": requested_ratio,
            "video_parameter_normalized": video_parameter_normalized,
            "duration": resolved_duration,
            "ratio": resolved_ratio,
            "generate_audio": resolved_generate_audio,
            "real_person_mode": resolved_real_person_mode,
            "include_modal_order_hint": resolved_include_order_hint,
            "return_last_frame": resolved_return_last_frame,
            "web_search": resolved_web_search,
            "bitrate_mode": resolved_bitrate_mode,
            "seed": resolved_seed,
            "output_format": resolved_output_format,
            "conversion_slots": resolved_conversion_slots,
            "first_frame": raw_first_frame or None,
            "last_frame": raw_last_frame or None,
            "input_images": submitted_input_images,
            "input_videos": submitted_input_videos,
            "input_audios": submitted_input_audios,
            "image_refs": raw_image_refs,
            "video_refs": raw_video_refs,
            "audio_refs": raw_audio_refs,
            "inferred_prompt_image_refs": inferred_prompt_inputs["images"],
            "inferred_prompt_video_refs": inferred_prompt_inputs["videos"],
            "inferred_prompt_audio_refs": inferred_prompt_inputs["audios"],
            "wait": False,
        }

        if task_id:
            await self._bridge_upsert_task(
                task_id=task_id,
                status=final_status or submit_status or "PENDING",
                model="seedance2.5",
                chat_id=str(chat_id or "").strip(),
                references=references,
                raw_submit_response=payload.get("submit") if isinstance(payload.get("submit"), dict) else payload,
                raw_last_response=payload.get("final") if isinstance(payload.get("final"), dict) else payload,
                video_url=None,
                error_code=error_code,
                error_message=error_message,
                request_id=request_id,
                prompt_text=prompt_text,
                generation_params=generation_params,
                prompt_resources=prompt_resources,
                duration=resolved_duration,
                ratio=resolved_ratio,
                generate_audio=resolved_generate_audio,
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
                __request__=__request__,
            )
            self._schedule_status_refresh(
                task_id=task_id,
                model="seedance2.5",
                chat_id=str(chat_id or "").strip(),
                prompt_text=prompt_text,
                references=references,
                prompt_resources=prompt_resources,
                duration=resolved_duration,
                ratio=resolved_ratio,
                generate_audio=resolved_generate_audio,
                poll_interval_seconds=resolved_poll_interval,
                wait_timeout_seconds=resolved_wait_timeout,
                max_polls=resolved_max_polls,
                api_key_env=str(api_key_env or "").strip(),
                api_key=resolved_api_key,
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
                __request__=__request__,
            )

        ok = bool(task_id)
        return json.dumps(
            {
                "ok": ok,
                "task_id": task_id,
                "response_id": task_id,
                "status": (final_status or submit_status or "RUNNING") if ok else "FAILED",
                **generation_params,
                "video_url": None,
                "video_url_markdown": "暂无",
                "output_urls": [],
                "error_code": error_code,
                "error_message": error_message,
                "request_id": request_id,
                "credential_alias": resolved_credential_alias,
                "routing_group_id": resolved_routing_group_id,
                "reminders": reminders,
            },
            ensure_ascii=False,
        )

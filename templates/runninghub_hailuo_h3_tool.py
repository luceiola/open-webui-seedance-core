"""
title: RunningHub Hailuo H3 Tool
author: local-dev
version: 0.1.0
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


def _load_seedance2_tool_class():
    try:
        from runninghub_seedance2_tool import Tools as Seedance2Tools

        return Seedance2Tools
    except Exception:
        pass

    candidates = [
        _TOOL_DIR / "runninghub_seedance2_tool.py",
        Path.cwd() / "templates" / "runninghub_seedance2_tool.py",
        Path.cwd().parent / "templates" / "runninghub_seedance2_tool.py",
    ]
    source_path = next((path for path in candidates if path.exists() and path.is_file()), None)
    if source_path is None:
        raise ImportError("Unable to locate templates/runninghub_seedance2_tool.py")

    module_name = "_openwebui_runninghub_seedance2_tool_base"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, str(source_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load RunningHub tool base: {source_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.Tools


_Seedance2Tools = _load_seedance2_tool_class()


class Tools(_Seedance2Tools):
    # Reuse the shared RunningHub task lifecycle without exposing Seedance2 generation.
    generate_video_with_runninghub_seedance2 = None

    class Valves(_Seedance2Tools.Valves):
        DEFAULT_RESOLUTION: str = Field(default="2K")
        DEFAULT_RATIO: str = Field(default="adaptive")
        DEFAULT_DURATION_SECONDS: int = Field(default=5, ge=5, le=15)
        DEFAULT_WAIT_TIMEOUT_SECONDS: int = Field(default=900, ge=10, le=7200)

    _PROVIDER = "runninghub_hailuo_h3"
    _SKILL_NAME = "runninghub-hailuo-h3"
    _TOOL_NAME = "runninghub_hailuo_h3_tool.generate_video_with_runninghub_hailuo_h3"
    _COMMAND = "rh-hailuo-h3-video"
    _ALLOWED_RATIOS = {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}

    def _reminders(self) -> list[str]:
        return [
            "无参考素材时使用文生视频；普通参考素材使用多模态模式。",
            "只有用户明确提出首帧或尾帧时，才使用首尾帧图生视频模式。",
            "Hailuo H3 固定为 2K，时长支持 5-15 秒，未声明时默认 5 秒。",
        ]

    def _mode_error(self, message: str, reminders: list[str]) -> str:
        return json.dumps(
            {
                "ok": False,
                "status_code": 400,
                "error_code": "HailuoH3ModeConflict",
                "error_message": message,
                "request_id": None,
                "reminders": reminders,
            },
            ensure_ascii=False,
        )

    def _normalized_prompt_refs(self, prompt_text: str) -> set[str]:
        from shared.toolkit import extract_media_asset_references

        return {
            self._normalize_media_ref_key(value)
            for value in extract_media_asset_references(prompt_text)
            if self._normalize_media_ref_key(value)
        }

    def _route_generation_mode(
        self,
        *,
        has_frame_inputs: bool,
        has_multimodal_inputs: bool,
        prompt_refs: set[str],
        frame_ref_keys: set[str],
    ) -> tuple[Optional[str], Optional[str]]:
        if has_frame_inputs and has_multimodal_inputs:
            return None, "first_frame/last_frame cannot be combined with multimodal image/video/audio references"
        if has_frame_inputs:
            extra_refs = sorted(prompt_refs - frame_ref_keys)
            if extra_refs:
                return (
                    None,
                    "first/last-frame mode cannot include additional multimodal prompt references: "
                    + ", ".join(f"%{value}" for value in extra_refs),
                )
            return "i2v", None
        if has_multimodal_inputs or prompt_refs:
            return "multimodal", None
        return "t2v", None

    def _extract_hailuo_task_id(self, payload: dict[str, Any]) -> Optional[str]:
        task_id, _, _ = self._extract_task_id_and_status(payload)
        if task_id:
            return task_id
        nodes = [
            payload,
            payload.get("submit") if isinstance(payload.get("submit"), dict) else {},
            payload.get("final") if isinstance(payload.get("final"), dict) else {},
            payload.get("query") if isinstance(payload.get("query"), dict) else {},
        ]
        for node in nodes:
            value = str(node.get("response_id") or node.get("responseId") or "").strip()
            if value:
                return value
        return None

    def _extract_hailuo_failure(self, payload: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        error_code, error_message = self._extract_failure(payload)
        if error_code or error_message:
            return error_code, error_message
        nodes = [
            payload.get("final") if isinstance(payload.get("final"), dict) else {},
            payload.get("query") if isinstance(payload.get("query"), dict) else {},
            payload.get("submit") if isinstance(payload.get("submit"), dict) else {},
            payload,
        ]
        for node in nodes:
            code = str(node.get("errorCode") or "").strip() or None
            message = str(node.get("errorMessage") or node.get("failedReason") or "").strip() or None
            if code or message:
                return code, message
        return None, None

    def _extract_hailuo_request_id(self, payload: dict[str, Any]) -> Optional[str]:
        nodes = [
            payload.get("final") if isinstance(payload.get("final"), dict) else {},
            payload.get("query") if isinstance(payload.get("query"), dict) else {},
            payload.get("submit") if isinstance(payload.get("submit"), dict) else {},
            payload,
        ]
        for node in nodes:
            value = str(node.get("request_id") or node.get("requestId") or "").strip()
            if value:
                return value
        return None

    async def generate_video_with_runninghub_hailuo_h3(
        self,
        prompt: str,
        first_frame: str = "",
        last_frame: str = "",
        images: Optional[list[str]] = None,
        videos: Optional[list[str]] = None,
        audios: Optional[list[str]] = None,
        resolution: str = "",
        duration: Optional[int] = None,
        ratio: str = "",
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
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "prompt is required",
                    "request_id": None,
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

        resolved_resolution = str(resolution or self.valves.DEFAULT_RESOLUTION).strip()
        if resolved_resolution.upper() != "2K":
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "resolution must be 2K for Hailuo H3",
                    "request_id": None,
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )
        resolved_resolution = "2K"

        resolved_duration = self.valves.DEFAULT_DURATION_SECONDS if duration is None else int(duration)
        if not 5 <= resolved_duration <= 15:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "duration must be an integer between 5 and 15 seconds",
                    "request_id": None,
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

        resolved_ratio = str(ratio or self.valves.DEFAULT_RATIO).strip() or "adaptive"
        if resolved_ratio not in self._ALLOWED_RATIOS:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": f"ratio must be one of: {', '.join(sorted(self._ALLOWED_RATIOS))}",
                    "request_id": None,
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

        raw_first_frame = str(first_frame or "").strip()
        raw_last_frame = str(last_frame or "").strip()
        raw_images = self._sanitize_list(images)
        raw_videos = self._sanitize_list(videos)
        raw_audios = self._sanitize_list(audios)
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
            return self._mode_error(mode_error or "unable to determine Hailuo H3 generation mode", reminders)

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

        if len(resolved_images) > 9 or len(resolved_videos) > 3 or len(resolved_audios) > 3:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "multimodal inputs support at most 9 images, 3 videos, and 3 audios",
                    "request_id": None,
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

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
        ]
        if final_mode != "i2v":
            args.extend(["--ratio", resolved_ratio])
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
        task_ratio: Optional[str] = None if final_mode == "i2v" else resolved_ratio

        if task_id:
            await self._bridge_upsert_task(
                task_id=task_id,
                status=final_status or submit_status or "PENDING",
                model="hailuo-h3",
                chat_id=str(chat_id or "").strip(),
                references=references,
                raw_submit_response=payload.get("submit") if isinstance(payload.get("submit"), dict) else payload,
                raw_last_response=payload.get("final") if isinstance(payload.get("final"), dict) else payload,
                video_url=None,
                error_code=error_code,
                error_message=error_message,
                request_id=request_id,
                prompt_text=prompt_text,
                generation_params={
                    "model": "hailuo-h3",
                    "command": self._COMMAND,
                    "mode": final_mode,
                    "resolution": resolved_resolution,
                    "duration": resolved_duration,
                    "ratio": task_ratio,
                    "first_frame": raw_first_frame or None,
                    "last_frame": raw_last_frame or None,
                    "input_images": submitted_input_images,
                    "input_videos": submitted_input_videos,
                    "input_audios": submitted_input_audios,
                    "inferred_prompt_image_refs": inferred_prompt_inputs["images"],
                    "inferred_prompt_video_refs": inferred_prompt_inputs["videos"],
                    "inferred_prompt_audio_refs": inferred_prompt_inputs["audios"],
                    "wait": False,
                },
                prompt_resources=prompt_resources,
                duration=resolved_duration,
                ratio=task_ratio,
                generate_audio=None,
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
                __request__=__request__,
            )
            self._schedule_status_refresh(
                task_id=task_id,
                model="hailuo-h3",
                chat_id=str(chat_id or "").strip(),
                prompt_text=prompt_text,
                references=references,
                prompt_resources=prompt_resources,
                duration=resolved_duration,
                ratio=task_ratio or "",
                generate_audio=None,
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
                "model": "hailuo-h3",
                "command": self._COMMAND,
                "mode": final_mode,
                "resolution": resolved_resolution,
                "duration": resolved_duration,
                "ratio": task_ratio,
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

    def _media_bridge_error(self, result: dict[str, Any], reminders: list[str]) -> str:
        return json.dumps(
            {
                "ok": False,
                "status_code": int(result.get("status_code") or 400),
                "error_code": result.get("error_code") or "MissingMediaAssetReferences",
                "error_message": result.get("error_message") or "Failed to resolve media references",
                "missing_references": result.get("missing_references") or [],
                "ambiguous_references": result.get("ambiguous_references") or [],
                "available_references": result.get("available_references") or [],
                "unresolved_inputs": result.get("unresolved_inputs") or [],
                "request_id": None,
                "reminders": reminders,
            },
            ensure_ascii=False,
        )

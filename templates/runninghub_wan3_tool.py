"""
title: RunningHub WAN 3 Video Tool
author: local-dev
version: 0.1.0
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import importlib.util
import json
import os
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

    module_name = "_openwebui_runninghub_seedance2_tool_base_for_wan3"
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
    # Keep the shared task/query lifecycle, but expose only WAN3 generation.
    generate_video_with_runninghub_seedance2 = None

    class Valves(_Seedance2Tools.Valves):
        AU_BIN: str = Field(
            default=os.getenv("AU_BIN", "/Users/lucas/Documents/ai-utility/.venv/bin/au")
        )
        AU_WORKDIR: str = Field(
            default=os.getenv("AU_WORKDIR", "/Users/lucas/Documents/ai-utility")
        )
        DEFAULT_RESOLUTION: str = Field(default="720P")
        DEFAULT_RATIO: str = Field(default="adaptive")
        DEFAULT_DURATION: str = Field(default="5")
        DEFAULT_GENERATE_AUDIO: bool = Field(default=True)
        DEFAULT_WAIT: bool = Field(default=False)
        DEFAULT_WAIT_TIMEOUT_SECONDS: int = Field(default=900, ge=10, le=7200)

    _PROVIDER = "runninghub_wan3"
    _SKILL_NAME = "runninghub-wan3-execution-skill"
    _TOOL_NAME = "runninghub_wan3_tool.generate_video_with_runninghub_wan3"
    _COMMAND = "rh-wan3-video"
    _ALLOWED_RESOLUTIONS = {"480P", "720P", "1080P"}
    _ALLOWED_RATIOS = {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"}

    def _reminders(self) -> list[str]:
        return [
            "无参考素材时使用 WAN3 reference-to-video 纯文生视频。",
            "只有明确提出首帧或尾帧时才使用 image-to-video。",
            "默认 720P、5 秒、adaptive 比例并生成音频。",
        ]

    def _error(self, code: str, message: str, reminders: list[str], *, status_code: int = 400) -> str:
        return json.dumps(
            {
                "ok": False,
                "status_code": status_code,
                "error_code": code,
                "error_message": message,
                "request_id": None,
                "reminders": reminders,
            },
            ensure_ascii=False,
        )

    def _mode_error(self, message: str, reminders: list[str]) -> str:
        return self._error("Wan3ModeConflict", message, reminders)

    def _wan3_task_id(self, payload: dict[str, Any]) -> Optional[str]:
        nodes = [
            payload,
            payload.get("submit") if isinstance(payload.get("submit"), dict) else {},
            payload.get("final") if isinstance(payload.get("final"), dict) else {},
            payload.get("query") if isinstance(payload.get("query"), dict) else {},
        ]
        for node in nodes:
            for key in ("taskId", "task_id", "responseId", "response_id"):
                value = str(node.get(key) or "").strip()
                if value:
                    return value
        return None

    def _wan3_status(self, payload: dict[str, Any]) -> Optional[str]:
        _, submit_status, final_status = self._extract_task_id_and_status(payload)
        return final_status or submit_status

    def _wan3_failure(self, payload: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        nodes = [
            payload.get("final") if isinstance(payload.get("final"), dict) else {},
            payload.get("query") if isinstance(payload.get("query"), dict) else {},
            payload.get("submit") if isinstance(payload.get("submit"), dict) else {},
            payload,
        ]
        for node in nodes:
            code = str(node.get("error_code") or node.get("errorCode") or node.get("code") or "").strip() or None
            message = str(
                node.get("error_message")
                or node.get("errorMessage")
                or node.get("failed_reason")
                or node.get("failedReason")
                or node.get("message")
                or ""
            ).strip() or None
            request_id = str(node.get("request_id") or node.get("requestId") or "").strip() or None
            if code or message or request_id:
                return code, message, request_id
        return None, None, None

    def _validate_inputs(
        self,
        *,
        resolution: str,
        ratio: str,
        duration: str,
        images: list[str],
        videos: list[str],
        audios: list[str],
        file_url: str,
        link_url: str,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        resolved_resolution = str(resolution or self.valves.DEFAULT_RESOLUTION).strip().upper()
        if resolved_resolution not in self._ALLOWED_RESOLUTIONS:
            return None, None, f"resolution must be one of: {', '.join(sorted(self._ALLOWED_RESOLUTIONS))}"
        resolved_ratio = str(ratio or self.valves.DEFAULT_RATIO).strip()
        if resolved_ratio not in self._ALLOWED_RATIOS:
            return None, None, f"ratio must be one of: {', '.join(sorted(self._ALLOWED_RATIOS))}"
        resolved_duration = str(duration or self.valves.DEFAULT_DURATION).strip().lower()
        if resolved_duration != "auto" and not resolved_duration.isdigit():
            return None, None, "duration must be auto or an integer between 2 and 30 seconds"
        if resolved_duration != "auto" and not 2 <= int(resolved_duration) <= 30:
            return None, None, "duration must be auto or an integer between 2 and 30 seconds"
        if len(images) > 10 or len(videos) > 5 or len(audios) > 5:
            return None, None, "WAN3 supports at most 10 images, 5 videos, and 5 audios"
        if file_url and link_url:
            return None, None, "file_url and link_url are mutually exclusive"
        return resolved_resolution, resolved_ratio, resolved_duration

    async def generate_video_with_runninghub_wan3(
        self,
        prompt: str = "",
        first_frame: str = "",
        last_frame: str = "",
        images: Optional[list[str]] = None,
        videos: Optional[list[str]] = None,
        audios: Optional[list[str]] = None,
        file_url: str = "",
        link_url: str = "",
        resolution: str = "",
        ratio: str = "",
        duration: str = "",
        generate_audio: Optional[bool] = None,
        seed: Optional[int] = None,
        webhook_url: str = "",
        chat_id: str = "",
        wait: Optional[bool] = None,
        poll_interval_seconds: Optional[int] = None,
        wait_timeout_seconds: Optional[int] = None,
        max_polls: Optional[int] = None,
        api_key_env: str = "",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        reminders = self._reminders()
        prompt_text = str(prompt or "").strip()
        raw_first = str(first_frame or "").strip()
        raw_last = str(last_frame or "").strip()
        raw_images = self._sanitize_list(images)
        raw_videos = self._sanitize_list(videos)
        raw_audios = self._sanitize_list(audios)
        raw_file_url = str(file_url or "").strip()
        raw_link_url = str(link_url or "").strip()
        resolved_resolution, resolved_ratio, resolved_duration = self._validate_inputs(
            resolution=resolution,
            ratio=ratio,
            duration=duration,
            images=raw_images,
            videos=raw_videos,
            audios=raw_audios,
            file_url=raw_file_url,
            link_url=raw_link_url,
        )
        if resolved_resolution is None:
            return self._error("InvalidParameter", resolved_duration or "invalid parameter", reminders)

        has_frames = bool(raw_first or raw_last)
        if has_frames and (raw_images or raw_videos or raw_audios or raw_file_url or raw_link_url):
            return self._mode_error("first/last-frame mode cannot be combined with reference inputs", reminders)
        mode = "image-to-video" if has_frames else "reference-to-video"
        if mode == "image-to-video" and not raw_first:
            return self._mode_error("image-to-video mode requires first_frame", reminders)
        if mode == "reference-to-video" and len(prompt_text) > 2000:
            return self._error("InvalidParameter", "prompt too long, expected <=2000 chars", reminders)

        credential = await self._resolve_vendor_credential(__user__=__user__)
        if not credential.get("ok"):
            return self._error(
                credential.get("error_code") or "KEY_ROUTING_RESOLVE_FAILED",
                credential.get("error_message") or credential.get("error") or "Failed to resolve credential",
                reminders,
                status_code=int(credential.get("status_code") or 500),
            )
        api_key = str(credential.get("api_key") or "").strip()
        alias = str(credential.get("credential_alias") or "").strip() or None
        group_id = str(credential.get("routing_group_id") or "").strip() or None

        resolved_first = resolved_last = None
        resolved_images: list[str] = []
        resolved_videos: list[str] = []
        resolved_audios: list[str] = []
        prompt_resources: list[dict[str, Any]] = []
        submitted_images = list(raw_images)
        submitted_videos = list(raw_videos)
        submitted_audios = list(raw_audios)
        if has_frames:
            bridge = await self._resolve_au_media_inputs(
                prompt_text="",
                images=[value for value in (raw_first, raw_last) if value],
                videos=[],
                audios=[],
                chat_id=str(chat_id or "").strip(),
                __request__=__request__,
            )
            if not bridge.get("ok") or len(list(bridge.get("images") or [])) != (1 + bool(raw_last)):
                return self._error("MediaReferenceResolveFailed", "failed to resolve first/last-frame inputs", reminders)
            frame_urls = list(bridge.get("images") or [])
            resolved_first = frame_urls[0]
            resolved_last = frame_urls[1] if raw_last else None
            prompt_resources = list(bridge.get("prompt_resources") or [])
        else:
            bridge = await self._resolve_au_media_inputs(
                prompt_text=prompt_text,
                images=raw_images,
                videos=raw_videos,
                audios=raw_audios,
                chat_id=str(chat_id or "").strip(),
                __request__=__request__,
            )
            if not bridge.get("ok"):
                return self._error(
                    bridge.get("error_code") or "MediaReferenceResolveFailed",
                    bridge.get("error_message") or "failed to resolve media references",
                    reminders,
                    status_code=int(bridge.get("status_code") or 400),
                )
            resolved_images = list(bridge.get("images") or [])
            resolved_videos = list(bridge.get("videos") or [])
            resolved_audios = list(bridge.get("audios") or [])
            submitted_images = list(bridge.get("input_images") or submitted_images)
            submitted_videos = list(bridge.get("input_videos") or submitted_videos)
            submitted_audios = list(bridge.get("input_audios") or submitted_audios)
            prompt_resources = list(bridge.get("prompt_resources") or [])

        resolved_audio = self.valves.DEFAULT_GENERATE_AUDIO if generate_audio is None else bool(generate_audio)
        args = [
            self._COMMAND,
            "--mode", mode,
            "--prompt", prompt_text,
            "--resolution", resolved_resolution,
            "--ratio", resolved_ratio,
            "--duration", resolved_duration,
            "--audio-output" if resolved_audio else "--no-audio-output",
        ]
        if resolved_first:
            args.extend(["--first-frame", resolved_first])
        if resolved_last:
            args.extend(["--last-frame", resolved_last])
        for value in resolved_images:
            args.extend(["--image", value])
        for value in resolved_videos:
            args.extend(["--video", value])
        for value in resolved_audios:
            args.extend(["--audio", value])
        if raw_file_url:
            args.extend(["--file-url", raw_file_url])
        if raw_link_url:
            args.extend(["--link-url", raw_link_url])
        if seed is not None:
            args.extend(["--seed", str(seed)])
        if str(webhook_url or "").strip():
            args.extend(["--webhook-url", str(webhook_url).strip()])
        args.extend(["--no-wait", "--poll-interval-seconds", str(max(1, int(poll_interval_seconds or self.valves.DEFAULT_POLL_INTERVAL_SECONDS)),), "--wait-timeout-seconds", str(max(1, int(wait_timeout_seconds or self.valves.DEFAULT_WAIT_TIMEOUT_SECONDS))), "--max-polls", str(max(1, int(max_polls or self.valves.DEFAULT_MAX_POLLS))), "--no-save-videos"])

        try:
            payload = await self._run_au_vendor_json(
                command_args=args,
                timeout_seconds=int(self.valves.SUBPROCESS_TIMEOUT_SECONDS),
                api_key_env=api_key_env,
                api_key=api_key,
            )
        except Exception as exc:
            return self._error("CommandExecutionFailed", str(exc), reminders, status_code=500)

        task_id = self._wan3_task_id(payload)
        status = self._wan3_status(payload) or "PENDING"
        error_code, error_message, request_id = self._wan3_failure(payload)
        if not task_id:
            return self._error("MissingTaskId", "au command returned no task_id; the generation task was not created", reminders)

        references = [value for value in (resolved_first, resolved_last, *resolved_images, *resolved_videos, *resolved_audios) if value]
        bridge_duration = int(resolved_duration) if resolved_duration.isdigit() else 0
        generation_params = {
            "model": "wan3",
            "command": self._COMMAND,
            "mode": mode,
            "resolution": resolved_resolution,
            "ratio": resolved_ratio,
            "duration": resolved_duration,
            "generate_audio": resolved_audio,
            "first_frame": raw_first or None,
            "last_frame": raw_last or None,
            "input_images": submitted_images,
            "input_videos": submitted_videos,
            "input_audios": submitted_audios,
            "file_url": raw_file_url or None,
            "link_url": raw_link_url or None,
            "wait": False,
        }
        await self._bridge_upsert_task(
            task_id=task_id,
            status=status,
            model="wan3",
            chat_id=str(chat_id or "").strip(),
            references=references,
            raw_submit_response=payload.get("submit") if isinstance(payload.get("submit"), dict) else payload,
            raw_last_response=payload.get("final") if isinstance(payload.get("final"), dict) else payload,
            video_url=None,
            request_id=request_id,
            error_code=error_code,
            error_message=error_message,
            prompt_text=prompt_text,
            generation_params=generation_params,
            prompt_resources=prompt_resources,
            duration=bridge_duration,
            ratio=resolved_ratio,
            generate_audio=resolved_audio,
            credential_alias=alias,
            routing_group_id=group_id,
            __request__=__request__,
        )
        self._schedule_status_refresh(
            task_id=task_id,
            model="wan3",
            chat_id=str(chat_id or "").strip(),
            prompt_text=prompt_text,
            references=references,
            prompt_resources=prompt_resources,
            duration=bridge_duration,
            ratio=resolved_ratio,
            generate_audio=resolved_audio,
            poll_interval_seconds=max(1, int(poll_interval_seconds or self.valves.DEFAULT_POLL_INTERVAL_SECONDS)),
            wait_timeout_seconds=max(1, int(wait_timeout_seconds or self.valves.DEFAULT_WAIT_TIMEOUT_SECONDS)),
            max_polls=max(1, int(max_polls or self.valves.DEFAULT_MAX_POLLS)),
            api_key_env=str(api_key_env or "").strip(),
            api_key=api_key,
            credential_alias=alias,
            routing_group_id=group_id,
            __request__=__request__,
        )
        return json.dumps(
            {
                "ok": True,
                "task_id": task_id,
                "response_id": task_id,
                "status": status,
                "model": "wan3",
                "command": self._COMMAND,
                "mode": mode,
                "resolution": resolved_resolution,
                "ratio": resolved_ratio,
                "duration": resolved_duration,
                "generate_audio": resolved_audio,
                "video_url": None,
                "video_url_markdown": "暂无",
                "output_urls": [],
                "raw_response": payload,
                "error_code": error_code,
                "error_message": error_message,
                "request_id": request_id,
                "credential_alias": alias,
                "routing_group_id": group_id,
                "reminders": reminders,
            },
            ensure_ascii=False,
        )

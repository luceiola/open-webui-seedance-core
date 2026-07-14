"""
title: RunningHub Seedance2 Tool
author: local-dev
version: 0.1.0
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlencode, urlparse

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.append(str(_TOOL_DIR))

def _ensure_shared_toolkit_loaded(*, force_reload: bool = False) -> None:
    import types

    toolkit_mod = sys.modules.get("shared.toolkit")
    has_basics = bool(
        toolkit_mod
        and hasattr(toolkit_mod, "bridge_upsert")
        and hasattr(toolkit_mod, "build_auth_headers")
        and hasattr(toolkit_mod, "build_base_url")
        and hasattr(toolkit_mod, "request_openwebui_json")
    )
    if has_basics and not force_reload:
        return

    candidate_paths = [
        _TOOL_DIR / "shared" / "toolkit.py",
        Path.cwd() / "templates" / "shared" / "toolkit.py",
        Path.cwd().parent / "templates" / "shared" / "toolkit.py",
    ]
    toolkit_path = next((p for p in candidate_paths if p.exists() and p.is_file()), None)
    if toolkit_path is None:
        return

    shared_pkg = sys.modules.get("shared")
    if shared_pkg is None:
        shared_pkg = types.ModuleType("shared")
        shared_pkg.__path__ = []
        sys.modules["shared"] = shared_pkg

    if toolkit_mod is None:
        toolkit_mod = types.ModuleType("shared.toolkit")
        sys.modules["shared.toolkit"] = toolkit_mod

    toolkit_mod.__dict__["__file__"] = str(toolkit_path)
    exec(toolkit_path.read_text(encoding="utf-8"), toolkit_mod.__dict__)


_ensure_shared_toolkit_loaded()

from shared.toolkit import (
    bridge_upsert,
    build_auth_headers,
    build_base_url,
    extract_media_asset_references,
    request_openwebui_json,
)

try:
    from shared.toolkit import AUMediaReferenceBridge
except Exception:
    _ensure_shared_toolkit_loaded(force_reload=True)
    from shared.toolkit import AUMediaReferenceBridge


class Tools:
    class Valves(BaseModel):
        OPENWEBUI_BASE_URL: str = Field(
            default="http://127.0.0.1:8080",
            description="OpenWebUI service base URL fallback when request context is unavailable.",
        )
        OPENWEBUI_API_KEY: str = Field(
            default="",
            description="Optional OpenWebUI API key fallback when request context has no auth.",
        )
        AU_BIN: str = Field(
            default="/Users/lucas/Documents/ai-utility/.venv/bin/au",
            description="Absolute path to au executable.",
        )
        AU_WORKDIR: str = Field(
            default="/Users/lucas/Documents/ai-utility",
            description="Working directory for au vendor commands.",
        )
        AU_API_KEY_ENV: str = Field(
            default="RUNNINGHUB_API_KEY",
            description="Default API key env name passed to au vendor commands.",
        )
        KEY_ROUTING_PROVIDER: str = Field(
            default="runninghub",
            description="Provider name in key_routing.json.",
        )
        KEY_ROUTING_PREFERRED_ALIAS: str = Field(
            default="",
            description="Optional preferred key alias for key routing resolution.",
        )
        ROUTED_AU_API_KEY_ENV: str = Field(
            default="AU_ROUTED_API_KEY",
            description="Temporary env variable name used to inject resolved key into au subprocess.",
        )
        REQUEST_TIMEOUT_SECONDS: int = Field(default=180, ge=30, le=1800)
        MEDIA_URL_EXPIRES_IN_SECONDS: int = Field(default=3600, ge=60, le=604800)
        SUBPROCESS_TIMEOUT_SECONDS: int = Field(default=900, ge=30, le=7200)
        DEFAULT_MODEL: str = Field(default="mini")
        DEFAULT_RESOLUTION: str = Field(default="720p")
        DEFAULT_RATIO: str = Field(default="9:16")
        DEFAULT_DURATION_SECONDS: int = Field(default=5, ge=4, le=15)
        DEFAULT_GENERATE_AUDIO: bool = Field(default=True)
        DEFAULT_REAL_PERSON_MODE: bool = Field(default=True)
        DEFAULT_WAIT: bool = Field(default=False)
        DEFAULT_POLL_INTERVAL_SECONDS: int = Field(default=30, ge=1, le=300)
        DEFAULT_WAIT_TIMEOUT_SECONDS: int = Field(default=900, ge=10, le=7200)
        DEFAULT_MAX_POLLS: int = Field(default=120, ge=1, le=2000)

    _PROVIDER = "runninghub_seedance2"
    _SKILL_NAME = "runninghub-seedance2"
    _TOOL_NAME = "runninghub_seedance2_tool.generate_video_with_runninghub_seedance2"

    def __init__(self) -> None:
        self.valves = self.Valves()
        self._active_refresh_tasks: dict[str, asyncio.Task] = {}

    def _base_url(self, __request__: Optional[Request]) -> str:
        return build_base_url(__request__, self.valves.OPENWEBUI_BASE_URL)

    def _headers(self, __request__: Optional[Request]) -> dict[str, str]:
        return build_auth_headers(
            __request__,
            self.valves.OPENWEBUI_API_KEY,
            include_content_type=True,
        )

    def _user_id(self, __user__: Optional[dict]) -> str:
        if __user__ and __user__.get("id"):
            return str(__user__.get("id"))
        return "anonymous"

    async def _request(
        self,
        method: str,
        path: str,
        __request__: Optional[Request],
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return await request_openwebui_json(
            method=method,
            path=path,
            __request__=__request__,
            timeout_seconds=self.valves.REQUEST_TIMEOUT_SECONDS,
            openwebui_base_url=self.valves.OPENWEBUI_BASE_URL,
            openwebui_api_key=self.valves.OPENWEBUI_API_KEY,
            body=body,
        )

    def _normalize_http_exception(self, exc: HTTPException) -> dict[str, Any]:
        status_code = int(exc.status_code or 500)
        detail = exc.detail
        raw = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)

        error_code: Optional[str] = None
        error_message: Optional[str] = None
        request_id: Optional[str] = None
        if isinstance(detail, dict):
            error_code = str(detail.get("code") or "").strip() or None
            error_message = str(detail.get("message") or detail.get("error") or "").strip() or None
            request_id = str(detail.get("request_id") or "").strip() or None
        elif isinstance(detail, str):
            error_message = detail

        return {
            "ok": False,
            "status_code": status_code,
            "error": raw,
            "error_code": error_code,
            "error_message": error_message or raw,
            "request_id": request_id,
        }

    async def _resolve_vendor_credential(
        self,
        *,
        __user__: Optional[dict],
        preferred_alias: str = "",
    ) -> dict[str, Any]:
        provider = str(self.valves.KEY_ROUTING_PROVIDER or "runninghub").strip().lower() or "runninghub"
        preferred_alias_value = (preferred_alias or "").strip() or str(self.valves.KEY_ROUTING_PREFERRED_ALIAS or "").strip()
        user_id = str((__user__ or {}).get("id") or "").strip()
        if not user_id:
            return {
                "ok": False,
                "status_code": 400,
                "error": "Missing __user__.id for key routing",
                "error_code": "KEY_ROUTING_RESOLVE_FAILED",
                "error_message": "Missing __user__.id for key routing",
                "request_id": None,
            }

        try:
            from open_webui.routers.material_packages import _resolve_provider_credential
        except Exception as exc:
            return {
                "ok": False,
                "status_code": 500,
                "error": str(exc),
                "error_code": "KEY_ROUTING_RESOLVE_FAILED",
                "error_message": f"Failed to import key routing resolver: {exc}",
                "request_id": None,
            }

        try:
            resolved = await _resolve_provider_credential(
                provider=provider,
                user_id=user_id,
                preferred_alias=preferred_alias_value or None,
            )
        except HTTPException as exc:
            return self._normalize_http_exception(exc)
        except Exception as exc:
            return {
                "ok": False,
                "status_code": 500,
                "error": str(exc),
                "error_code": "KEY_ROUTING_RESOLVE_FAILED",
                "error_message": f"Failed to resolve key routing credential: {exc}",
                "request_id": None,
            }

        api_key = str((resolved or {}).get("api_key") or "").strip()
        if not api_key:
            return {
                "ok": False,
                "status_code": 400,
                "error": "Resolved api_key is empty",
                "error_code": "KEY_ROUTING_ENV_MISSING",
                "error_message": "Resolved api_key is empty",
                "request_id": None,
            }

        return {
            "ok": True,
            "provider": str((resolved or {}).get("provider") or provider),
            "credential_alias": str((resolved or {}).get("credential_alias") or ""),
            "routing_group_id": (resolved or {}).get("routing_group_id"),
            "api_key": api_key,
            "source": "key_routing",
        }

    async def _bridge_upsert_task(
        self,
        *,
        task_id: str,
        status: str = "",
        model: str = "",
        chat_id: str = "",
        references: Optional[list[str]] = None,
        raw_submit_response: Optional[dict[str, Any]] = None,
        raw_last_response: Optional[dict[str, Any]] = None,
        video_url: Optional[str] = None,
        request_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        prompt_text: Optional[str] = None,
        generation_params: Optional[dict[str, Any]] = None,
        prompt_resources: Optional[list[dict[str, Any]]] = None,
        duration: Optional[int] = None,
        ratio: Optional[str] = None,
        generate_audio: Optional[bool] = None,
        credential_alias: Optional[str] = None,
        routing_group_id: Optional[str] = None,
        __request__: Optional[Request] = None,
    ) -> bool:
        tid = (task_id or "").strip()
        if not tid:
            return False

        payload: dict[str, Any] = {
            "task_id": tid,
            "provider": self._PROVIDER,
            "provider_task_id": tid,
            "tool_name": self._TOOL_NAME,
            "skill_name": self._SKILL_NAME,
            "status": (status or "").strip() or "PENDING",
            "artifact_kind": "video",
        }
        if model:
            payload["model"] = model
        if chat_id:
            payload["chat_id"] = chat_id
        if references:
            payload["references"] = references
        if raw_submit_response is not None:
            payload["raw_submit_response"] = raw_submit_response
        if raw_last_response is not None:
            payload["raw_last_response"] = raw_last_response
        if video_url:
            payload["video_url"] = video_url
        if request_id:
            payload["request_id"] = request_id
        if error_code:
            payload["error_code"] = error_code
        if error_message:
            payload["error_message"] = error_message
        if prompt_text is not None:
            payload["prompt_text"] = prompt_text
        if generation_params is not None:
            payload["generation_params"] = generation_params
        if prompt_resources is not None:
            payload["prompt_resources"] = prompt_resources
        if duration is not None:
            payload["duration"] = int(duration)
        if ratio is not None:
            payload["ratio"] = ratio
        if generate_audio is not None:
            payload["generate_audio"] = bool(generate_audio)
        if credential_alias is not None:
            payload["credential_alias"] = str(credential_alias).strip() or None
        if routing_group_id is not None:
            payload["routing_group_id"] = str(routing_group_id).strip() or None

        return await bridge_upsert(
            requester=self._request,
            payload=payload,
            __request__=__request__,
        )

    def _reminders(self) -> list[str]:
        return [
            "请显式声明所需模型（standard/mini/fast），未声明时默认 mini。",
            "请显式声明时长（秒），未声明时默认 5 秒。",
            "如涉及音频版权风险，请声明不生成音频（--no-generate-audio）。",
        ]

    def _sanitize_list(self, values: Optional[list[str]]) -> list[str]:
        out: list[str] = []
        for value in values or []:
            text = str(value or "").strip()
            if text:
                out.append(text)
        return out

    def _normalize_media_ref_key(self, value: str) -> str:
        text = str(value or "").strip().strip("\"'")
        if not text:
            return ""
        if text.startswith("%"):
            text = text[1:]
        return text.strip()

    def _normalize_model(self, value: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        raw = str(value or "").strip().lower()
        if not raw:
            raw = self.valves.DEFAULT_MODEL

        aliases = {
            "standard": "standard",
            "std": "standard",
            "normal": "standard",
            "rh-seedance2-video": "standard",
            "mini": "mini",
            "rh-seedance2-mini-video": "mini",
            "fast": "fast",
            "rh-seedance2-fast-video": "fast",
        }
        normalized = aliases.get(raw)
        if not normalized:
            return None, None, "model must be one of: standard, mini, fast"

        cmd = {
            "standard": "rh-seedance2-video",
            "mini": "rh-seedance2-mini-video",
            "fast": "rh-seedance2-fast-video",
        }[normalized]
        return normalized, cmd, None

    def _is_http_url(self, value: Any) -> bool:
        text = str(value or "").strip()
        return text.startswith(("http://", "https://"))

    def _is_likely_video_url(self, value: Any) -> bool:
        if not self._is_http_url(value):
            return False
        url = str(value or "").strip()
        path = unquote(urlparse(url).path or "").lower()
        video_exts = (
            ".mp4",
            ".mov",
            ".webm",
            ".m3u8",
            ".m4v",
            ".mkv",
            ".avi",
            ".flv",
            ".ts",
            ".mpeg",
            ".mpg",
        )
        return any(ext in path for ext in video_exts)

    def _extract_video_urls(self, payload: Any, *, max_items: int = 12) -> list[str]:
        results: list[str] = []

        explicit_video_keys = {
            "video",
            "video_url",
            "video_download_url",
            "video_preview_url",
            "video_play_url",
            "video_result_url",
            "output_video_url",
            "result_video_url",
        }
        generic_url_keys = {"url", "download_url", "preview_url", "play_url", "output_url", "result_url", "src", "source"}

        def add_url(value: str) -> None:
            text = str(value or "").strip()
            if not text or text in results:
                return
            results.append(text)

        def walk(node: Any, key_path: tuple[str, ...] = ()) -> None:
            if len(results) >= max_items:
                return

            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, key_path + (str(key or "").strip().lower(),))
                return

            if isinstance(node, list):
                for item in node:
                    walk(item, key_path)
                return

            if not isinstance(node, str):
                return
            if not self._is_http_url(node):
                return

            leaf_key = key_path[-1] if key_path else ""
            parent_keys = key_path[:-1]
            parent_has_video = any("video" in part for part in parent_keys)

            if leaf_key in explicit_video_keys or "video" in leaf_key:
                add_url(node)
                return

            if leaf_key in generic_url_keys:
                if parent_has_video or self._is_likely_video_url(node):
                    add_url(node)
                return

            if self._is_likely_video_url(node):
                add_url(node)

        walk(payload)
        return results

    def _extract_failure(self, payload: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        candidates: list[dict[str, Any]] = []
        for key in ("final", "query", "submit"):
            value = payload.get(key)
            if isinstance(value, dict):
                candidates.append(value)

        for node in candidates:
            for key in ("error_code", "code"):
                value = str(node.get(key) or "").strip()
                if value:
                    err_code = value
                    err_msg = str(node.get("error_message") or node.get("failed_reason") or node.get("message") or "").strip() or None
                    return err_code, err_msg

        for node in candidates:
            err_msg = str(node.get("error_message") or node.get("failed_reason") or "").strip()
            if err_msg:
                return None, err_msg

        return None, None

    def _extract_task_id_and_status(self, payload: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        submit = payload.get("submit") if isinstance(payload.get("submit"), dict) else {}
        final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
        query = payload.get("query") if isinstance(payload.get("query"), dict) else {}

        task_id = str(
            submit.get("taskId")
            or final.get("taskId")
            or query.get("taskId")
            or payload.get("task_id")
            or ""
        ).strip() or None
        submit_status = str(submit.get("status") or payload.get("submit_status") or "").strip() or None
        final_status = str(final.get("status") or query.get("status") or payload.get("final_status") or submit_status or "").strip() or None
        return task_id, submit_status, final_status

    async def _run_au_vendor_json(
        self,
        *,
        command_args: list[str],
        timeout_seconds: Optional[int] = None,
        api_key_env: str = "",
        api_key: str = "",
    ) -> dict[str, Any]:
        au_bin = str(self.valves.AU_BIN or "").strip()
        if not au_bin:
            raise RuntimeError("AU_BIN is empty")

        workdir = Path(str(self.valves.AU_WORKDIR or "")).expanduser().resolve()
        if not workdir.exists() or not workdir.is_dir():
            raise RuntimeError(f"AU_WORKDIR does not exist or is not a directory: {workdir}")

        resolved_env = str(api_key_env or self.valves.AU_API_KEY_ENV).strip()
        process_env = os.environ.copy()
        resolved_api_key = str(api_key or "").strip()
        if resolved_api_key:
            routed_env_name = str(self.valves.ROUTED_AU_API_KEY_ENV or "").strip()
            if not routed_env_name:
                raise RuntimeError("ROUTED_AU_API_KEY_ENV is empty")
            process_env[routed_env_name] = resolved_api_key
            resolved_env = routed_env_name

        if not resolved_env:
            raise RuntimeError("api key env is required")

        argv: list[str] = [au_bin, "vendor", *command_args]
        argv.extend(["--api-key-env", resolved_env])
        argv.extend(["--full-json", "--quiet"])

        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                argv,
                cwd=str(workdir),
                env=process_env,
                capture_output=True,
                text=True,
                timeout=int(timeout_seconds or self.valves.SUBPROCESS_TIMEOUT_SECONDS),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"au command timed out after {exc.timeout}s") from exc
        except FileNotFoundError as exc:
            raise RuntimeError(f"au executable not found: {au_bin}") from exc

        stdout = str(completed.stdout or "").strip()
        stderr = str(completed.stderr or "").strip()
        if completed.returncode != 0:
            detail = stderr or stdout or f"exit code {completed.returncode}"
            raise RuntimeError(detail)

        if not stdout:
            raise RuntimeError("au command returned empty stdout")

        try:
            return json.loads(stdout)
        except Exception:
            pass

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                continue

        raise RuntimeError("failed to parse JSON from au command output")

    def _build_prompt_resources(
        self,
        *,
        images: list[str],
        videos: list[str],
        audios: list[str],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for idx, value in enumerate(images, start=1):
            out.append({"name": f"image_{idx}", "source": value})
        for idx, value in enumerate(videos, start=1):
            out.append({"name": f"video_{idx}", "source": value})
        for idx, value in enumerate(audios, start=1):
            out.append({"name": f"audio_{idx}", "source": value})
        return out

    async def _resolve_au_media_inputs(
        self,
        *,
        prompt_text: str,
        images: list[str],
        videos: list[str],
        audios: list[str],
        chat_id: str,
        __request__: Optional[Request],
    ) -> dict[str, Any]:
        bridge = AUMediaReferenceBridge(
            __request__=__request__,
            request_timeout_seconds=self.valves.REQUEST_TIMEOUT_SECONDS,
            openwebui_base_url=self.valves.OPENWEBUI_BASE_URL,
            openwebui_api_key=self.valves.OPENWEBUI_API_KEY,
            chat_id=str(chat_id or "").strip(),
            status="active",
            url_expires_in=self.valves.MEDIA_URL_EXPIRES_IN_SECONDS,
        )

        input_images = list(images or [])
        input_videos = list(videos or [])
        input_audios = list(audios or [])

        inferred_prompt_inputs: dict[str, list[str]] = {
            "images": [],
            "videos": [],
            "audios": [],
        }
        prompt_refs = extract_media_asset_references(prompt_text or "")
        existing_ref_keys: set[str] = set()
        for item in (*input_images, *input_videos, *input_audios):
            key = self._normalize_media_ref_key(str(item or ""))
            if key:
                existing_ref_keys.add(key)

        unresolved_prompt_refs: list[str] = []
        ambiguous_prompt_refs: list[dict[str, Any]] = []

        for raw_ref in prompt_refs:
            ref_key = self._normalize_media_ref_key(raw_ref)
            if not ref_key:
                continue
            if ref_key in existing_ref_keys:
                continue

            probe_value = f"%{ref_key}"
            matched_types: list[str] = []
            for media_type in ("image", "video", "audio"):
                probe_result = await bridge.resolve_media_inputs(
                    values=[probe_value],
                    media_type=media_type,
                    workdir=self.valves.AU_WORKDIR,
                )
                if probe_result.get("ok") and list(probe_result.get("resolved_values") or []):
                    matched_types.append(media_type)

            if len(matched_types) == 1:
                matched_type = matched_types[0]
                if matched_type == "image":
                    input_images.append(probe_value)
                    inferred_prompt_inputs["images"].append(probe_value)
                elif matched_type == "video":
                    input_videos.append(probe_value)
                    inferred_prompt_inputs["videos"].append(probe_value)
                else:
                    input_audios.append(probe_value)
                    inferred_prompt_inputs["audios"].append(probe_value)
                existing_ref_keys.add(ref_key)
            elif len(matched_types) > 1:
                ambiguous_prompt_refs.append(
                    {
                        "reference": probe_value,
                        "matched_media_types": matched_types,
                    }
                )
            else:
                unresolved_prompt_refs.append(probe_value)

        if unresolved_prompt_refs or ambiguous_prompt_refs:
            return {
                "ok": False,
                "status_code": 400,
                "error_code": "PromptMediaReferenceTypeResolveFailed",
                "error_message": "Failed to map prompt media references into image/video/audio parameters",
                "resolved_values": [],
                "prompt_resources": [],
                "missing_references": unresolved_prompt_refs,
                "ambiguous_references": ambiguous_prompt_refs,
                "available_references": [],
                "unresolved_inputs": (
                    [{"input": item, "reason": "missing_reference"} for item in unresolved_prompt_refs]
                    + [{"input": str(item.get("reference") or ""), "reason": "ambiguous_media_type"} for item in ambiguous_prompt_refs]
                ),
            }

        image_result = await bridge.resolve_media_inputs(
            values=input_images,
            media_type="image",
            workdir=self.valves.AU_WORKDIR,
        )
        if not image_result.get("ok"):
            return image_result

        video_result = await bridge.resolve_media_inputs(
            values=input_videos,
            media_type="video",
            workdir=self.valves.AU_WORKDIR,
        )
        if not video_result.get("ok"):
            return video_result

        audio_result = await bridge.resolve_media_inputs(
            values=input_audios,
            media_type="audio",
            workdir=self.valves.AU_WORKDIR,
        )
        if not audio_result.get("ok"):
            return audio_result

        prompt_resources: list[dict[str, Any]] = []
        for block in (
            image_result.get("prompt_resources") or [],
            video_result.get("prompt_resources") or [],
            audio_result.get("prompt_resources") or [],
        ):
            for item in block:
                if isinstance(item, dict) and str(item.get("url") or "").startswith(("http://", "https://")):
                    prompt_resources.append(item)

        if not prompt_resources:
            prompt_resources = self._build_prompt_resources(
                images=list(image_result.get("resolved_values") or []),
                videos=list(video_result.get("resolved_values") or []),
                audios=list(audio_result.get("resolved_values") or []),
            )

        return {
            "ok": True,
            "images": list(image_result.get("resolved_values") or []),
            "videos": list(video_result.get("resolved_values") or []),
            "audios": list(audio_result.get("resolved_values") or []),
            "prompt_resources": prompt_resources,
            "input_images": input_images,
            "input_videos": input_videos,
            "input_audios": input_audios,
            "inferred_prompt_inputs": inferred_prompt_inputs,
        }

    async def _query_task_via_au(
        self,
        *,
        task_id: str,
        wait: bool,
        poll_interval_seconds: int,
        wait_timeout_seconds: int,
        max_polls: int,
        api_key_env: str,
        api_key: str = "",
    ) -> dict[str, Any]:
        args = [
            "rh-query-task",
            "--task-id",
            task_id,
            "--poll-interval-seconds",
            str(max(1, int(poll_interval_seconds))),
            "--wait-timeout-seconds",
            str(max(1, int(wait_timeout_seconds))),
            "--max-polls",
            str(max(1, int(max_polls))),
        ]
        args.append("--wait" if wait else "--no-wait")
        timeout = max(60, int(wait_timeout_seconds) + 120 if wait else 120)
        return await self._run_au_vendor_json(
            command_args=args,
            timeout_seconds=timeout,
            api_key_env=api_key_env,
            api_key=api_key,
        )

    def _schedule_status_refresh(
        self,
        *,
        task_id: str,
        model: str,
        chat_id: str,
        prompt_text: str,
        references: list[str],
        prompt_resources: list[dict[str, Any]],
        duration: int,
        ratio: str,
        generate_audio: bool,
        poll_interval_seconds: int,
        wait_timeout_seconds: int,
        max_polls: int,
        api_key_env: str,
        api_key: str,
        credential_alias: Optional[str],
        routing_group_id: Optional[str],
        __request__: Optional[Request],
    ) -> None:
        existing = self._active_refresh_tasks.get(task_id)
        if existing and not existing.done():
            return

        async def runner() -> None:
            try:
                payload = await self._query_task_via_au(
                    task_id=task_id,
                    wait=True,
                    poll_interval_seconds=poll_interval_seconds,
                    wait_timeout_seconds=wait_timeout_seconds,
                    max_polls=max_polls,
                    api_key_env=api_key_env,
                    api_key=api_key,
                )
                _, _, final_status = self._extract_task_id_and_status(payload)
                urls = self._extract_video_urls(payload)
                video_url = urls[0] if urls else None
                error_code, error_message = self._extract_failure(payload)
                await self._bridge_upsert_task(
                    task_id=task_id,
                    status=final_status or "RUNNING",
                    model=model,
                    chat_id=chat_id,
                    references=references,
                    raw_last_response=payload,
                    video_url=video_url,
                    error_code=error_code,
                    error_message=error_message,
                    prompt_text=prompt_text,
                    prompt_resources=prompt_resources,
                    duration=duration,
                    ratio=ratio,
                    generate_audio=generate_audio,
                    credential_alias=credential_alias,
                    routing_group_id=routing_group_id,
                    __request__=__request__,
                )
            except Exception as exc:
                await self._bridge_upsert_task(
                    task_id=task_id,
                    status="RUNNING",
                    model=model,
                    chat_id=chat_id,
                    references=references,
                    error_code="RefreshTaskFailed",
                    error_message=str(exc),
                    prompt_text=prompt_text,
                    prompt_resources=prompt_resources,
                    duration=duration,
                    ratio=ratio,
                    generate_audio=generate_audio,
                    credential_alias=credential_alias,
                    routing_group_id=routing_group_id,
                    __request__=__request__,
                )
            finally:
                self._active_refresh_tasks.pop(task_id, None)

        task = asyncio.create_task(runner(), name=f"rh-seedance2-refresh:{task_id}")
        self._active_refresh_tasks[task_id] = task

    async def generate_video_with_runninghub_seedance2(
        self,
        prompt: str,
        model: str = "",
        images: Optional[list[str]] = None,
        videos: Optional[list[str]] = None,
        audios: Optional[list[str]] = None,
        image_refs: Optional[list[str]] = None,
        video_refs: Optional[list[str]] = None,
        audio_refs: Optional[list[str]] = None,
        resolution: str = "",
        duration: Optional[int] = None,
        ratio: str = "",
        generate_audio: Optional[bool] = None,
        real_person_mode: Optional[bool] = None,
        conversion_slots: Optional[list[str]] = None,
        return_last_frame: Optional[bool] = None,
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

        normalized_model, subcommand, model_error = self._normalize_model(model)
        if model_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": model_error,
                    "request_id": None,
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

        credential = await self._resolve_vendor_credential(__user__=__user__)
        if not credential.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": int(credential.get("status_code") or 500),
                    "error_code": credential.get("error_code") or "KEY_ROUTING_RESOLVE_FAILED",
                    "error_message": credential.get("error_message") or credential.get("error") or "Failed to resolve key routing credential",
                    "request_id": credential.get("request_id"),
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

        resolved_api_key = str(credential.get("api_key") or "").strip()
        resolved_credential_alias = str(credential.get("credential_alias") or "").strip() or None
        resolved_routing_group_id = str(credential.get("routing_group_id") or "").strip() or None

        resolved_images = self._sanitize_list(images)
        resolved_videos = self._sanitize_list(videos)
        resolved_audios = self._sanitize_list(audios)
        resolved_image_refs = self._sanitize_list(image_refs)
        resolved_video_refs = self._sanitize_list(video_refs)
        resolved_audio_refs = self._sanitize_list(audio_refs)
        resolved_conversion_slots = self._sanitize_list(conversion_slots)

        media_bridge_result = await self._resolve_au_media_inputs(
            prompt_text=prompt_text,
            images=resolved_images,
            videos=resolved_videos,
            audios=resolved_audios,
            chat_id=str(chat_id or "").strip(),
            __request__=__request__,
        )
        if not media_bridge_result.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": int(media_bridge_result.get("status_code") or 400),
                    "error_code": media_bridge_result.get("error_code") or "MissingMediaAssetReferences",
                    "error_message": media_bridge_result.get("error_message") or "Failed to resolve media references for au vendor command",
                    "missing_references": media_bridge_result.get("missing_references") or [],
                    "ambiguous_references": media_bridge_result.get("ambiguous_references") or [],
                    "available_references": media_bridge_result.get("available_references") or [],
                    "unresolved_inputs": media_bridge_result.get("unresolved_inputs") or [],
                    "request_id": None,
                    "reminders": reminders,
                },
                ensure_ascii=False,
            )

        resolved_images = list(media_bridge_result.get("images") or [])
        resolved_videos = list(media_bridge_result.get("videos") or [])
        resolved_audios = list(media_bridge_result.get("audios") or [])
        resolved_prompt_resources = list(media_bridge_result.get("prompt_resources") or [])
        submitted_input_images = list(media_bridge_result.get("input_images") or [])
        submitted_input_videos = list(media_bridge_result.get("input_videos") or [])
        submitted_input_audios = list(media_bridge_result.get("input_audios") or [])
        inferred_prompt_inputs = media_bridge_result.get("inferred_prompt_inputs")
        if not isinstance(inferred_prompt_inputs, dict):
            inferred_prompt_inputs = {}

        resolved_resolution = str(resolution or self.valves.DEFAULT_RESOLUTION).strip() or self.valves.DEFAULT_RESOLUTION
        resolved_duration = int(duration or self.valves.DEFAULT_DURATION_SECONDS)
        resolved_ratio = str(ratio or self.valves.DEFAULT_RATIO).strip() or self.valves.DEFAULT_RATIO
        resolved_generate_audio = self.valves.DEFAULT_GENERATE_AUDIO if generate_audio is None else bool(generate_audio)
        resolved_real_person_mode = self.valves.DEFAULT_REAL_PERSON_MODE if real_person_mode is None else bool(real_person_mode)
        resolved_return_last_frame = False if return_last_frame is None else bool(return_last_frame)
        resolved_seed = -1 if seed is None else int(seed)
        resolved_wait = self.valves.DEFAULT_WAIT if wait is None else bool(wait)
        resolved_poll_interval = int(poll_interval_seconds or self.valves.DEFAULT_POLL_INTERVAL_SECONDS)
        resolved_wait_timeout = int(wait_timeout_seconds or self.valves.DEFAULT_WAIT_TIMEOUT_SECONDS)
        resolved_max_polls = int(max_polls or self.valves.DEFAULT_MAX_POLLS)

        args: list[str] = [
            subcommand or "rh-seedance2-mini-video",
            "--prompt",
            prompt_text,
            "--resolution",
            resolved_resolution,
            "--duration",
            str(resolved_duration),
            "--ratio",
            resolved_ratio,
            "--seed",
            str(resolved_seed),
        ]

        args.append("--generate-audio" if resolved_generate_audio else "--no-generate-audio")
        args.append("--real-person-mode" if resolved_real_person_mode else "--no-real-person-mode")
        args.append("--return-last-frame" if resolved_return_last_frame else "--no-return-last-frame")
        args.append("--wait" if resolved_wait else "--no-wait")
        args.extend(["--poll-interval-seconds", str(max(1, resolved_poll_interval))])
        args.extend(["--wait-timeout-seconds", str(max(1, resolved_wait_timeout))])
        args.extend(["--max-polls", str(max(1, resolved_max_polls))])

        webhook_text = str(webhook_url or "").strip()
        if webhook_text:
            args.extend(["--webhook-url", webhook_text])

        for item in resolved_images:
            args.extend(["--image", item])
        for item in resolved_videos:
            args.extend(["--video", item])
        for item in resolved_audios:
            args.extend(["--audio", item])
        for item in resolved_image_refs:
            args.extend(["--image-ref", item])
        for item in resolved_video_refs:
            args.extend(["--video-ref", item])
        for item in resolved_audio_refs:
            args.extend(["--audio-ref", item])
        for item in resolved_conversion_slots:
            args.extend(["--conversion-slot", item])

        try:
            payload = await self._run_au_vendor_json(
                command_args=args,
                timeout_seconds=max(int(self.valves.SUBPROCESS_TIMEOUT_SECONDS), resolved_wait_timeout + 180),
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

        task_id, submit_status, final_status = self._extract_task_id_and_status(payload)
        output_urls = self._extract_video_urls(payload)
        video_url = output_urls[0] if output_urls else None
        error_code, error_message = self._extract_failure(payload)

        references: list[str] = []
        references.extend(resolved_images)
        references.extend(resolved_videos)
        references.extend(resolved_audios)
        prompt_resources = resolved_prompt_resources or self._build_prompt_resources(
            images=resolved_images,
            videos=resolved_videos,
            audios=resolved_audios,
        )

        if task_id:
            await self._bridge_upsert_task(
                task_id=task_id,
                status=final_status or submit_status or "PENDING",
                model=f"seedance2-{normalized_model}",
                chat_id=str(chat_id or "").strip(),
                references=references,
                raw_submit_response=payload.get("submit") if isinstance(payload.get("submit"), dict) else payload,
                raw_last_response=payload.get("final") if isinstance(payload.get("final"), dict) else payload,
                video_url=video_url,
                error_code=error_code,
                error_message=error_message,
                prompt_text=prompt_text,
                generation_params={
                    "model": normalized_model,
                    "command": subcommand,
                    "resolution": resolved_resolution,
                    "duration": resolved_duration,
                    "ratio": resolved_ratio,
                    "generate_audio": resolved_generate_audio,
                    "real_person_mode": resolved_real_person_mode,
                    "return_last_frame": resolved_return_last_frame,
                    "wait": resolved_wait,
                    "input_images": submitted_input_images,
                    "input_videos": submitted_input_videos,
                    "input_audios": submitted_input_audios,
                    "inferred_prompt_image_refs": list(inferred_prompt_inputs.get("images") or []),
                    "inferred_prompt_video_refs": list(inferred_prompt_inputs.get("videos") or []),
                    "inferred_prompt_audio_refs": list(inferred_prompt_inputs.get("audios") or []),
                },
                prompt_resources=prompt_resources,
                duration=resolved_duration,
                ratio=resolved_ratio,
                generate_audio=resolved_generate_audio,
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
                __request__=__request__,
            )

            if not resolved_wait:
                self._schedule_status_refresh(
                    task_id=task_id,
                    model=f"seedance2-{normalized_model}",
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
        status_text = final_status or submit_status or ("RUNNING" if not resolved_wait else "FAILED")

        return json.dumps(
            {
                "ok": ok,
                "task_id": task_id,
                "response_id": task_id,
                "status": status_text,
                "model": normalized_model,
                "command": subcommand,
                "resolution": resolved_resolution,
                "duration": resolved_duration,
                "ratio": resolved_ratio,
                "generate_audio": resolved_generate_audio,
                "video_url": video_url,
                "video_url_markdown": f"[查看视频]({video_url})" if video_url else "暂无",
                "output_urls": output_urls,
                "raw_response": payload,
                "error_code": error_code,
                "error_message": error_message,
                "request_id": None,
                "credential_alias": resolved_credential_alias,
                "routing_group_id": resolved_routing_group_id,
                "reminders": reminders,
            },
            ensure_ascii=False,
        )

    async def list_generation_tasks(
        self,
        status: str = "",
        chat_id: str = "",
        limit: int = 50,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        limit = max(1, min(int(limit or 50), 200))
        query: dict[str, Any] = {
            "provider": self._PROVIDER,
            "limit": limit,
            "offset": 0,
            "refresh_status": "true",
        }

        desired_status = str(status or "").strip().upper()
        if desired_status:
            query["status"] = desired_status

        path = f"/api/v1/tasks?{urlencode(query)}"
        result = await self._request("GET", path, __request__)
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        desired_chat = str(chat_id or "").strip()

        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if desired_chat and str(item.get("chat_id") or "").strip() != desired_chat:
                continue
            row = {
                "task_id": item.get("id"),
                "status": item.get("status"),
                "model": item.get("model"),
                "chat_id": item.get("chat_id"),
                "video_url": item.get("video_preview_url") or item.get("video_download_url"),
                "video_url_markdown": (
                    f"[查看视频]({item.get('video_preview_url') or item.get('video_download_url')})"
                    if (item.get("video_preview_url") or item.get("video_download_url"))
                    else "暂无"
                ),
                "error_code": item.get("error_code"),
                "error_message": item.get("error_message"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            }
            rows.append(row)

        return json.dumps({"ok": True, "tasks": rows[:limit], "count": len(rows[:limit])}, ensure_ascii=False)

    async def get_generation_task_status(
        self,
        task_id: str,
        poll_provider: bool = True,
        api_key_env: str = "",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        tid = str(task_id or "").strip()
        if not tid:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "task_id is required",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        resolved_api_key = ""
        resolved_credential_alias: Optional[str] = None
        resolved_routing_group_id: Optional[str] = None

        if poll_provider:
            credential = await self._resolve_vendor_credential(__user__=__user__)
            if not credential.get("ok"):
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": int(credential.get("status_code") or 500),
                        "task_id": tid,
                        "error_code": credential.get("error_code") or "KEY_ROUTING_RESOLVE_FAILED",
                        "error_message": credential.get("error_message") or credential.get("error") or "Failed to resolve key routing credential",
                        "request_id": credential.get("request_id"),
                    },
                    ensure_ascii=False,
                )

            resolved_api_key = str(credential.get("api_key") or "").strip()
            resolved_credential_alias = str(credential.get("credential_alias") or "").strip() or None
            resolved_routing_group_id = str(credential.get("routing_group_id") or "").strip() or None
            try:
                payload = await self._query_task_via_au(
                    task_id=tid,
                    wait=False,
                    poll_interval_seconds=self.valves.DEFAULT_POLL_INTERVAL_SECONDS,
                    wait_timeout_seconds=self.valves.DEFAULT_WAIT_TIMEOUT_SECONDS,
                    max_polls=self.valves.DEFAULT_MAX_POLLS,
                    api_key_env=api_key_env,
                    api_key=resolved_api_key,
                )
                _, _, final_status = self._extract_task_id_and_status(payload)
                urls = self._extract_video_urls(payload)
                await self._bridge_upsert_task(
                    task_id=tid,
                    status=final_status or "RUNNING",
                    raw_last_response=payload,
                    video_url=(urls[0] if urls else None),
                    credential_alias=resolved_credential_alias,
                    routing_group_id=resolved_routing_group_id,
                    __request__=__request__,
                )
            except Exception:
                pass

        result = await self._request("GET", f"/api/v1/tasks/{tid}?refresh_status=true", __request__)
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        task = data.get("task") if isinstance(data.get("task"), dict) else {}
        artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), list) else []

        video_url = task.get("video_preview_url") or task.get("video_download_url")
        if not video_url:
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                if str(artifact.get("artifact_type") or "").strip().lower() != "video":
                    continue
                candidate = task.get("video_download_url") or task.get("video_preview_url")
                if candidate:
                    video_url = candidate
                    break

        payload = {
            "ok": True,
            "task_id": task.get("id") or tid,
            "response_id": task.get("id") or tid,
            "status": task.get("status"),
            "model": task.get("model"),
            "video_url": video_url,
            "video_url_markdown": f"[查看视频]({video_url})" if video_url else "暂无",
            "error_code": task.get("error_code"),
            "error_message": task.get("error_message"),
            "request_id": task.get("request_id"),
            "credential_alias": task.get("credential_alias") or resolved_credential_alias,
            "routing_group_id": task.get("routing_group_id") or resolved_routing_group_id,
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "raw_response": data,
        }
        return json.dumps(payload, ensure_ascii=False)

    async def wait_generation_task(
        self,
        task_id: str,
        poll_interval_seconds: Optional[int] = None,
        wait_timeout_seconds: Optional[int] = None,
        max_polls: Optional[int] = None,
        api_key_env: str = "",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        tid = str(task_id or "").strip()
        if not tid:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "task_id is required",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        poll = int(poll_interval_seconds or self.valves.DEFAULT_POLL_INTERVAL_SECONDS)
        timeout = int(wait_timeout_seconds or self.valves.DEFAULT_WAIT_TIMEOUT_SECONDS)
        polls = int(max_polls or self.valves.DEFAULT_MAX_POLLS)

        credential = await self._resolve_vendor_credential(__user__=__user__)
        if not credential.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": int(credential.get("status_code") or 500),
                    "task_id": tid,
                    "error_code": credential.get("error_code") or "KEY_ROUTING_RESOLVE_FAILED",
                    "error_message": credential.get("error_message") or credential.get("error") or "Failed to resolve key routing credential",
                    "request_id": credential.get("request_id"),
                },
                ensure_ascii=False,
            )

        resolved_api_key = str(credential.get("api_key") or "").strip()
        resolved_credential_alias = str(credential.get("credential_alias") or "").strip() or None
        resolved_routing_group_id = str(credential.get("routing_group_id") or "").strip() or None

        start = time.monotonic()
        try:
            payload = await self._query_task_via_au(
                task_id=tid,
                wait=True,
                poll_interval_seconds=poll,
                wait_timeout_seconds=timeout,
                max_polls=polls,
                api_key_env=api_key_env,
                api_key=resolved_api_key,
            )
        except Exception as exc:
            await self._bridge_upsert_task(
                task_id=tid,
                status="RUNNING",
                error_code="GenerationTaskPollingFailed",
                error_message=str(exc),
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
                __request__=__request__,
            )
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 500,
                    "task_id": tid,
                    "error_code": "GenerationTaskPollingFailed",
                    "error_message": str(exc),
                    "request_id": None,
                    "elapsed_seconds": int(time.monotonic() - start),
                },
                ensure_ascii=False,
            )

        _, _, final_status = self._extract_task_id_and_status(payload)
        urls = self._extract_video_urls(payload)
        video_url = urls[0] if urls else None
        error_code, error_message = self._extract_failure(payload)

        await self._bridge_upsert_task(
            task_id=tid,
            status=final_status or "RUNNING",
            raw_last_response=payload,
            video_url=video_url,
            error_code=error_code,
            error_message=error_message,
            credential_alias=resolved_credential_alias,
            routing_group_id=resolved_routing_group_id,
            __request__=__request__,
        )

        status_text = str(final_status or "").strip().upper()
        ok = status_text in {"SUCCESS", "SUCCEEDED", "COMPLETED"}
        return json.dumps(
            {
                "ok": ok,
                "task_id": tid,
                "response_id": tid,
                "status": final_status,
                "video_url": video_url,
                "video_url_markdown": f"[查看视频]({video_url})" if video_url else "暂无",
                "output_urls": urls,
                "error_code": (None if ok else (error_code or "GenerationTaskFailed")),
                "error_message": (None if ok else error_message),
                "request_id": None,
                "credential_alias": resolved_credential_alias,
                "routing_group_id": resolved_routing_group_id,
                "elapsed_seconds": int(time.monotonic() - start),
                "raw_response": payload,
            },
            ensure_ascii=False,
        )

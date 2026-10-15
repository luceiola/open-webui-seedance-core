"""
title: AI Utility MiniMax H3 Tool
author: local-dev
version: 0.1.0
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

import httpx
from fastapi import Request
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
        and hasattr(toolkit_mod, "normalize_httpx_error")
        and hasattr(toolkit_mod, "request_openwebui_json")
    )
    if has_basics and not force_reload:
        return

    candidate_paths = [
        _TOOL_DIR / "shared" / "toolkit.py",
        Path.cwd() / "templates" / "shared" / "toolkit.py",
        Path.cwd().parent / "templates" / "shared" / "toolkit.py",
    ]
    toolkit_path = next((path for path in candidate_paths if path.exists() and path.is_file()), None)
    if toolkit_path is None:
        raise ImportError("Unable to locate templates/shared/toolkit.py")

    shared_pkg = sys.modules.get("shared")
    if shared_pkg is None:
        shared_pkg = types.ModuleType("shared")
        shared_pkg.__path__ = [str(toolkit_path.parent)]
        sys.modules["shared"] = shared_pkg
    else:
        package_paths = list(getattr(shared_pkg, "__path__", []) or [])
        shared_path = str(toolkit_path.parent)
        if shared_path not in package_paths:
            package_paths.append(shared_path)
            shared_pkg.__path__ = package_paths

    if toolkit_mod is None:
        toolkit_mod = types.ModuleType("shared.toolkit")
        sys.modules["shared.toolkit"] = toolkit_mod
    toolkit_mod.__dict__["__file__"] = str(toolkit_path)
    exec(toolkit_path.read_text(encoding="utf-8"), toolkit_mod.__dict__)


_ensure_shared_toolkit_loaded()

from shared.toolkit import (  # noqa: E402
    bridge_upsert,
    build_auth_headers,
    build_base_url,
    normalize_httpx_error,
    request_openwebui_json,
)


TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled", "canceled", "interrupted"}
ALLOWED_CONTEXT_IR = {"none", "rh-official", "doubao"}
ALLOWED_RATIOS = {"1:1", "4:5", "5:4", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}
ALLOWED_INSTANCE_TYPES = {"default", "plus"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


class H3InputError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


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
        PUBLIC_API_BASE_URL: str = Field(
            default=os.getenv("AI_UTILITY_PUBLIC_API_BASE_URL", "http://10.104.14.205:8807"),
            description="AI Utility Public API base URL.",
        )
        PUBLIC_API_KEY: str = Field(
            default=os.getenv("AI_UTILITY_PUBLIC_API_KEY", ""),
            description="AI Utility Public API bearer key.",
        )
        PUBLIC_API_KEY_ENV: str = Field(
            default="AI_UTILITY_PUBLIC_API_KEY",
            description="Environment variable containing the Public API key when PUBLIC_API_KEY is empty.",
        )
        REQUEST_TIMEOUT_SECONDS: int = Field(default=180, ge=30, le=1800)
        UPLOAD_TIMEOUT_SECONDS: int = Field(default=300, ge=30, le=3600)
        VIDEO_DOWNLOAD_TIMEOUT_SECONDS: int = Field(default=600, ge=30, le=7200)
        DEFAULT_DURATION_SECONDS: int = Field(default=5, ge=1, le=15)
        DEFAULT_RATIO: str = Field(default="9:16")
        DEFAULT_CONTEXT_IR: str = Field(default="rh-official")
        DEFAULT_INSTANCE_TYPE: str = Field(default="plus")
        DEFAULT_POLL_INTERVAL_SECONDS: int = Field(default=5, ge=1, le=60)
        DEFAULT_WAIT_TIMEOUT_SECONDS: int = Field(default=1800, ge=30, le=21600)
        DEFAULT_MAX_POLLS: int = Field(default=360, ge=1, le=5000)

    _PROVIDER = "ai_utility_minimax_h3"
    _SKILL_NAME = "ai-utility-minimax-h3"
    _TOOL_PREFIX = "ai_utility_minimax_h3_tool"

    def __init__(self) -> None:
        self.valves = self.Valves()
        self._public_file_cache: dict[str, dict[str, Any]] = {}

    def _error(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        job_id: Optional[str] = None,
        request_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> str:
        payload: dict[str, Any] = {
            "ok": False,
            "status_code": status_code,
            "job_id": job_id,
            "error_code": code,
            "error_message": message,
            "request_id": request_id,
        }
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)

    def _public_api_key(self) -> str:
        direct = str(self.valves.PUBLIC_API_KEY or "").strip()
        if direct:
            return direct
        env_name = str(self.valves.PUBLIC_API_KEY_ENV or "").strip()
        return str(os.getenv(env_name, "") if env_name else "").strip()

    def _public_api_headers(self) -> dict[str, str]:
        api_key = self._public_api_key()
        if not api_key:
            raise H3InputError(
                "PublicApiKeyMissing",
                "AI Utility Public API key is not configured",
                status_code=500,
            )
        return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    async def _public_api_request_json(
        self,
        method: str,
        path: str,
        *,
        body: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        base_url = str(self.valves.PUBLIC_API_BASE_URL or "").rstrip("/")
        url = f"{base_url}{path}"
        request_headers = self._public_api_headers()
        request_headers.update(headers or {})
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.REQUEST_TIMEOUT_SECONDS,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    json=body,
                )
        except Exception as exc:
            return {
                "ok": False,
                "status_code": 502,
                "error_code": "PublicApiRequestFailed",
                "error_message": str(exc),
                "request_id": None,
            }

        if response.status_code >= 400:
            return normalize_httpx_error(response)
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": response.text}
        return {"ok": True, "status_code": response.status_code, "data": payload}

    async def _public_api_download(self, path: str) -> bytes:
        base_url = str(self.valves.PUBLIC_API_BASE_URL or "").rstrip("/")
        url = f"{base_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.VIDEO_DOWNLOAD_TIMEOUT_SECONDS,
                trust_env=False,
                follow_redirects=True,
            ) as client:
                response = await client.get(url, headers=self._public_api_headers())
        except Exception as exc:
            raise H3InputError("VideoDownloadFailed", str(exc), status_code=502) from exc
        if response.status_code >= 400:
            error = normalize_httpx_error(response)
            raise H3InputError(
                str(error.get("error_code") or "VideoDownloadFailed"),
                str(error.get("error_message") or "Failed to download H3 video"),
                status_code=int(error.get("status_code") or 502),
            )
        return response.content

    async def _openwebui_request_json(
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

    def _extract_upload_items(self, __files__: Any) -> list[dict[str, Any]]:
        if not isinstance(__files__, list):
            return []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in __files__:
            if not isinstance(item, dict):
                continue
            nested = item.get("file") if isinstance(item.get("file"), dict) else {}
            file_id = ""
            for key in ("id", "file_id", "upload_id"):
                value = item.get(key) or nested.get(key)
                if isinstance(value, str) and value.strip():
                    file_id = value.strip()
                    break
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            items.append(
                {
                    "id": file_id,
                    "name": str(item.get("name") or item.get("filename") or nested.get("filename") or "").strip(),
                    "content_type": str(
                        item.get("content_type")
                        or item.get("contentType")
                        or nested.get("content_type")
                        or ""
                    ).strip(),
                }
            )
        return items

    async def _load_upload_items(
        self,
        __files__: Any,
        __request__: Optional[Request],
    ) -> list[dict[str, Any]]:
        uploads = self._extract_upload_items(__files__)
        loaded: list[dict[str, Any]] = []
        for upload in uploads:
            file_id = str(upload.get("id") or "").strip()
            metadata_result = await self._openwebui_request_json(
                "GET",
                f"/api/v1/files/{file_id}",
                __request__,
            )
            metadata = metadata_result.get("data") if isinstance(metadata_result.get("data"), dict) else {}
            meta = metadata.get("meta") if isinstance(metadata.get("meta"), dict) else {}
            name = str(upload.get("name") or meta.get("name") or metadata.get("filename") or "").strip()
            content_type = str(upload.get("content_type") or meta.get("content_type") or "").strip()
            media_type = self._media_type(name=name, content_type=content_type)
            loaded.append(
                {
                    "id": file_id,
                    "name": name or file_id,
                    "content_type": content_type or mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "media_type": media_type,
                }
            )
        return loaded

    @staticmethod
    def _media_type(*, name: str, content_type: str) -> Optional[str]:
        mime = str(content_type or "").strip().lower()
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
        suffix = Path(str(name or "")).suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            return "image"
        if suffix in VIDEO_SUFFIXES:
            return "video"
        if suffix in AUDIO_SUFFIXES:
            return "audio"
        return None

    @staticmethod
    def _clean_upload_token(value: str) -> str:
        token = unquote(str(value or "").strip().strip("\"'"))
        token = token.lstrip("%@")
        return Path(token).name.strip()

    def _resolve_upload_reference(
        self,
        value: str,
        uploads: list[dict[str, Any]],
        *,
        expected_type: Optional[str],
        label: str,
    ) -> dict[str, Any]:
        raw = str(value or "").strip()
        cleaned = self._clean_upload_token(raw)
        if not cleaned:
            raise H3InputError("InvalidUploadReference", f"{label} is empty")

        exact = [item for item in uploads if str(item.get("id") or "") == raw]
        if not exact:
            lowered = cleaned.lower()
            exact = [
                item
                for item in uploads
                if lowered
                in {
                    str(item.get("name") or "").strip().lower(),
                    Path(str(item.get("name") or "")).name.lower(),
                    Path(str(item.get("name") or "")).stem.lower(),
                }
            ]
        if not exact:
            exact = [
                item
                for item in uploads
                if cleaned.lower() in str(item.get("name") or "").lower()
            ]
        if len(exact) != 1:
            code = "AmbiguousUploadReference" if len(exact) > 1 else "MissingUploadReference"
            message = (
                f"{label} reference is ambiguous: {raw}"
                if len(exact) > 1
                else f"{label} reference was not found in current chat attachments: {raw}"
            )
            raise H3InputError(code, message)

        item = exact[0]
        if expected_type and item.get("media_type") != expected_type:
            raise H3InputError(
                "UploadMediaTypeMismatch",
                f"{label} requires {expected_type}, got {item.get('media_type') or 'unknown'}: {raw}",
            )
        return item

    async def _upload_to_public_api(
        self,
        upload: dict[str, Any],
        *,
        __request__: Optional[Request],
    ) -> dict[str, Any]:
        file_id = str(upload.get("id") or "").strip()
        cached = self._public_file_cache.get(file_id)
        if cached:
            return cached

        base_url = build_base_url(__request__, self.valves.OPENWEBUI_BASE_URL).rstrip("/")
        headers = build_auth_headers(
            __request__,
            self.valves.OPENWEBUI_API_KEY,
            include_content_type=False,
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.UPLOAD_TIMEOUT_SECONDS,
                trust_env=False,
            ) as client:
                response = await client.get(
                    f"{base_url}/api/v1/files/{file_id}/content",
                    headers=headers,
                )
                if response.status_code >= 400:
                    return normalize_httpx_error(response)
                content = response.content
                content_type = str(upload.get("content_type") or "application/octet-stream")
                public_response = await client.post(
                    f"{str(self.valves.PUBLIC_API_BASE_URL or '').rstrip('/')}/api/v1/files",
                    headers=self._public_api_headers(),
                    files={"file": (str(upload.get("name") or f"{file_id}.bin"), content, content_type)},
                )
        except Exception as exc:
            return {
                "ok": False,
                "status_code": 502,
                "error_code": "PublicApiUploadFailed",
                "error_message": str(exc),
                "request_id": None,
            }

        if public_response.status_code >= 400:
            return normalize_httpx_error(public_response)
        try:
            payload = public_response.json()
        except Exception:
            payload = {"raw_text": public_response.text}
        public_file_id = str(payload.get("file_id") or "").strip()
        if not public_file_id:
            return {
                "ok": False,
                "status_code": 502,
                "error_code": "PublicApiUploadMissingFileId",
                "error_message": "Public API upload response did not contain file_id",
                "request_id": None,
            }
        record = {
            "ok": True,
            "openwebui_file_id": file_id,
            "file_id": public_file_id,
            "filename": payload.get("filename") or upload.get("name"),
            "content_type": payload.get("content_type") or content_type,
            "media_type": upload.get("media_type"),
        }
        self._public_file_cache[file_id] = record
        return record

    async def _upload_references(
        self,
        uploads: list[dict[str, Any]],
        *,
        __request__: Optional[Request],
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        uploaded: list[dict[str, Any]] = []
        for upload in uploads:
            result = await self._upload_to_public_api(upload, __request__=__request__)
            if not result.get("ok"):
                return uploaded, result
            uploaded.append({**upload, **result})
        return uploaded, None

    def _resolve_ref2v_uploads(
        self,
        *,
        uploads: list[dict[str, Any]],
        reference_images: Optional[list[str]],
        reference_videos: Optional[list[str]],
        reference_audios: Optional[list[str]],
    ) -> dict[str, list[dict[str, Any]]]:
        explicit = {
            "image": reference_images or [],
            "video": reference_videos or [],
            "audio": reference_audios or [],
        }
        has_explicit = any(explicit.values())
        if not has_explicit:
            return {
                media_type: [item for item in uploads if item.get("media_type") == media_type]
                for media_type in ("image", "video", "audio")
            }

        resolved: dict[str, list[dict[str, Any]]] = {"image": [], "video": [], "audio": []}
        for media_type, values in explicit.items():
            for value in values:
                resolved[media_type].append(
                    self._resolve_upload_reference(
                        value,
                        uploads,
                        expected_type=media_type,
                        label=f"reference {media_type}",
                    )
                )
        return resolved

    def _resolve_fl2v_uploads(
        self,
        *,
        prompt: str,
        uploads: list[dict[str, Any]],
        first_frame: str,
        last_frame: str,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        first = (
            self._resolve_upload_reference(
                first_frame,
                uploads,
                expected_type="image",
                label="first frame",
            )
            if str(first_frame or "").strip()
            else None
        )
        last = (
            self._resolve_upload_reference(
                last_frame,
                uploads,
                expected_type="image",
                label="last frame",
            )
            if str(last_frame or "").strip()
            else None
        )
        if first or last:
            referenced_ids = {
                str(item.get("id") or "")
                for item in (first, last)
                if isinstance(item, dict)
            }
            extra_uploads = [item for item in uploads if str(item.get("id") or "") not in referenced_ids]
            if extra_uploads:
                raise H3InputError(
                    "H3ModeConflict",
                    "FL2V frame inputs cannot be combined with additional reference media; use REF2V or confirm which frames to keep",
                )
            return first, last

        images = [item for item in uploads if item.get("media_type") == "image"]
        if not images:
            if uploads:
                raise H3InputError(
                    "H3ModeConflict",
                    "FL2V text-to-video does not accept non-frame reference media; use REF2V",
                )
            return None, None

        text = str(prompt or "")
        wants_first = bool(re.search(r"首帧|起始帧|第一帧|开场帧", text))
        wants_last = bool(re.search(r"尾帧|结束帧|最后一帧|结尾帧", text))
        if wants_first and wants_last and len(images) >= 2:
            return images[0], images[1]
        if wants_first and len(images) == 1:
            return images[0], None
        if wants_last and len(images) == 1:
            return None, images[0]
        raise H3InputError(
            "H3ModeConflict",
            "Attached images without explicit first/last frame intent must use REF2V",
        )

    @staticmethod
    def _validate_prompt(prompt: str) -> str:
        value = str(prompt or "").strip()
        if not value:
            raise H3InputError("PromptRequired", "prompt is required")
        if len(value) > 7000:
            raise H3InputError("PromptTooLong", "prompt must not exceed 7000 characters")
        return value

    def _validate_common(
        self,
        *,
        duration: int,
        ratio: str,
        context_ir: str,
        instance_type: str,
        mode: str,
    ) -> tuple[int, str, str, str]:
        duration_value = int(duration)
        min_duration = 5 if mode == "ref2v" else 1
        if not min_duration <= duration_value <= 15:
            raise H3InputError(
                "InvalidDuration",
                f"{mode} duration must be between {min_duration} and 15 seconds",
            )
        ratio_value = str(ratio or self.valves.DEFAULT_RATIO).strip()
        if ratio_value not in ALLOWED_RATIOS:
            raise H3InputError("InvalidRatio", f"ratio must be one of: {', '.join(sorted(ALLOWED_RATIOS))}")
        context_value = str(context_ir or self.valves.DEFAULT_CONTEXT_IR).strip()
        if context_value not in ALLOWED_CONTEXT_IR:
            raise H3InputError(
                "InvalidContextIR",
                f"context_ir must be one of: {', '.join(sorted(ALLOWED_CONTEXT_IR))}",
            )
        instance_value = str(instance_type or self.valves.DEFAULT_INSTANCE_TYPE).strip()
        if instance_value not in ALLOWED_INSTANCE_TYPES:
            raise H3InputError("InvalidInstanceType", "instance_type must be default or plus")
        return duration_value, ratio_value, context_value, instance_value

    def _idempotency_key(
        self,
        *,
        mode: str,
        body: dict[str, Any],
        explicit_key: str,
        __user__: Optional[dict[str, Any]],
        __metadata__: Optional[dict[str, Any]],
    ) -> str:
        explicit = str(explicit_key or "").strip()
        if explicit:
            return explicit
        metadata = __metadata__ if isinstance(__metadata__, dict) else {}
        seed = json.dumps(
            {
                "mode": mode,
                "body": body,
                "user_id": (__user__ or {}).get("id"),
                "chat_id": metadata.get("chat_id"),
                "message_id": metadata.get("message_id"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @staticmethod
    def _task_status(status: str) -> str:
        value = str(status or "").strip().lower()
        return {
            "pending": "PENDING",
            "running": "RUNNING",
            "completed": "SUCCEEDED",
            "failed": "FAILED",
            "cancelled": "CANCELED",
            "canceled": "CANCELED",
            "interrupted": "FAILED",
        }.get(value, "RUNNING")

    @staticmethod
    def _job_costs(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        costs = payload.get("costs")
        if isinstance(costs, dict) and costs:
            return costs
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        result_costs = result.get("costs")
        return result_costs if isinstance(result_costs, dict) and result_costs else None

    def _compact_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = str(payload.get("status") or "").strip().lower()
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        context_ir = result.get("context_ir") if isinstance(result.get("context_ir"), dict) else {}
        return {
            "job_id": payload.get("job_id"),
            "status": status,
            "mode": payload.get("mode"),
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "completed_at": payload.get("completed_at"),
            "error": payload.get("error"),
            "costs": self._job_costs(payload),
            "context_ir_provider": context_ir.get("provider"),
            "context_ir_text": context_ir.get("text"),
            "video_ready": status == "completed" and bool(payload.get("video_url")),
        }

    async def _bridge_job(
        self,
        payload: dict[str, Any],
        *,
        tool_name: str,
        prompt_text: str,
        generation_params: dict[str, Any],
        __request__: Optional[Request],
        __metadata__: Optional[dict[str, Any]],
        submit: bool,
    ) -> bool:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            return False
        metadata = __metadata__ if isinstance(__metadata__, dict) else {}
        bridge_payload: dict[str, Any] = {
            "task_id": job_id,
            "provider": self._PROVIDER,
            "provider_task_id": job_id,
            "chat_id": metadata.get("chat_id"),
            "status": self._task_status(str(payload.get("status") or "")),
            "artifact_kind": "video",
            "costs": self._job_costs(payload),
            "archive_required": False,
        }
        if submit:
            bridge_payload.update(
                {
                    "tool_name": f"{self._TOOL_PREFIX}.{tool_name}",
                    "skill_name": self._SKILL_NAME,
                    "model": "minimax-h3",
                    "prompt_text": prompt_text,
                    "generation_params": generation_params,
                }
            )
            bridge_payload["raw_submit_response"] = payload
        else:
            bridge_payload["raw_last_response"] = payload
        return await bridge_upsert(
            requester=self._openwebui_request_json,
            payload=bridge_payload,
            __request__=__request__,
        )

    async def create_h3_ref2v_job(
        self,
        prompt: str,
        duration: int = 5,
        ratio: str = "9:16",
        context_ir: str = "rh-official",
        context_ir_profile: str = "",
        context_ir_model_profile: str = "",
        instance_type: str = "plus",
        reference_images: Optional[list[str]] = None,
        reference_videos: Optional[list[str]] = None,
        reference_audios: Optional[list[str]] = None,
        confirm_processing: bool = False,
        idempotency_key: str = "",
        __files__: Optional[list[dict[str, Any]]] = None,
        __request__: Request = None,
        __user__: Optional[dict[str, Any]] = None,
        __metadata__: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        使用普通参考图片、视频或音频创建 MiniMax H3 Ref2V 任务。
        仅在用户明确确认付费生成后传 confirm_processing=true。
        """
        try:
            prompt_value = self._validate_prompt(prompt)
            duration_value, ratio_value, context_value, instance_value = self._validate_common(
                duration=duration,
                ratio=ratio,
                context_ir=context_ir,
                instance_type=instance_type,
                mode="ref2v",
            )
            if not confirm_processing:
                raise H3InputError(
                    "ProcessingConfirmationRequired",
                    "User confirmation is required before creating a paid H3 job",
                    status_code=409,
                )
            uploads = await self._load_upload_items(__files__, __request__)
            resolved = self._resolve_ref2v_uploads(
                uploads=uploads,
                reference_images=reference_images,
                reference_videos=reference_videos,
                reference_audios=reference_audios,
            )
            if not any(resolved.values()):
                raise H3InputError(
                    "ReferencesRequired",
                    "REF2V requires at least one reference image, video, or audio attachment",
                )
            uploaded, upload_error = await self._upload_references(
                [item for values in resolved.values() for item in values],
                __request__=__request__,
            )
            if upload_error:
                return self._error(
                    str(upload_error.get("error_code") or "PublicApiUploadFailed"),
                    str(upload_error.get("error_message") or upload_error.get("error") or "Upload failed"),
                    status_code=int(upload_error.get("status_code") or 502),
                    request_id=upload_error.get("request_id"),
                )
            upload_by_openwebui_id = {item["openwebui_file_id"]: item for item in uploaded}
            body: dict[str, Any] = {
                "prompt": prompt_value,
                "duration": duration_value,
                "ratio": ratio_value,
                "context_ir": context_value,
                "instance_type": instance_value,
                "reference_image_file_ids": [
                    upload_by_openwebui_id[item["id"]]["file_id"] for item in resolved["image"]
                ],
                "reference_video_file_ids": [
                    upload_by_openwebui_id[item["id"]]["file_id"] for item in resolved["video"]
                ],
                "reference_audio_file_ids": [
                    upload_by_openwebui_id[item["id"]]["file_id"] for item in resolved["audio"]
                ],
                "confirm_processing": True,
            }
            if str(context_ir_profile or "").strip():
                body["context_ir_profile"] = str(context_ir_profile).strip()
            if str(context_ir_model_profile or "").strip():
                body["context_ir_model_profile"] = str(context_ir_model_profile).strip()
            key = self._idempotency_key(
                mode="ref2v",
                body=body,
                explicit_key=idempotency_key,
                __user__=__user__,
                __metadata__=__metadata__,
            )
            result = await self._public_api_request_json(
                "POST",
                "/api/v1/minimax-h3/ref2v/jobs",
                body=body,
                headers={"Idempotency-Key": key},
            )
            if not result.get("ok"):
                return self._error(
                    str(result.get("error_code") or "CreateH3JobFailed"),
                    str(result.get("error_message") or result.get("error") or "Failed to create H3 job"),
                    status_code=int(result.get("status_code") or 502),
                    request_id=result.get("request_id"),
                )
            payload = result.get("data") if isinstance(result.get("data"), dict) else {}
            bridged = await self._bridge_job(
                payload,
                tool_name="create_h3_ref2v_job",
                prompt_text=prompt_value,
                generation_params={
                    "mode": "ref2v",
                    "duration": duration_value,
                    "ratio": ratio_value,
                    "context_ir": context_value,
                    "context_ir_profile": str(context_ir_profile or "").strip() or None,
                    "context_ir_model_profile": str(context_ir_model_profile or "").strip() or None,
                    "instance_type": instance_value,
                    "reference_image_count": len(resolved["image"]),
                    "reference_video_count": len(resolved["video"]),
                    "reference_audio_count": len(resolved["audio"]),
                    "idempotency_key": key,
                },
                __request__=__request__,
                __metadata__=__metadata__,
                submit=True,
            )
            compact = self._compact_job(payload)
            compact.update(
                {
                    "ok": True,
                    "idempotency_key": key,
                    "idempotency_replayed": bool(payload.get("idempotency_replayed")),
                    "task_center_bridge_ok": bridged,
                }
            )
            return json.dumps(compact, ensure_ascii=False)
        except H3InputError as exc:
            return self._error(exc.code, exc.message, status_code=exc.status_code)
        except Exception as exc:
            return self._error("UnexpectedToolError", str(exc), status_code=500)

    async def create_h3_fl2v_job(
        self,
        prompt: str,
        duration: int = 5,
        ratio: str = "9:16",
        context_ir: str = "rh-official",
        context_ir_profile: str = "",
        context_ir_model_profile: str = "",
        instance_type: str = "plus",
        first_frame: str = "",
        last_frame: str = "",
        confirm_processing: bool = False,
        idempotency_key: str = "",
        __files__: Optional[list[dict[str, Any]]] = None,
        __request__: Request = None,
        __user__: Optional[dict[str, Any]] = None,
        __metadata__: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        创建 MiniMax H3 FL2V 任务，支持纯文生、显式首帧、尾帧或首尾帧。
        仅在用户明确确认付费生成后传 confirm_processing=true。
        """
        try:
            prompt_value = self._validate_prompt(prompt)
            duration_value, ratio_value, context_value, instance_value = self._validate_common(
                duration=duration,
                ratio=ratio,
                context_ir=context_ir,
                instance_type=instance_type,
                mode="fl2v",
            )
            if not confirm_processing:
                raise H3InputError(
                    "ProcessingConfirmationRequired",
                    "User confirmation is required before creating a paid H3 job",
                    status_code=409,
                )
            uploads = await self._load_upload_items(__files__, __request__)
            first_upload, last_upload = self._resolve_fl2v_uploads(
                prompt=prompt_value,
                uploads=uploads,
                first_frame=first_frame,
                last_frame=last_frame,
            )
            uploaded, upload_error = await self._upload_references(
                [item for item in (first_upload, last_upload) if item is not None],
                __request__=__request__,
            )
            if upload_error:
                return self._error(
                    str(upload_error.get("error_code") or "PublicApiUploadFailed"),
                    str(upload_error.get("error_message") or upload_error.get("error") or "Upload failed"),
                    status_code=int(upload_error.get("status_code") or 502),
                    request_id=upload_error.get("request_id"),
                )
            upload_by_openwebui_id = {item["openwebui_file_id"]: item for item in uploaded}
            body: dict[str, Any] = {
                "prompt": prompt_value,
                "duration": duration_value,
                "ratio": ratio_value,
                "context_ir": context_value,
                "instance_type": instance_value,
                "first_frame_file_id": (
                    upload_by_openwebui_id[first_upload["id"]]["file_id"] if first_upload else None
                ),
                "last_frame_file_id": (
                    upload_by_openwebui_id[last_upload["id"]]["file_id"] if last_upload else None
                ),
                "confirm_processing": True,
            }
            if str(context_ir_profile or "").strip():
                body["context_ir_profile"] = str(context_ir_profile).strip()
            if str(context_ir_model_profile or "").strip():
                body["context_ir_model_profile"] = str(context_ir_model_profile).strip()
            key = self._idempotency_key(
                mode="fl2v",
                body=body,
                explicit_key=idempotency_key,
                __user__=__user__,
                __metadata__=__metadata__,
            )
            result = await self._public_api_request_json(
                "POST",
                "/api/v1/minimax-h3/fl2v/jobs",
                body=body,
                headers={"Idempotency-Key": key},
            )
            if not result.get("ok"):
                return self._error(
                    str(result.get("error_code") or "CreateH3JobFailed"),
                    str(result.get("error_message") or result.get("error") or "Failed to create H3 job"),
                    status_code=int(result.get("status_code") or 502),
                    request_id=result.get("request_id"),
                )
            payload = result.get("data") if isinstance(result.get("data"), dict) else {}
            bridged = await self._bridge_job(
                payload,
                tool_name="create_h3_fl2v_job",
                prompt_text=prompt_value,
                generation_params={
                    "mode": "fl2v",
                    "duration": duration_value,
                    "ratio": ratio_value,
                    "context_ir": context_value,
                    "context_ir_profile": str(context_ir_profile or "").strip() or None,
                    "context_ir_model_profile": str(context_ir_model_profile or "").strip() or None,
                    "instance_type": instance_value,
                    "first_frame": first_frame or None,
                    "last_frame": last_frame or None,
                    "idempotency_key": key,
                },
                __request__=__request__,
                __metadata__=__metadata__,
                submit=True,
            )
            compact = self._compact_job(payload)
            compact.update(
                {
                    "ok": True,
                    "idempotency_key": key,
                    "idempotency_replayed": bool(payload.get("idempotency_replayed")),
                    "task_center_bridge_ok": bridged,
                }
            )
            return json.dumps(compact, ensure_ascii=False)
        except H3InputError as exc:
            return self._error(exc.code, exc.message, status_code=exc.status_code)
        except Exception as exc:
            return self._error("UnexpectedToolError", str(exc), status_code=500)

    async def get_h3_job(
        self,
        mode: str,
        job_id: str,
        __request__: Request = None,
        __metadata__: Optional[dict[str, Any]] = None,
    ) -> str:
        """查询 Ref2V 或 FL2V 任务状态、费用和结果。"""
        try:
            mode_value = str(mode or "").strip().lower()
            if mode_value not in {"ref2v", "fl2v"}:
                raise H3InputError("InvalidMode", "mode must be ref2v or fl2v")
            job_value = str(job_id or "").strip()
            if not job_value:
                raise H3InputError("JobIdRequired", "job_id is required")
            result = await self._public_api_request_json(
                "GET",
                f"/api/v1/minimax-h3/{mode_value}/jobs/{job_value}",
            )
            if not result.get("ok"):
                return self._error(
                    str(result.get("error_code") or "GetH3JobFailed"),
                    str(result.get("error_message") or result.get("error") or "Failed to query H3 job"),
                    status_code=int(result.get("status_code") or 502),
                    job_id=job_value,
                    request_id=result.get("request_id"),
                )
            payload = result.get("data") if isinstance(result.get("data"), dict) else {}
            bridged = await self._bridge_job(
                payload,
                tool_name="get_h3_job",
                prompt_text="",
                generation_params={"mode": mode_value, "job_id": job_value},
                __request__=__request__,
                __metadata__=__metadata__,
                submit=False,
            )
            compact = self._compact_job(payload)
            compact.update({"ok": True, "task_center_bridge_ok": bridged})
            return json.dumps(compact, ensure_ascii=False)
        except H3InputError as exc:
            return self._error(exc.code, exc.message, status_code=exc.status_code)
        except Exception as exc:
            return self._error("UnexpectedToolError", str(exc), status_code=500)

    async def wait_h3_job(
        self,
        mode: str,
        job_id: str,
        poll_interval_seconds: Optional[int] = None,
        wait_timeout_seconds: Optional[int] = None,
        max_polls: Optional[int] = None,
        __request__: Request = None,
        __metadata__: Optional[dict[str, Any]] = None,
    ) -> str:
        """等待 H3 任务进入终态；用户明确要求持续等待时使用。"""
        try:
            mode_value = str(mode or "").strip().lower()
            job_value = str(job_id or "").strip()
            if mode_value not in {"ref2v", "fl2v"}:
                raise H3InputError("InvalidMode", "mode must be ref2v or fl2v")
            if not job_value:
                raise H3InputError("JobIdRequired", "job_id is required")
            poll = int(poll_interval_seconds or self.valves.DEFAULT_POLL_INTERVAL_SECONDS)
            timeout = int(wait_timeout_seconds or self.valves.DEFAULT_WAIT_TIMEOUT_SECONDS)
            polls = int(max_polls or self.valves.DEFAULT_MAX_POLLS)
            start = time.monotonic()
            latest: dict[str, Any] = {}
            for _ in range(polls):
                result = await self._public_api_request_json(
                    "GET",
                    f"/api/v1/minimax-h3/{mode_value}/jobs/{job_value}",
                )
                if not result.get("ok"):
                    return self._error(
                        str(result.get("error_code") or "GetH3JobFailed"),
                        str(result.get("error_message") or result.get("error") or "Failed to query H3 job"),
                        status_code=int(result.get("status_code") or 502),
                        job_id=job_value,
                        request_id=result.get("request_id"),
                    )
                latest = result.get("data") if isinstance(result.get("data"), dict) else {}
                if str(latest.get("status") or "").strip().lower() in TERMINAL_JOB_STATUSES:
                    break
                if time.monotonic() - start >= timeout:
                    break
                await asyncio.sleep(poll)
            bridged = await self._bridge_job(
                latest,
                tool_name="wait_h3_job",
                prompt_text="",
                generation_params={"mode": mode_value, "job_id": job_value},
                __request__=__request__,
                __metadata__=__metadata__,
                submit=False,
            )
            compact = self._compact_job(latest)
            compact.update({"ok": True, "task_center_bridge_ok": bridged})
            return json.dumps(compact, ensure_ascii=False)
        except H3InputError as exc:
            return self._error(exc.code, exc.message, status_code=exc.status_code)
        except Exception as exc:
            return self._error("UnexpectedToolError", str(exc), status_code=500)

    async def list_h3_jobs(
        self,
        mode: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """列出 H3 Ref2V、FL2V 或全部任务。"""
        try:
            mode_value = str(mode or "all").strip().lower()
            if mode_value not in {"all", "ref2v", "fl2v"}:
                raise H3InputError("InvalidMode", "mode must be all, ref2v, or fl2v")
            limit_value = max(1, min(int(limit or 50), 200))
            offset_value = max(0, int(offset or 0))
            modes = ["ref2v", "fl2v"] if mode_value == "all" else [mode_value]
            jobs: list[dict[str, Any]] = []
            for item_mode in modes:
                result = await self._public_api_request_json(
                    "GET",
                    f"/api/v1/minimax-h3/{item_mode}/jobs?limit={limit_value}&offset={offset_value}",
                )
                if not result.get("ok"):
                    return self._error(
                        str(result.get("error_code") or "ListH3JobsFailed"),
                        str(result.get("error_message") or result.get("error") or "Failed to list H3 jobs"),
                        status_code=int(result.get("status_code") or 502),
                        request_id=result.get("request_id"),
                    )
                payload = result.get("data") if isinstance(result.get("data"), dict) else {}
                for job in payload.get("items") if isinstance(payload.get("items"), list) else []:
                    if isinstance(job, dict):
                        jobs.append(self._compact_job(job))
            jobs.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
            return json.dumps({"ok": True, "items": jobs[:limit_value], "count": len(jobs)}, ensure_ascii=False)
        except H3InputError as exc:
            return self._error(exc.code, exc.message, status_code=exc.status_code)
        except Exception as exc:
            return self._error("UnexpectedToolError", str(exc), status_code=500)

    async def get_h3_job_logs(self, mode: str, job_id: str, limit: int = 200) -> str:
        """查询 H3 任务日志，仅用于排障。"""
        mode_value = str(mode or "").strip().lower()
        if mode_value not in {"ref2v", "fl2v"}:
            return self._error("InvalidMode", "mode must be ref2v or fl2v")
        job_value = str(job_id or "").strip()
        if not job_value:
            return self._error("JobIdRequired", "job_id is required")
        result = await self._public_api_request_json(
            "GET",
            f"/api/v1/minimax-h3/{mode_value}/jobs/{job_value}/logs?limit={max(1, min(int(limit), 2000))}",
        )
        if not result.get("ok"):
            return self._error(
                str(result.get("error_code") or "GetH3JobLogsFailed"),
                str(result.get("error_message") or result.get("error") or "Failed to query H3 job logs"),
                status_code=int(result.get("status_code") or 502),
                job_id=job_value,
                request_id=result.get("request_id"),
            )
        return json.dumps({"ok": True, **(result.get("data") or {})}, ensure_ascii=False)

    async def cancel_h3_job(
        self,
        mode: str,
        job_id: str,
        __request__: Request = None,
        __metadata__: Optional[dict[str, Any]] = None,
    ) -> str:
        """取消仍处于 pending 状态的 H3 任务。"""
        mode_value = str(mode or "").strip().lower()
        job_value = str(job_id or "").strip()
        if mode_value not in {"ref2v", "fl2v"}:
            return self._error("InvalidMode", "mode must be ref2v or fl2v")
        if not job_value:
            return self._error("JobIdRequired", "job_id is required")
        result = await self._public_api_request_json(
            "POST",
            f"/api/v1/minimax-h3/{mode_value}/jobs/{job_value}/cancel",
        )
        if not result.get("ok"):
            return self._error(
                str(result.get("error_code") or "CancelH3JobFailed"),
                str(result.get("error_message") or result.get("error") or "Failed to cancel H3 job"),
                status_code=int(result.get("status_code") or 502),
                job_id=job_value,
                request_id=result.get("request_id"),
            )
        payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        bridged = await self._bridge_job(
            payload,
            tool_name="cancel_h3_job",
            prompt_text="",
            generation_params={"mode": mode_value, "job_id": job_value},
            __request__=__request__,
            __metadata__=__metadata__,
            submit=False,
        )
        compact = self._compact_job(payload)
        compact.update({"ok": True, "task_center_bridge_ok": bridged})
        return json.dumps(compact, ensure_ascii=False)

    async def attach_h3_video(
        self,
        mode: str,
        job_id: str,
        __request__: Request = None,
        __event_emitter__: Any = None,
    ) -> str:
        """
        下载已完成的 H3 视频并附加到 OpenWebUI。
        返回下载链接；不要直接把公共 API 的 Bearer 视频地址给用户。
        """
        try:
            mode_value = str(mode or "").strip().lower()
            job_value = str(job_id or "").strip()
            if mode_value not in {"ref2v", "fl2v"}:
                raise H3InputError("InvalidMode", "mode must be ref2v or fl2v")
            if not job_value:
                raise H3InputError("JobIdRequired", "job_id is required")
            result = await self._public_api_request_json(
                "GET",
                f"/api/v1/minimax-h3/{mode_value}/jobs/{job_value}",
            )
            if not result.get("ok"):
                return self._error(
                    str(result.get("error_code") or "GetH3JobFailed"),
                    str(result.get("error_message") or result.get("error") or "Failed to query H3 job"),
                    status_code=int(result.get("status_code") or 502),
                    job_id=job_value,
                    request_id=result.get("request_id"),
                )
            payload = result.get("data") if isinstance(result.get("data"), dict) else {}
            if str(payload.get("status") or "").strip().lower() != "completed":
                raise H3InputError("JobNotCompleted", "H3 video is not available before completion", status_code=409)
            video_path = str(payload.get("video_url") or "").strip()
            if not video_path:
                raise H3InputError("VideoUrlMissing", "Completed H3 job did not return a video URL", status_code=502)
            video_bytes = await self._public_api_download(video_path)
            openwebui_file = await self._upload_video_to_openwebui(
                video_bytes,
                filename=f"minimax-h3-{job_value}.mp4",
                __request__=__request__,
            )
            if not openwebui_file.get("ok"):
                return self._error(
                    str(openwebui_file.get("error_code") or "OpenWebUIUploadFailed"),
                    str(openwebui_file.get("error_message") or "Failed to attach H3 video"),
                    status_code=int(openwebui_file.get("status_code") or 502),
                    job_id=job_value,
                    request_id=openwebui_file.get("request_id"),
                )
            file_url = openwebui_file["url"]
            file_item = {
                "type": "file",
                "name": openwebui_file["name"],
                "url": file_url,
                "content_type": "video/mp4",
            }
            if callable(__event_emitter__):
                try:
                    await __event_emitter__(
                        {
                            "type": "chat:message:files",
                            "data": {"files": [file_item]},
                        }
                    )
                except Exception:
                    pass
            return json.dumps(
                {
                    "ok": True,
                    "job_id": job_value,
                    "mode": mode_value,
                    "openwebui_file_id": openwebui_file["id"],
                    "download_url": file_url,
                    "download_url_markdown": f"[下载视频]({file_url})",
                    "costs": self._job_costs(payload),
                },
                ensure_ascii=False,
            )
        except H3InputError as exc:
            return self._error(exc.code, exc.message, status_code=exc.status_code, job_id=str(job_id or ""))
        except Exception as exc:
            return self._error("UnexpectedToolError", str(exc), status_code=500, job_id=str(job_id or ""))

    async def _upload_video_to_openwebui(
        self,
        content: bytes,
        *,
        filename: str,
        __request__: Optional[Request],
    ) -> dict[str, Any]:
        base_url = build_base_url(__request__, self.valves.OPENWEBUI_BASE_URL).rstrip("/")
        headers = build_auth_headers(
            __request__,
            self.valves.OPENWEBUI_API_KEY,
            include_content_type=False,
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.UPLOAD_TIMEOUT_SECONDS,
                trust_env=False,
            ) as client:
                response = await client.post(
                    f"{base_url}/api/v1/files/?process=false",
                    headers=headers,
                    files={"file": (filename, content, "video/mp4")},
                )
        except Exception as exc:
            return {
                "ok": False,
                "status_code": 502,
                "error_code": "OpenWebUIUploadFailed",
                "error_message": str(exc),
                "request_id": None,
            }
        if response.status_code >= 400:
            return normalize_httpx_error(response)
        try:
            payload = response.json()
        except Exception:
            payload = {}
        file_id = str(payload.get("id") or "").strip()
        if not file_id:
            return {
                "ok": False,
                "status_code": 502,
                "error_code": "OpenWebUIUploadMissingFileId",
                "error_message": "OpenWebUI upload response did not contain id",
                "request_id": None,
            }
        return {
            "ok": True,
            "id": file_id,
            "name": str(payload.get("filename") or filename),
            "url": f"/api/v1/files/{file_id}/content",
        }

    async def get_h3_account_usage(
        self,
        start_date: str = "",
        end_date: str = "",
    ) -> str:
        """查询当前 Public API Key 的身份和费用汇总。"""
        me_result = await self._public_api_request_json("GET", "/api/v1/me")
        if not me_result.get("ok"):
            return self._error(
                str(me_result.get("error_code") or "GetH3AccountFailed"),
                str(me_result.get("error_message") or me_result.get("error") or "Failed to query account"),
                status_code=int(me_result.get("status_code") or 502),
                request_id=me_result.get("request_id"),
            )
        path = "/api/v1/usage"
        query: list[str] = []
        if str(start_date or "").strip():
            query.append(f"start_date={str(start_date).strip()}")
        if str(end_date or "").strip():
            query.append(f"end_date={str(end_date).strip()}")
        if query:
            path = f"{path}?{'&'.join(query)}"
        usage_result = await self._public_api_request_json("GET", path)
        if not usage_result.get("ok"):
            return self._error(
                str(usage_result.get("error_code") or "GetH3UsageFailed"),
                str(usage_result.get("error_message") or usage_result.get("error") or "Failed to query usage"),
                status_code=int(usage_result.get("status_code") or 502),
                request_id=usage_result.get("request_id"),
            )
        return json.dumps(
            {
                "ok": True,
                "account": me_result.get("data"),
                "usage": usage_result.get("data"),
            },
            ensure_ascii=False,
        )

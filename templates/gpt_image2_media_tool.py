"""
title: GPT Image 2 Media Tool
author: local-dev
version: 0.1.0
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Request
from pydantic import BaseModel, Field

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.append(str(_TOOL_DIR))

from shared.toolkit import (
    bridge_upsert,
    build_auth_headers,
    build_base_url,
    compact_media_asset_item,
    extract_media_asset_references,
    extract_request_id,
    normalize_httpx_error,
    request_openwebui_json,
)


class Tools:
    class Valves(BaseModel):
        OPENWEBUI_BASE_URL: str = Field(
            default="http://127.0.0.1:8080",
            description="OpenWebUI 服务地址，仅在无法从请求上下文推断时使用",
        )
        OPENWEBUI_API_KEY: str = Field(
            default="",
            description="可选。若请求上下文没有认证信息，使用该 API Key 调用后端接口",
        )
        DEFAULT_OPENAI_IMAGE_MODEL: str = Field(
            default="gpt-image-2",
            description="固定模型 ID（仅允许 gpt-image-2）",
        )
        DEFAULT_IMAGE_SIZE: str = Field(
            default="auto",
            description="默认图像尺寸（未指定时）",
        )
        DEFAULT_IMAGE_QUALITY: str = Field(
            default="auto",
            description="默认质量（未指定时）",
        )
        DEFAULT_IMAGE_BACKGROUND: str = Field(
            default="auto",
            description="默认背景（未指定时）",
        )
        DEFAULT_IMAGE_OUTPUT_FORMAT: str = Field(
            default="png",
            description="默认输出格式（未指定时）",
        )
        DEFAULT_IMAGE_N: int = Field(
            default=1,
            ge=1,
            le=10,
            description="默认输出图片数量（未指定时）",
        )
        OPENAI_BASE_URL: str = Field(
            default="https://api.openai.com/v1",
            description="OpenAI API Base URL",
        )
        REQUEST_TIMEOUT_SECONDS: int = Field(default=600, ge=30, le=600)
        TASK_POLL_INTERVAL_SECONDS: int = Field(default=3, ge=1, le=60)
        TASK_MAX_WAIT_SECONDS: int = Field(default=600, ge=10, le=7200)
        TASK_STORE_DIR: str = Field(
            default=".data-dev/gpt_image2_tasks",
            description="本地任务状态目录（用于任务查询/轮询）",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def _base_url(self, __request__: Optional[Request]) -> str:
        return build_base_url(__request__, self.valves.OPENWEBUI_BASE_URL)

    def _headers(self, __request__: Optional[Request]) -> dict[str, str]:
        return build_auth_headers(
            __request__,
            self.valves.OPENWEBUI_API_KEY,
            include_content_type=True,
        )

    def _headers_without_content_type(self, __request__: Optional[Request]) -> dict[str, str]:
        return build_auth_headers(
            __request__,
            self.valves.OPENWEBUI_API_KEY,
            include_content_type=False,
        )

    def _user_id(self, __user__: Optional[dict]) -> str:
        if __user__ and __user__.get("id"):
            return str(__user__.get("id"))
        return "anonymous"

    def _extract_request_id(self, text: str) -> Optional[str]:
        return extract_request_id(text)

    def _normalize_error(self, response: httpx.Response) -> dict[str, Any]:
        return normalize_httpx_error(response)

    def _read_env_value_from_file(self, key: str) -> Optional[str]:
        candidates: list[Path] = []
        env_path = (os.getenv("OPENAI_ENV_FILE") or "").strip()
        if env_path:
            candidates.append(Path(env_path).expanduser())

        candidates.extend(
            [
                Path.cwd() / "config" / "openai.env",
                Path.cwd() / "config" / "ark.dev.env",
                Path.cwd() / "config" / "ark.env",
                Path.cwd() / ".env",
            ]
        )

        for path in candidates:
            try:
                if not path.exists() or not path.is_file():
                    continue
                for raw_line in path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() != key:
                        continue
                    value = v.strip().strip('"').strip("'")
                    if value:
                        return value
            except Exception:
                continue

        return None

    def _get_openai_api_key(self) -> str:
        return (os.getenv("OPENAI_API_KEY") or self._read_env_value_from_file("OPENAI_API_KEY") or "").strip()

    def _get_openai_base_url(self) -> str:
        base_url = os.getenv("OPENAI_BASE_URL") or self._read_env_value_from_file("OPENAI_BASE_URL") or self.valves.OPENAI_BASE_URL
        raw = str(base_url).strip()
        parsed = urlsplit(raw)
        path = re.sub(r"/{2,}", "/", parsed.path or "")
        path = path.rstrip("/")
        # Normalize to API root to avoid accidentally pointing to provider web portal.
        # Do not append /v1 repeatedly when the path already ends with /v1.
        segments = [seg for seg in path.split("/") if seg]
        if not segments or segments[-1].lower() != "v1":
            path = f"{path}/v1" if path else "/v1"
        normalized = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
        return normalized.rstrip("/")

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
        image_urls: Optional[list[str]] = None,
        primary_image_url: Optional[str] = None,
        request_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        prompt_text: Optional[str] = None,
        generation_params: Optional[dict[str, Any]] = None,
        prompt_resources: Optional[list[dict[str, Any]]] = None,
        __request__: Optional[Request] = None,
    ) -> bool:
        tid = (task_id or "").strip()
        if not tid:
            return False

        payload: dict[str, Any] = {
            "task_id": tid,
            "provider": "openai_image2",
            "provider_task_id": tid,
            "tool_name": "gpt_image2_media_tool.generate_image_with_media_assets",
            "skill_name": "gpt-image2",
            "status": (status or "").strip() or "PENDING",
            "artifact_kind": "image",
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
        if image_urls is not None:
            payload["image_urls"] = list(image_urls)
        if primary_image_url:
            payload["primary_image_url"] = primary_image_url
        if request_id:
            payload["request_id"] = request_id
        if error_code:
            payload["error_code"] = error_code
        if error_message:
            payload["error_message"] = error_message
        if prompt_text is not None:
            payload["prompt_text"] = str(prompt_text)
        if generation_params is not None:
            payload["generation_params"] = generation_params
        if prompt_resources is not None:
            payload["prompt_resources"] = prompt_resources

        return await bridge_upsert(
            requester=self._request,
            payload=payload,
            __request__=__request__,
        )

    async def _download_binary(self, url: str, headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code >= 400:
            return self._normalize_error(resp)

        return {
            "ok": True,
            "status_code": resp.status_code,
            "content": resp.content,
            "content_type": resp.headers.get("content-type"),
        }

    async def _upload_generated_image(
        self,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        metadata: dict[str, Any],
        __request__: Optional[Request],
    ) -> dict[str, Any]:
        base_url = self._base_url(__request__)
        url = f"{base_url}/api/v1/files/?process=false&process_in_background=false"
        headers = self._headers_without_content_type(__request__)

        data = {
            "metadata": json.dumps(metadata, ensure_ascii=False),
        }

        files = {
            "file": (filename, image_bytes, content_type or "image/png"),
        }

        async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)

        if resp.status_code >= 400:
            return self._normalize_error(resp)

        try:
            payload = resp.json()
        except Exception:
            payload = {"raw_text": resp.text}

        file_id = payload.get("id") if isinstance(payload, dict) else None
        if not file_id:
            return {
                "ok": False,
                "status_code": 502,
                "error_code": "InvalidUploadResponse",
                "error_message": "Upload succeeded but file id is missing",
                "request_id": None,
            }

        return {
            "ok": True,
            "file_id": file_id,
            "url": f"{base_url}/api/v1/files/{file_id}/content",
            "raw_response": payload,
        }

    async def _upload_generated_image_with_context(
        self,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        metadata: dict[str, Any],
        base_url: str,
        authorization: str = "",
    ) -> dict[str, Any]:
        url = f"{str(base_url).rstrip('/')}/api/v1/files/?process=false&process_in_background=false"
        headers: dict[str, str] = {}
        if authorization:
            headers["Authorization"] = authorization

        data = {
            "metadata": json.dumps(metadata, ensure_ascii=False),
        }
        files = {
            "file": (filename, image_bytes, content_type or "image/png"),
        }

        async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)

        if resp.status_code >= 400:
            return self._normalize_error(resp)

        try:
            payload = resp.json()
        except Exception:
            payload = {"raw_text": resp.text}

        file_id = payload.get("id") if isinstance(payload, dict) else None
        if not file_id:
            return {
                "ok": False,
                "status_code": 502,
                "error_code": "InvalidUploadResponse",
                "error_message": "Upload succeeded but file id is missing",
                "request_id": None,
            }

        return {
            "ok": True,
            "file_id": file_id,
            "url": f"{str(base_url).rstrip('/')}/api/v1/files/{file_id}/content",
            "raw_response": payload,
        }

    def _extract_media_asset_references(self, prompt: str) -> list[str]:
        return extract_media_asset_references(prompt)

    def _replace_refs_with_characters(self, prompt: str, references: list[str]) -> str:
        updated = prompt or ""
        for idx, ref in enumerate(references, start=1):
            updated = updated.replace(f"%{ref}", f"character{idx}")
        return updated

    def _media_asset_reference_candidates(self, item: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("relative_path", "display_name", "original_filename"):
            value = str(item.get(key) or "").strip()
            if value:
                candidates.append(value)
        return list(dict.fromkeys(candidates))

    def _compact_media_asset_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return compact_media_asset_item(item)

    def _normalize_http_url(self, value: Any) -> Optional[str]:
        url = str(value or "").strip()
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            return None
        return url

    def _to_image_url_markdown_or_na(self, value: Any) -> str:
        url = self._normalize_http_url(value)
        if not url:
            return "暂无"
        return f"[查看图片]({url})"

    def _normalize_model(self, value: str) -> tuple[Optional[str], Optional[str]]:
        model = (value or "").strip() or self.valves.DEFAULT_OPENAI_IMAGE_MODEL
        if model != "gpt-image-2":
            return None, "Only gpt-image-2 is supported in this tool."
        return model, None

    def _normalize_size(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = (value or "").strip().lower()
        if not raw:
            return None, None
        allowed = {"auto", "1024x1024", "1536x1024", "1024x1536"}
        if raw in allowed:
            return raw, None
        return None, "size must be one of: auto, 1024x1024, 1536x1024, 1024x1536"

    def _normalize_quality(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = (value or "").strip().lower()
        if not raw:
            return None, None
        allowed = {"auto", "low", "medium", "high"}
        if raw in allowed:
            return raw, None
        return None, "quality must be one of: auto, low, medium, high"

    def _normalize_background(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = (value or "").strip().lower()
        if not raw:
            return None, None
        allowed = {"auto", "transparent", "opaque"}
        if raw in allowed:
            return raw, None
        return None, "background must be one of: auto, transparent, opaque"

    def _normalize_output_format(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = (value or "").strip().lower()
        if not raw:
            return None, None
        allowed = {"png", "jpeg", "webp"}
        if raw in allowed:
            return raw, None
        return None, "output_format must be one of: png, jpeg, webp"

    def _normalize_moderation(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = (value or "").strip().lower()
        if not raw:
            return None, None
        allowed = {"auto", "low"}
        if raw in allowed:
            return raw, None
        return None, "moderation must be one of: auto, low"

    def _normalize_n(self, value: Optional[int]) -> tuple[Optional[int], Optional[str]]:
        if value is None:
            return None, None
        n = int(value)
        if 1 <= n <= 10:
            return n, None
        return None, "n must be an integer between 1 and 10"

    def _normalize_output_compression(self, value: Optional[int]) -> tuple[Optional[int], Optional[str]]:
        if value is None:
            return None, None
        comp = int(value)
        if 0 <= comp <= 100:
            return comp, None
        return None, "output_compression must be an integer between 0 and 100"

    def _task_store_root(self) -> Path:
        root = Path(self.valves.TASK_STORE_DIR)
        if not root.is_absolute():
            root = Path.cwd() / root
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _user_task_dir(self, user_id: str) -> Path:
        path = self._task_store_root() / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _task_file_path(self, user_id: str, task_id: str) -> Path:
        return self._user_task_dir(user_id) / f"{task_id}.json"

    def _save_task_record(self, user_id: str, task_id: str, payload: dict[str, Any]) -> None:
        self._task_file_path(user_id, task_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_task_record(self, user_id: str, task_id: str) -> Optional[dict[str, Any]]:
        path = self._task_file_path(user_id, task_id)
        if not path.exists() or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _list_task_records(self, user_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for p in sorted(self._user_task_dir(user_id).glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                continue
        return rows

    def _compact_task_item(self, item: dict[str, Any]) -> dict[str, Any]:
        image_urls = [u for u in (item.get("image_urls") or []) if isinstance(u, str)]
        primary = self._normalize_http_url(item.get("image_url") or (image_urls[0] if image_urls else None))
        provider_debug = item.get("provider_debug") if isinstance(item.get("provider_debug"), dict) else None
        return {
            "task_id": item.get("task_id"),
            "response_id": item.get("response_id") or item.get("task_id"),
            "chat_id": item.get("chat_id"),
            "model": item.get("model"),
            "status": item.get("status"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "references": item.get("references") or [],
            "image_url": primary,
            "image_url_markdown": self._to_image_url_markdown_or_na(primary),
            "image_urls": image_urls,
            "error_code": item.get("error_code"),
            "error_message": item.get("error_message"),
            "request_id": item.get("request_id"),
            "provider_debug": provider_debug,
        }

    def _new_task_id(self) -> str:
        return f"gptimg2_{int(time.time())}_{uuid.uuid4().hex[:10]}"

    def _schedule_generation_task(
        self,
        *,
        user_id: str,
        task_id: str,
        initial_record: dict[str, Any],
        payload_common: dict[str, Any],
        reference_files: list[tuple[str, bytes, str]],
        transformed_prompt: str,
        refs: list[str],
        openai_base_url: str,
        openai_api_key: str,
        internal_base_url: str,
        internal_authorization: str,
    ) -> tuple[bool, Optional[str]]:
        # Use a daemon thread to avoid request-lifecycle loop cancellations
        # that may leave tasks stuck in PENDING.
        def _runner() -> None:
            try:
                asyncio.run(
                    self._execute_generation_task(
                        user_id=user_id,
                        task_id=task_id,
                        initial_record=initial_record,
                        payload_common=payload_common,
                        reference_files=reference_files,
                        transformed_prompt=transformed_prompt,
                        refs=refs,
                        openai_base_url=openai_base_url,
                        openai_api_key=openai_api_key,
                        internal_base_url=internal_base_url,
                        internal_authorization=internal_authorization,
                    )
                )
            except Exception as e:
                message = str(e).strip() or e.__class__.__name__
                failed = {
                    **initial_record,
                    "status": "FAILED",
                    "updated_at": int(time.time()),
                    "error_code": "BackgroundWorkerFailed",
                    "error_message": message,
                }
                self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)

        try:
            worker = threading.Thread(target=_runner, name=f"gptimg2-{task_id}", daemon=True)
            worker.start()
            return True, None
        except Exception as e:
            return False, (str(e).strip() or e.__class__.__name__)

    def _maybe_finalize_stale_task(
        self,
        *,
        user_id: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        status = str(item.get("status") or "").strip().upper()
        if status not in {"PENDING", "RUNNING"}:
            return item

        task_id = str(item.get("task_id") or "").strip()
        if not task_id:
            return item

        created_at = int(item.get("created_at") or 0)
        updated_at = int(item.get("updated_at") or created_at or 0)
        now_ts = int(time.time())

        stale_after_seconds = max(
            int(self.valves.REQUEST_TIMEOUT_SECONDS) + 30,
            int(self.valves.TASK_MAX_WAIT_SECONDS),
        )
        if now_ts - updated_at < stale_after_seconds:
            return item

        failed = {
            **item,
            "status": "FAILED",
            "updated_at": now_ts,
            "error_code": item.get("error_code") or "GenerationTaskStale",
            "error_message": item.get("error_message")
            or f"Task stayed in {status} for over {stale_after_seconds}s without worker update. Please resubmit.",
        }
        self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
        return failed

    def _content_type_from_format(self, fmt: str) -> str:
        value = (fmt or "").strip().lower()
        if value == "jpeg":
            return "image/jpeg"
        if value == "webp":
            return "image/webp"
        return "image/png"

    async def _execute_generation_task(
        self,
        *,
        user_id: str,
        task_id: str,
        initial_record: dict[str, Any],
        payload_common: dict[str, Any],
        reference_files: list[tuple[str, bytes, str]],
        transformed_prompt: str,
        refs: list[str],
        openai_base_url: str,
        openai_api_key: str,
        internal_base_url: str,
        internal_authorization: str,
    ) -> None:
        endpoint_path = "/images/edits" if reference_files else "/images/generations"
        request_url = f"{openai_base_url}{endpoint_path}"

        provider_debug = initial_record.get("provider_debug") if isinstance(initial_record.get("provider_debug"), dict) else {}
        provider_debug = {
            **provider_debug,
            "mode": "edits" if reference_files else "generations",
            "endpoint_path": endpoint_path,
            "request_url": request_url,
            "worker_started_at": int(time.time()),
            "worker_name": threading.current_thread().name,
        }
        running_record = {
            **initial_record,
            "status": "RUNNING",
            "updated_at": int(time.time()),
            "provider_debug": provider_debug,
        }
        self._save_task_record(user_id=user_id, task_id=task_id, payload=running_record)
        start_monotonic = time.monotonic()

        try:
            openai_headers = {
                "Authorization": f"Bearer {openai_api_key}",
            }
            if reference_files:
                form_data: dict[str, str] = {}
                for k, v in payload_common.items():
                    form_data[k] = str(v)

                files_payload: list[tuple[str, tuple[str, bytes, str]]] = []
                multi_inputs = len(reference_files) > 1
                for filename, content, content_type in reference_files:
                    field_name = "image[]" if multi_inputs else "image"
                    files_payload.append((field_name, (filename, content, content_type)))

                async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
                    resp = await client.post(
                        request_url,
                        headers=openai_headers,
                        data=form_data,
                        files=files_payload,
                    )
            else:
                headers_with_json = {**openai_headers, "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
                    resp = await client.post(
                        request_url,
                        headers=headers_with_json,
                        json=payload_common,
                    )

            if resp.status_code >= 400:
                err = self._normalize_error(resp)
                response_content_type = str(resp.headers.get("content-type") or "")
                failed = {
                    **running_record,
                    "status": "FAILED",
                    "updated_at": int(time.time()),
                    "error_code": err.get("error_code"),
                    "error_message": err.get("error_message"),
                    "request_id": err.get("request_id"),
                    "provider_debug": {
                        **provider_debug,
                        "http_status": resp.status_code,
                        "response_content_type": response_content_type,
                    },
                }
                self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
                return

            try:
                response_json = resp.json()
            except Exception:
                response_json = {"raw_text": resp.text}

            if not isinstance(response_json, dict):
                response_json = {"raw_text": str(response_json)}

            provider_error = response_json.get("error")
            if isinstance(provider_error, dict):
                err_code = provider_error.get("code")
                err_message = provider_error.get("message") or "Provider returned error payload"
                failed = {
                    **running_record,
                    "status": "FAILED",
                    "updated_at": int(time.time()),
                    "error_code": err_code or "ProviderError",
                    "error_message": err_message,
                    "request_id": provider_error.get("request_id"),
                    "raw_response": response_json,
                    "provider_debug": {
                        **provider_debug,
                        "http_status": resp.status_code,
                        "response_content_type": str(resp.headers.get("content-type") or ""),
                    },
                }
                self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
                return

            data_rows = response_json.get("data")
            if not isinstance(data_rows, list):
                data_rows = []

            if not data_rows:
                raw_text = str(response_json.get("raw_text") or "")
                lowered = raw_text.lower()
                looks_like_html = "<html" in lowered or "<!doctype html" in lowered
                if looks_like_html:
                    failed = {
                        **running_record,
                        "status": "FAILED",
                        "updated_at": int(time.time()),
                        "error_code": "InvalidProviderResponse",
                        "error_message": "Provider returned HTML instead of OpenAI image JSON. Check OPENAI_BASE_URL points to API /v1 endpoint.",
                        "raw_response": response_json,
                        "provider_debug": {
                            **provider_debug,
                            "http_status": resp.status_code,
                            "response_content_type": str(resp.headers.get("content-type") or ""),
                        },
                    }
                    self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
                    return

            output_format_value = str(payload_common.get("output_format") or "png")
            generated_urls: list[str] = []
            upload_errors: list[dict[str, Any]] = []

            for idx, row in enumerate(data_rows, start=1):
                if not isinstance(row, dict):
                    continue

                image_bytes: Optional[bytes] = None
                image_content_type: str = self._content_type_from_format(output_format_value)

                source_url = self._normalize_http_url(row.get("url"))
                if source_url:
                    downloaded = await self._download_binary(source_url, headers=openai_headers)
                    if downloaded.get("ok"):
                        image_bytes = downloaded.get("content") or b""
                        image_content_type = str(downloaded.get("content_type") or image_content_type).split(";")[0].strip()
                    else:
                        upload_errors.append(
                            {
                                "index": idx,
                                "error_code": downloaded.get("error_code"),
                                "error_message": downloaded.get("error_message") or downloaded.get("error"),
                            }
                        )
                        continue
                else:
                    b64_text = row.get("b64_json")
                    if isinstance(b64_text, str) and b64_text.strip():
                        try:
                            image_bytes = base64.b64decode(b64_text)
                        except Exception:
                            image_bytes = None

                if not image_bytes:
                    upload_errors.append(
                        {
                            "index": idx,
                            "error_code": "InvalidImagePayload",
                            "error_message": "Missing image bytes from provider response",
                        }
                    )
                    continue

                ext = "png"
                if output_format_value == "jpeg":
                    ext = "jpg"
                elif output_format_value == "webp":
                    ext = "webp"

                upload_result = await self._upload_generated_image_with_context(
                    image_bytes=image_bytes,
                    filename=f"gpt-image-2-{task_id}-{idx}.{ext}",
                    content_type=image_content_type,
                    metadata={
                        "source": "gpt-image-2",
                        "task_id": task_id,
                        "index": idx,
                        "prompt": transformed_prompt,
                        "references": refs,
                    },
                    base_url=internal_base_url,
                    authorization=internal_authorization,
                )

                if not upload_result.get("ok"):
                    upload_errors.append(
                        {
                            "index": idx,
                            "error_code": upload_result.get("error_code"),
                            "error_message": upload_result.get("error_message") or upload_result.get("error"),
                        }
                    )
                    continue

                generated_urls.append(upload_result.get("url"))

            if not generated_urls:
                failed = {
                    **running_record,
                    "status": "FAILED",
                    "updated_at": int(time.time()),
                    "error_code": "ImageUploadFailed",
                    "error_message": "No generated images were uploaded successfully",
                    "request_id": self._extract_request_id(json.dumps(response_json, ensure_ascii=False)),
                    "raw_response": response_json,
                    "provider_debug": {
                        **provider_debug,
                        "http_status": resp.status_code,
                        "response_content_type": str(resp.headers.get("content-type") or ""),
                    },
                }
                self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
                return

            primary_url = generated_urls[0]
            final_record = {
                **running_record,
                "status": "SUCCEEDED",
                "updated_at": int(time.time()),
                "image_url": primary_url,
                "image_urls": generated_urls,
                "request_id": response_json.get("request_id") if isinstance(response_json, dict) else None,
                "raw_response": response_json,
                "error_code": None,
                "error_message": None,
                "provider_debug": {
                    **provider_debug,
                    "http_status": resp.status_code,
                    "response_content_type": str(resp.headers.get("content-type") or ""),
                },
            }
            if upload_errors:
                final_record["upload_errors"] = upload_errors
            self._save_task_record(user_id=user_id, task_id=task_id, payload=final_record)
            return

        except httpx.TimeoutException:
            timeout_seconds = int(self.valves.REQUEST_TIMEOUT_SECONDS)
            elapsed = int(time.monotonic() - start_monotonic)
            message = f"OpenAI image request timed out after {timeout_seconds}s"
            failed = {
                **running_record,
                "status": "FAILED",
                "updated_at": int(time.time()),
                "error_code": "GenerationRequestTimeout",
                "error_message": message,
                "request_id": None,
                "raw_response": {"elapsed_seconds": elapsed},
                "provider_debug": {
                    **provider_debug,
                    "http_status": None,
                    "response_content_type": None,
                },
            }
            self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
            return
        except Exception as e:
            message = str(e).strip() or e.__class__.__name__
            failed = {
                **running_record,
                "status": "FAILED",
                "updated_at": int(time.time()),
                "error_code": "GenerateRequestFailed",
                "error_message": message,
                "provider_debug": {
                    **provider_debug,
                    "http_status": None,
                    "response_content_type": None,
                },
            }
            self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
            return

    async def list_media_assets(
        self,
        media_type: str = "",
        status: str = "active",
        chat_id: str = "",
        limit: int = 100,
        offset: int = 0,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        列出当前用户媒体素材（共享 media-assets 素材池）。
        """
        limit = max(1, min(int(limit or 100), 200))
        offset = max(0, int(offset or 0))
        query: dict[str, Any] = {"limit": limit, "offset": offset}
        if (media_type or "").strip():
            query["media_type"] = media_type.strip()
        if (status or "").strip():
            query["status"] = status.strip()
        if (chat_id or "").strip():
            query["chat_id"] = chat_id.strip()

        path = f"/api/v1/media-assets/?{urlencode(query)}"
        result = await self._request("GET", path, __request__)
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        rows = []
        for item in result.get("data", []):
            if isinstance(item, dict):
                rows.append(self._compact_media_asset_item(item))

        return json.dumps({"ok": True, "assets": rows, "count": len(rows)}, ensure_ascii=False)

    async def get_media_asset(
        self,
        asset_id: str,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        查询单个媒体素材详情。
        """
        aid = (asset_id or "").strip()
        if not aid:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "asset_id is required",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        result = await self._request("GET", f"/api/v1/media-assets/{aid}", __request__)
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        row = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
        return json.dumps({"ok": True, "asset": self._compact_media_asset_item(row)}, ensure_ascii=False)

    async def get_media_asset_url(
        self,
        asset_id: str,
        expires_in: int = 3600,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        获取媒体素材临时访问地址（TOS 预签名 URL）。
        """
        aid = (asset_id or "").strip()
        if not aid:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "asset_id is required",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        ttl = max(60, min(int(expires_in or 3600), 7 * 24 * 3600))
        path = f"/api/v1/media-assets/{aid}/url?{urlencode({'expires_in': ttl})}"
        result = await self._request("GET", path, __request__)
        return json.dumps(result, ensure_ascii=False)

    async def resolve_media_asset_references(
        self,
        prompt: str,
        chat_id: str = "",
        status: str = "active",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        解析 prompt 中 `%素材路径` 引用，返回命中素材、缺失和重名冲突。
        """
        page_size = 200
        offset = 0
        assets_raw: list[dict[str, Any]] = []

        while True:
            query: dict[str, Any] = {"limit": page_size, "offset": offset}
            if (status or "").strip():
                query["status"] = status.strip()
            if (chat_id or "").strip():
                query["chat_id"] = chat_id.strip()

            path = f"/api/v1/media-assets/?{urlencode(query)}"
            result = await self._request("GET", path, __request__)
            if not result.get("ok"):
                return json.dumps(result, ensure_ascii=False)

            page_rows = [row for row in (result.get("data") or []) if isinstance(row, dict)]
            assets_raw.extend(page_rows)
            if len(page_rows) < page_size:
                break

            offset += page_size
            if offset >= 4000:
                break

        refs = self._extract_media_asset_references(prompt)

        alias_to_canonical: dict[str, str] = {}
        canonical_to_item: dict[str, dict[str, Any]] = {}
        basename_to_canonical: dict[str, list[str]] = {}
        available_refs: list[str] = []

        for item in assets_raw:
            candidates = self._media_asset_reference_candidates(item)
            if not candidates:
                continue

            canonical = candidates[0]
            if canonical not in canonical_to_item:
                canonical_to_item[canonical] = item
                available_refs.append(canonical)

            for ref in candidates:
                if ref and ref not in alias_to_canonical:
                    alias_to_canonical[ref] = canonical

            basename = Path(canonical).name
            if basename:
                rows = basename_to_canonical.setdefault(basename, [])
                if canonical not in rows:
                    rows.append(canonical)

        missing: list[str] = []
        ambiguous: list[dict[str, Any]] = []
        resolved_refs: list[str] = []
        seen_resolved: set[str] = set()

        for raw_ref in refs:
            if raw_ref in alias_to_canonical:
                canonical = alias_to_canonical[raw_ref]
                if canonical not in seen_resolved:
                    resolved_refs.append(canonical)
                    seen_resolved.add(canonical)
                continue

            basename = Path(raw_ref).name
            dupes = basename_to_canonical.get(basename, []) if basename else []
            if basename and len(dupes) > 1:
                ambiguous.append(
                    {
                        "reference": raw_ref,
                        "candidates": sorted(dupes),
                        "guidance": "Use full relative_path to disambiguate",
                    }
                )
                continue

            missing.append(raw_ref)

        cleaned_prompt = prompt or ""
        for ref in refs:
            cleaned_prompt = cleaned_prompt.replace(f"%{ref}", ref)

        resolved_assets = [
            self._compact_media_asset_item(canonical_to_item[ref])
            for ref in resolved_refs
            if ref in canonical_to_item
        ]

        return json.dumps(
            {
                "ok": True,
                "data": {
                    "references": resolved_refs,
                    "raw_references": refs,
                    "missing_references": missing,
                    "ambiguous_references": ambiguous,
                    "available_references": sorted(available_refs),
                    "cleaned_prompt": cleaned_prompt,
                    "assets": resolved_assets,
                },
            },
            ensure_ascii=False,
        )

    async def generate_image_with_media_assets(
        self,
        prompt: str,
        model: str = "",
        size: str = "",
        quality: str = "",
        background: str = "",
        output_format: str = "",
        output_compression: Optional[int] = None,
        n: Optional[int] = None,
        moderation: str = "",
        chat_id: str = "",
        url_expires_in: int = 3600,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        使用 `%素材路径` 引用图片素材，调用 OpenAI gpt-image-2 生成图片。
        - 含引用时：调用 /images/edits（多图参考融合）
        - 无引用时：调用 /images/generations（纯文本）
        """
        user_id = self._user_id(__user__)
        now_ts = int(time.time())

        prompt_text = (prompt or "").strip()
        if not prompt_text:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "prompt is required",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        model_id, model_err = self._normalize_model(model)
        if model_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": model_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        size_input = size if str(size or "").strip() else self.valves.DEFAULT_IMAGE_SIZE
        size_value, size_err = self._normalize_size(size_input)
        if size_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": size_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        quality_input = quality if str(quality or "").strip() else self.valves.DEFAULT_IMAGE_QUALITY
        quality_value, quality_err = self._normalize_quality(quality_input)
        if quality_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": quality_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        background_input = background if str(background or "").strip() else self.valves.DEFAULT_IMAGE_BACKGROUND
        background_value, background_err = self._normalize_background(background_input)
        if background_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": background_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        output_format_input = output_format if str(output_format or "").strip() else self.valves.DEFAULT_IMAGE_OUTPUT_FORMAT
        output_format_value, output_format_err = self._normalize_output_format(output_format_input)
        if output_format_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": output_format_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        n_input = n if n is not None else self.valves.DEFAULT_IMAGE_N
        n_value, n_err = self._normalize_n(n_input)
        if n_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": n_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        moderation_value, moderation_err = self._normalize_moderation(moderation)
        if moderation_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": moderation_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        output_compression_value, compression_err = self._normalize_output_compression(output_compression)
        if compression_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": compression_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        if output_compression_value is not None and output_format_value not in {"jpeg", "webp"}:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "output_compression is only valid when output_format is jpeg or webp",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        resolve_raw = await self.resolve_media_asset_references(
            prompt=prompt_text,
            chat_id=chat_id,
            __request__=__request__,
            __user__=__user__,
        )

        try:
            resolve_payload = json.loads(resolve_raw)
        except Exception:
            resolve_payload = {
                "ok": False,
                "status_code": 500,
                "error_code": "InvalidToolPayload",
                "error_message": "Failed to parse media reference resolve payload",
                "request_id": None,
            }

        if not resolve_payload.get("ok"):
            return json.dumps(resolve_payload, ensure_ascii=False)

        resolve_data = resolve_payload.get("data", {}) if isinstance(resolve_payload.get("data"), dict) else {}
        refs = list(resolve_data.get("references") or [])
        missing = list(resolve_data.get("missing_references") or [])
        ambiguous = list(resolve_data.get("ambiguous_references") or [])

        if missing or ambiguous:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "MissingMediaAssetReferences",
                    "error_message": "Missing or ambiguous referenced media assets",
                    "request_id": None,
                    "missing_references": missing,
                    "ambiguous_references": ambiguous,
                    "available_references": resolve_data.get("available_references") or [],
                },
                ensure_ascii=False,
            )

        transformed_prompt = self._replace_refs_with_characters(prompt=prompt_text, references=refs)

        openai_api_key = self._get_openai_api_key()
        if not openai_api_key:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": None,
                    "error_message": "OPENAI_API_KEY is not configured",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        task_id = self._new_task_id()
        generation_params_for_task: dict[str, Any] = {
            "size": size_value,
            "quality": quality_value,
            "background": background_value,
            "output_format": output_format_value,
            "n": n_value,
        }
        if output_compression_value is not None:
            generation_params_for_task["output_compression"] = output_compression_value
        if moderation_value is not None:
            generation_params_for_task["moderation"] = moderation_value
        prompt_resources_for_task: list[dict[str, str]] = []
        initial_record = {
            "task_id": task_id,
            "response_id": task_id,
            "status": "PENDING",
            "created_at": now_ts,
            "updated_at": now_ts,
            "model": model_id,
            "chat_id": chat_id,
            "prompt": transformed_prompt,
            "references": refs,
            "image_url": None,
            "image_urls": [],
            "size": size_value,
            "quality": quality_value,
            "background": background_value,
            "output_format": output_format_value,
            "output_compression": output_compression_value,
            "moderation": moderation_value,
            "n": n_value,
            "error_code": None,
            "error_message": None,
            "request_id": None,
            "raw_response": None,
        }
        self._save_task_record(user_id=user_id, task_id=task_id, payload=initial_record)
        try:
            await self._bridge_upsert_task(
                task_id=task_id,
                status="PENDING",
                model=model_id,
                chat_id=chat_id,
                references=refs,
                raw_submit_response=initial_record,
                raw_last_response=initial_record,
                prompt_text=prompt_text,
                generation_params=generation_params_for_task,
                prompt_resources=prompt_resources_for_task,
                __request__=__request__,
            )
        except Exception:
            pass

        resolved_assets = [item for item in (resolve_data.get("assets") or []) if isinstance(item, dict)]
        character_mapping: list[dict[str, Any]] = []
        reference_files: list[tuple[str, bytes, str]] = []

        if refs:
            if len(resolved_assets) > 16:
                failed = {
                    **initial_record,
                    "status": "FAILED",
                    "updated_at": int(time.time()),
                    "error_code": "InvalidParameter",
                    "error_message": "gpt-image-2 supports at most 16 reference images in edits",
                }
                self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": "gpt-image-2 supports at most 16 reference images in edits",
                        "request_id": None,
                        "task_id": task_id,
                        "response_id": task_id,
                        "status": "FAILED",
                    },
                    ensure_ascii=False,
                )

            unsupported_refs: list[dict[str, Any]] = []
            for idx, asset in enumerate(resolved_assets, start=1):
                asset_id = str(asset.get("asset_id") or "").strip()
                media_type = str(asset.get("media_type") or "").strip().lower()
                ref_name = str(asset.get("relative_path") or asset.get("display_name") or asset.get("original_filename") or "")
                if media_type != "image":
                    unsupported_refs.append(
                        {
                            "reference": ref_name,
                            "asset_id": asset_id,
                            "media_type": media_type,
                            "error_message": "gpt-image-2 reference mode supports image assets only.",
                        }
                    )
                    continue

                url_raw = await self.get_media_asset_url(
                    asset_id=asset_id,
                    expires_in=url_expires_in,
                    __request__=__request__,
                    __user__=__user__,
                )
                try:
                    url_payload = json.loads(url_raw)
                except Exception:
                    url_payload = {
                        "ok": False,
                        "status_code": 500,
                        "error_code": "InvalidToolPayload",
                        "error_message": "Failed to parse media asset url payload",
                        "request_id": None,
                    }

                if not url_payload.get("ok"):
                    unsupported_refs.append(
                        {
                            "reference": ref_name,
                            "asset_id": asset_id,
                            "media_type": media_type,
                            "error_message": url_payload.get("error_message") or url_payload.get("error"),
                        }
                    )
                    continue

                url_data = url_payload.get("data", {}) if isinstance(url_payload.get("data"), dict) else {}
                media_url = url_data.get("url")
                media_url = self._normalize_http_url(media_url)
                if not media_url:
                    unsupported_refs.append(
                        {
                            "reference": ref_name,
                            "asset_id": asset_id,
                            "media_type": media_type,
                            "error_message": "Invalid media asset url",
                        }
                    )
                    continue

                prompt_resources_for_task.append(
                    {"name": ref_name or f"resource_{idx}", "url": media_url}
                )
                download = await self._download_binary(media_url)
                if not download.get("ok"):
                    unsupported_refs.append(
                        {
                            "reference": ref_name,
                            "asset_id": asset_id,
                            "media_type": media_type,
                            "error_message": download.get("error_message") or download.get("error"),
                        }
                    )
                    continue

                original_name = str(asset.get("original_filename") or Path(ref_name).name or f"reference_{idx}.png")
                ext = Path(original_name).suffix.lower()
                if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
                    ext = ".png"
                filename = f"ref_{idx}{ext}"
                content_type = str(download.get("content_type") or "").split(";")[0].strip().lower()
                if content_type not in {"image/png", "image/jpeg", "image/webp"}:
                    content_type = "image/png" if ext == ".png" else "image/jpeg" if ext in {".jpg", ".jpeg"} else "image/webp"

                reference_files.append((filename, download.get("content") or b"", content_type))
                character_mapping.append(
                    {
                        "character": f"character{idx}",
                        "reference": ref_name,
                        "asset_id": asset_id,
                    }
                )

            if prompt_resources_for_task:
                try:
                    await self._bridge_upsert_task(
                        task_id=task_id,
                        status="PENDING",
                        prompt_resources=prompt_resources_for_task,
                        __request__=__request__,
                    )
                except Exception:
                    pass

            if unsupported_refs:
                failed = {
                    **initial_record,
                    "status": "FAILED",
                    "updated_at": int(time.time()),
                    "error_code": "UnsupportedReferences",
                    "error_message": "Some referenced assets are unsupported or unavailable",
                }
                self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "UnsupportedReferences",
                        "error_message": "Some referenced assets are unsupported or unavailable",
                        "request_id": None,
                        "task_id": task_id,
                        "response_id": task_id,
                        "status": "FAILED",
                        "unsupported_references": unsupported_refs,
                    },
                    ensure_ascii=False,
                )

        openai_base_url = self._get_openai_base_url()
        provider_mode = "edits" if refs else "generations"
        provider_endpoint_path = "/images/edits" if refs else "/images/generations"
        provider_request_url = f"{openai_base_url}{provider_endpoint_path}"
        payload_common: dict[str, Any] = {
            "model": model_id,
            "prompt": transformed_prompt,
            "n": n_value,
            "size": size_value,
            "quality": quality_value,
            "background": background_value,
            "output_format": output_format_value,
        }
        if output_compression_value is not None:
            payload_common["output_compression"] = output_compression_value
        if moderation_value is not None:
            payload_common["moderation"] = moderation_value

        initial_record["provider_debug"] = {
            "base_url": openai_base_url,
            "mode": provider_mode,
            "endpoint_path": provider_endpoint_path,
            "request_url": provider_request_url,
            "http_status": None,
            "response_content_type": None,
        }
        self._save_task_record(user_id=user_id, task_id=task_id, payload=initial_record)

        internal_headers = self._headers(__request__)
        internal_auth = str(internal_headers.get("Authorization") or "")
        internal_base_url = self._base_url(__request__)

        ok, schedule_error = self._schedule_generation_task(
            user_id=user_id,
            task_id=task_id,
            initial_record=initial_record,
            payload_common=payload_common,
            reference_files=reference_files,
            transformed_prompt=transformed_prompt,
            refs=refs,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
            internal_base_url=internal_base_url,
            internal_authorization=internal_auth,
        )
        if not ok:
            message = str(schedule_error or "TaskScheduleFailed")
            failed = {
                **initial_record,
                "status": "FAILED",
                "updated_at": int(time.time()),
                "error_code": "TaskScheduleFailed",
                "error_message": message,
            }
            self._save_task_record(user_id=user_id, task_id=task_id, payload=failed)
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 500,
                    "error_code": "TaskScheduleFailed",
                    "error_message": message,
                    "request_id": None,
                    "task_id": task_id,
                    "response_id": task_id,
                    "status": "FAILED",
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "ok": True,
                "references": refs,
                "character_mapping": character_mapping,
                "response_id": task_id,
                "task_id": task_id,
                "status": "PENDING",
                "image_url": None,
                "image_url_markdown": "暂无",
                "image_urls": [],
                "raw_response": None,
                "provider_debug": initial_record.get("provider_debug"),
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
        """
        列出当前用户的 gpt-image-2 任务。
        """
        user_id = self._user_id(__user__)
        desired_status = (status or "").strip().lower()
        chat_id = (chat_id or "").strip()
        limit = max(1, min(int(limit or 50), 200))

        rows = self._list_task_records(user_id)
        filtered: list[dict[str, Any]] = []
        for item in rows:
            item = self._maybe_finalize_stale_task(user_id=user_id, item=item)
            s = str(item.get("status") or "").strip().lower()
            if desired_status and s != desired_status:
                continue
            if chat_id and str(item.get("chat_id") or "").strip() != chat_id:
                continue
            filtered.append(self._compact_task_item(item))

        return json.dumps({"ok": True, "tasks": filtered[:limit], "count": len(filtered[:limit])}, ensure_ascii=False)

    async def get_generation_task_status(
        self,
        task_id: str,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        查询 gpt-image-2 任务状态。
        """
        tid = (task_id or "").strip()
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

        user_id = self._user_id(__user__)
        item = self._load_task_record(user_id=user_id, task_id=tid)
        if not item:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 404,
                    "error_code": "TaskNotFound",
                    "error_message": "Task not found",
                    "request_id": None,
                    "task_id": tid,
                },
                ensure_ascii=False,
            )

        item = self._maybe_finalize_stale_task(user_id=user_id, item=item)
        compact = self._compact_task_item(item)
        payload = {
            "ok": True,
            "task_id": compact.get("task_id") or tid,
            "response_id": compact.get("response_id") or tid,
            "status": compact.get("status"),
            "image_url": compact.get("image_url"),
            "image_url_markdown": compact.get("image_url_markdown"),
            "image_urls": compact.get("image_urls") or [],
            "request_id": compact.get("request_id"),
            "error_code": compact.get("error_code"),
            "error_message": compact.get("error_message"),
            "created_at": compact.get("created_at"),
            "updated_at": compact.get("updated_at"),
            "raw_response": item.get("raw_response"),
            "references": compact.get("references") or [],
            "provider_debug": compact.get("provider_debug"),
        }
        return json.dumps(payload, ensure_ascii=False)

    async def wait_generation_task(
        self,
        task_id: str,
        timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        轮询任务直到终态或超时。
        """
        tid = (task_id or "").strip()
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

        timeout = int(timeout_seconds or self.valves.TASK_MAX_WAIT_SECONDS)
        poll = int(poll_interval_seconds or self.valves.TASK_POLL_INTERVAL_SECONDS)
        timeout = max(10, min(timeout, 7200))
        poll = max(1, min(poll, 60))

        start = time.monotonic()
        last_payload: dict[str, Any] = {}
        terminal = {"succeeded", "failed", "canceled", "cancelled", "unknown"}

        while True:
            status_raw = await self.get_generation_task_status(
                task_id=tid,
                __request__=__request__,
                __user__=__user__,
            )

            try:
                payload = json.loads(status_raw)
            except Exception:
                payload = {
                    "ok": False,
                    "status_code": 500,
                    "error_code": "InvalidToolPayload",
                    "error_message": "Failed to parse task status payload",
                    "request_id": None,
                    "task_id": tid,
                }
                return json.dumps(payload, ensure_ascii=False)

            last_payload = payload
            if not payload.get("ok"):
                payload["elapsed_seconds"] = int(time.monotonic() - start)
                return json.dumps(payload, ensure_ascii=False)

            status = str(payload.get("status") or "").strip().lower()
            if status in terminal:
                payload["elapsed_seconds"] = int(time.monotonic() - start)
                if status == "succeeded":
                    payload["ok"] = True
                    return json.dumps(payload, ensure_ascii=False)

                payload["ok"] = False
                payload["status_code"] = 200
                payload["error_code"] = payload.get("error_code") or "GenerationTaskFailed"
                payload["error_message"] = payload.get("error_message") or f"Task ended with status={status}"
                return json.dumps(payload, ensure_ascii=False)

            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 408,
                        "task_id": tid,
                        "status": last_payload.get("status"),
                        "image_url": last_payload.get("image_url"),
                        "image_url_markdown": self._to_image_url_markdown_or_na(last_payload.get("image_url")),
                        "raw_response": last_payload.get("raw_response"),
                        "error_code": "GenerationTaskPollingTimeout",
                        "error_message": "Generation task polling timeout",
                        "request_id": None,
                        "elapsed_seconds": int(elapsed),
                    },
                    ensure_ascii=False,
                )

            await asyncio.sleep(poll)

    async def generate_and_wait_with_media_assets(
        self,
        prompt: str,
        model: str = "",
        size: str = "",
        quality: str = "",
        background: str = "",
        output_format: str = "",
        output_compression: Optional[int] = None,
        n: Optional[int] = None,
        moderation: str = "",
        chat_id: str = "",
        url_expires_in: int = 3600,
        timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        提交 gpt-image-2 任务并等待终态。
        """
        submit_raw = await self.generate_image_with_media_assets(
            prompt=prompt,
            model=model,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            output_compression=output_compression,
            n=n,
            moderation=moderation,
            chat_id=chat_id,
            url_expires_in=url_expires_in,
            __request__=__request__,
            __user__=__user__,
        )

        try:
            submit_payload = json.loads(submit_raw)
        except Exception:
            submit_payload = {
                "ok": False,
                "status_code": 500,
                "error_code": "InvalidToolPayload",
                "error_message": "Failed to parse generate response payload",
                "request_id": None,
            }

        if not submit_payload.get("ok"):
            return json.dumps(submit_payload, ensure_ascii=False)

        task_id = submit_payload.get("task_id") or submit_payload.get("response_id")
        if not task_id:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 502,
                    "error_code": "MissingTaskId",
                    "error_message": "Generate call succeeded but task_id(response_id) is missing",
                    "request_id": None,
                    "raw_submit_response": submit_payload.get("raw_response"),
                },
                ensure_ascii=False,
            )

        wait_raw = await self.wait_generation_task(
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            __request__=__request__,
            __user__=__user__,
        )

        try:
            wait_payload = json.loads(wait_raw)
        except Exception:
            wait_payload = {
                "ok": False,
                "status_code": 500,
                "error_code": "InvalidToolPayload",
                "error_message": "Failed to parse wait response payload",
                "request_id": None,
            }

        wait_payload["response_id"] = task_id
        wait_payload["submit_status"] = submit_payload.get("status")
        wait_payload["references"] = submit_payload.get("references", [])
        wait_payload["character_mapping"] = submit_payload.get("character_mapping", [])
        wait_payload["raw_submit_response"] = submit_payload.get("raw_response")
        return json.dumps(wait_payload, ensure_ascii=False)

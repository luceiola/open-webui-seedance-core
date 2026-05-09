"""
title: Seedance Material Package Tool
author: local-dev
version: 0.2.2
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlencode

from pathlib import Path

import httpx
from fastapi import Request
from fastapi import HTTPException
from pydantic import BaseModel, Field


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
        DEFAULT_SEEDANCE_MODEL: str = Field(
            default="doubao-seedance-2-0-260128",
            description="默认模型 ID",
        )
        PREFER_LOCAL_BACKEND: bool = Field(
            default=True,
            description="优先使用本地后端逻辑，避免通过 HTTP 回环调用导致的 502/鉴权问题",
        )
        REQUEST_TIMEOUT_SECONDS: int = Field(default=180, ge=30, le=600)

    def __init__(self) -> None:
        self.valves = self.Valves()

    def _base_url(self, __request__: Optional[Request]) -> str:
        if __request__ is not None and __request__.url is not None:
            return f"{__request__.url.scheme}://{__request__.url.netloc}"
        return self.valves.OPENWEBUI_BASE_URL.rstrip("/")

    def _headers(self, __request__: Optional[Request]) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}

        if __request__ is not None:
            auth_header = __request__.headers.get("Authorization")
            if auth_header:
                headers["Authorization"] = auth_header
                return headers

            token_cookie = __request__.cookies.get("token")
            if token_cookie:
                headers["Authorization"] = f"Bearer {token_cookie}"
                return headers

        if self.valves.OPENWEBUI_API_KEY:
            headers["Authorization"] = f"Bearer {self.valves.OPENWEBUI_API_KEY}"

        return headers

    def _user_id(self, __user__: Optional[dict]) -> Optional[str]:
        if not __user__:
            return None
        user_id = __user__.get("id")
        return str(user_id) if user_id else None

    def _extract_request_id(self, text: str) -> Optional[str]:
        if not text:
            return None
        m = re.search(r"Request id:\s*([A-Za-z0-9_-]+)", text, flags=re.IGNORECASE)
        return m.group(1) if m else None

    def _normalize_error(self, response: httpx.Response) -> dict[str, Any]:
        raw_text = response.text
        detail: Any = None
        parsed_json: Optional[dict[str, Any]] = None

        try:
            obj = response.json()
            if isinstance(obj, dict):
                parsed_json = obj
                detail = obj.get("detail")
        except Exception:
            pass

        error_code: Optional[str] = None
        error_message: Optional[str] = None
        request_id: Optional[str] = None

        # 1) FastAPI detail may already be a dict.
        if isinstance(detail, dict):
            direct_code = detail.get("error_code") or detail.get("code")
            direct_message = detail.get("error_message") or detail.get("message")
            if isinstance(direct_code, str) and direct_code.strip():
                error_code = direct_code.strip()
            if isinstance(direct_message, str) and direct_message.strip():
                error_message = direct_message.strip()

            err = detail.get("error")
            if isinstance(err, dict):
                error_code = err.get("code")
                error_message = err.get("message")
                request_id = self._extract_request_id(error_message or "")
            elif isinstance(err, str):
                error_message = err
            if not request_id:
                request_id = detail.get("request_id")

        # 2) Typical case: detail is a string "Ark tasks.create failed: { ... }".
        if isinstance(detail, str):
            text = detail
            request_id = request_id or self._extract_request_id(text)
            # Try parse trailing JSON object after ": ".
            pos = text.find("{")
            if pos >= 0:
                candidate = text[pos:]
                try:
                    nested = json.loads(candidate)
                    if isinstance(nested, dict):
                        err = nested.get("error")
                        if isinstance(err, dict):
                            error_code = error_code or err.get("code")
                            error_message = error_message or err.get("message")
                            request_id = request_id or self._extract_request_id(error_message or "")
                except Exception:
                    pass
            if not error_message:
                error_message = text

        # 3) Direct provider style: {"error":{...}}
        if not error_code and parsed_json and isinstance(parsed_json.get("error"), dict):
            err = parsed_json.get("error") or {}
            error_code = err.get("code")
            error_message = error_message or err.get("message")
            request_id = request_id or self._extract_request_id(error_message or "")

        # 4) Flat error payload style: {"error_code":"...","error_message":"..."}
        if parsed_json:
            if not error_code and isinstance(parsed_json.get("error_code"), str):
                error_code = parsed_json.get("error_code")
            if not error_message and isinstance(parsed_json.get("error_message"), str):
                error_message = parsed_json.get("error_message")
            if not request_id and isinstance(parsed_json.get("request_id"), str):
                request_id = parsed_json.get("request_id")

        return {
            "ok": False,
            "status_code": response.status_code,
            "error": raw_text,
            "error_code": error_code,
            "error_message": error_message or raw_text,
            "request_id": request_id,
        }

    def _normalize_http_exception(self, exc: HTTPException) -> dict[str, Any]:
        status_code = int(exc.status_code or 500)
        detail = exc.detail
        raw = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)

        error_code: Optional[str] = None
        error_message: Optional[str] = None
        request_id: Optional[str] = None

        if isinstance(detail, dict):
            direct_code = detail.get("error_code") or detail.get("code")
            direct_message = detail.get("error_message") or detail.get("message")
            if isinstance(direct_code, str) and direct_code.strip():
                error_code = direct_code.strip()
            if isinstance(direct_message, str) and direct_message.strip():
                error_message = direct_message.strip()

            err = detail.get("error")
            if isinstance(err, dict):
                error_code = err.get("code")
                error_message = err.get("message")
            elif isinstance(err, str):
                error_message = err
            request_id = detail.get("request_id") or self._extract_request_id(error_message or "")
        elif isinstance(detail, str):
            request_id = self._extract_request_id(detail)
            pos = detail.find("{")
            if pos >= 0:
                try:
                    nested = json.loads(detail[pos:])
                    if isinstance(nested, dict) and isinstance(nested.get("error"), dict):
                        err = nested.get("error") or {}
                        error_code = err.get("code")
                        error_message = err.get("message")
                        request_id = request_id or self._extract_request_id(error_message or "")
                except Exception:
                    pass
            if not error_message:
                error_message = detail

        return {
            "ok": False,
            "status_code": status_code,
            "error": raw,
            "error_code": error_code,
            "error_message": error_message or raw,
            "request_id": request_id,
        }

    def _manifest_dir(self, user_id: str) -> Path:
        from open_webui.routers.material_packages import MATERIAL_PACKAGES_DIR

        return Path(MATERIAL_PACKAGES_DIR) / user_id

    def _load_manifest(self, user_id: str, package_id: str) -> dict[str, Any]:
        manifest_path = self._manifest_dir(user_id) / f"{package_id}.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"material package not found: {package_id}")
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def _to_dict(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            return data
        if hasattr(data, "model_dump"):
            return data.model_dump()
        if hasattr(data, "dict"):
            return data.dict()
        return {}

    def _is_terminal_status(self, status: Optional[str]) -> bool:
        s = (status or "").strip().lower()
        return s in {"succeeded", "completed", "failed", "error", "cancelled"}

    def _is_success_status(self, status: Optional[str]) -> bool:
        s = (status or "").strip().lower()
        return s in {"succeeded", "completed"}

    def _extract_status_from_raw(self, raw_response: Any) -> Optional[str]:
        if not isinstance(raw_response, dict):
            return None
        data = raw_response.get("data") if isinstance(raw_response.get("data"), dict) else {}
        return (
            raw_response.get("status")
            or data.get("status")
            or (data.get("task") or {}).get("status")
            or (raw_response.get("task") or {}).get("status")
        )

    def _find_first_video_url(self, payload: Any) -> Optional[str]:
        if isinstance(payload, dict):
            for key in ("video_url", "output_video_url", "result_url"):
                value = payload.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value

            # Fallback: accept generic url fields only when looking like mp4.
            for key in ("url", "download_url"):
                value = payload.get(key)
                if (
                    isinstance(value, str)
                    and value.startswith(("http://", "https://"))
                    and ".mp4" in value.lower()
                ):
                    return value

            for value in payload.values():
                found = self._find_first_video_url(value)
                if found:
                    return found
            return None

        if isinstance(payload, list):
            for item in payload:
                found = self._find_first_video_url(item)
                if found:
                    return found

        return None

    def _normalize_video_url(self, value: Any) -> Optional[str]:
        url = str(value or "").strip()
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            return None
        return url

    def _to_video_url_markdown_or_na(self, value: Any) -> str:
        url = self._normalize_video_url(value)
        if not url:
            return "暂无"
        return f"[查看视频]({url})"

    def _extract_task_error(self, raw_response: Any) -> dict[str, Optional[str]]:
        if not isinstance(raw_response, dict):
            return {"error_code": None, "error_message": None, "request_id": None}

        request_id = raw_response.get("request_id")
        error_code: Optional[str] = None
        error_message: Optional[str] = None

        for candidate in (
            raw_response.get("error"),
            (raw_response.get("data") or {}).get("error") if isinstance(raw_response.get("data"), dict) else None,
        ):
            if isinstance(candidate, dict):
                error_code = error_code or candidate.get("code")
                error_message = error_message or candidate.get("message")
                request_id = request_id or candidate.get("request_id")
            elif isinstance(candidate, str):
                error_message = error_message or candidate

        if not error_message and isinstance(raw_response.get("message"), str):
            error_message = raw_response.get("message")

        request_id = request_id or self._extract_request_id(error_message or "")
        return {
            "error_code": error_code,
            "error_message": error_message,
            "request_id": request_id,
        }

    def _read_env_value_from_file(self, key: str) -> Optional[str]:
        candidates: list[Path] = []
        env_path = (os.getenv("ARK_ENV_FILE") or "").strip()
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.extend(
            [
                Path.cwd() / "config" / "ark.env",
                Path.cwd() / "config" / "ark.dev.env",
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

    def _get_ark_base_url(self) -> str:
        base_url = (
            os.getenv("ARK_BASE_URL")
            or self._read_env_value_from_file("ARK_BASE_URL")
            or "https://ark.cn-beijing.volces.com/api/v3"
        )
        base = str(base_url).strip().rstrip("/")
        if "/api/" not in base:
            base = f"{base}/api/v3"
        return base

    def _get_ark_api_key(self) -> str:
        value = (os.getenv("ARK_API_KEY") or self._read_env_value_from_file("ARK_API_KEY") or "").strip()
        return value

    def _is_seedance_model(self, model: str) -> bool:
        value = (model or "").strip().lower()
        return "seedance" in value or "seedance" in value.replace("-", "")

    def _build_seedance_reference_block(self, media_type: str, url: str) -> dict[str, Any]:
        if media_type == "image":
            return {
                "type": "image_url",
                "image_url": {"url": url},
                "role": "reference_image",
            }
        if media_type == "video":
            return {
                "type": "video_url",
                "video_url": {"url": url},
                "role": "reference_video",
            }
        if media_type == "audio":
            return {
                "type": "audio_url",
                "audio_url": {"url": url},
                "role": "reference_audio",
            }
        raise ValueError(f"Unsupported media type for seedance generation: {media_type}")

    def _extract_media_asset_references(self, prompt: str) -> list[str]:
        refs = re.findall(r"%([^\s%,，。；;:：!！?？)）\]】}》>\"“”'`]+)", prompt or "")
        cleaned: list[str] = []
        for ref in refs:
            value = ref.strip().rstrip(".,;:!?)\\]}>'\"")
            if value:
                cleaned.append(value)
        return list(dict.fromkeys(cleaned))

    def _clean_prompt_references(self, prompt: str, references: list[str], symbol: str) -> str:
        cleaned = prompt
        for ref in references:
            cleaned = re.sub(rf"{re.escape(symbol)}{re.escape(ref)}", ref, cleaned)
        return cleaned

    def _compact_task_item(self, item: dict[str, Any]) -> dict[str, Any]:
        video_url = self._normalize_video_url(item.get("video_url"))
        return {
            "task_id": item.get("task_id"),
            "package_id": item.get("package_id"),
            "chat_id": item.get("chat_id"),
            "model": item.get("model"),
            "status": item.get("status"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "references": item.get("references") or [],
            "duration": item.get("duration"),
            "ratio": item.get("ratio"),
            "watermark": item.get("watermark"),
            "generate_audio": item.get("generate_audio"),
            "video_url": video_url,
            "video_url_markdown": self._to_video_url_markdown_or_na(video_url),
            "error_code": item.get("error_code"),
            "error_message": item.get("error_message"),
            "request_id": item.get("request_id"),
        }

    def _compact_package_item(self, item: dict[str, Any]) -> dict[str, Any]:
        assets = item.get("assets", [])
        refs = [a.get("reference_name") for a in assets if isinstance(a, dict) and a.get("reference_name")]
        package_id = item.get("asset_package_id") or item.get("id")
        return {
            "asset_package_id": package_id,
            "package_display_name": item.get("package_display_name") or item.get("source_filename") or item.get("zip_filename"),
            "source_filename": item.get("source_filename") or item.get("zip_filename"),
            "source_kind": item.get("source_kind"),
            "merged_asset_count": item.get("merged_asset_count"),
            "status": item.get("status"),
            "asset_count": len(assets),
            "references": refs,
            "created_at": item.get("created_at"),
        }

    def _compact_media_asset_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": item.get("asset_id"),
            "display_name": item.get("display_name"),
            "relative_path": item.get("relative_path"),
            "original_filename": item.get("original_filename"),
            "media_type": item.get("media_type"),
            "mime_type": item.get("mime_type"),
            "size_bytes": item.get("size_bytes"),
            "status": item.get("status"),
            "chat_id": item.get("chat_id"),
            "tos_key": item.get("tos_key"),
            "tos_status": item.get("tos_status"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }

    def _media_asset_reference_candidates(self, item: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("relative_path", "display_name", "original_filename"):
            value = str(item.get(key) or "").strip()
            if value:
                candidates.append(value)
        # dedupe while preserving order
        return list(dict.fromkeys(candidates))

    def _extract_upload_ids_from_files_context(self, __files__: Any) -> list[str]:
        if not isinstance(__files__, list):
            return []

        ids: list[str] = []
        seen: set[str] = set()
        for item in __files__:
            if not isinstance(item, dict):
                continue

            candidates = []
            for key in ("id", "file_id", "upload_id"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())

            nested = item.get("file")
            if isinstance(nested, dict):
                nested_id = nested.get("id")
                if isinstance(nested_id, str) and nested_id.strip():
                    candidates.append(nested_id.strip())

            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                ids.append(candidate)

        return ids

    def _is_zip_like(self, filename: str = "", mime_type: str = "") -> bool:
        name = (filename or "").strip().lower()
        if name.endswith(".zip"):
            return True
        mt = (mime_type or "").strip().lower()
        return mt in {
            "application/zip",
            "application/x-zip-compressed",
            "application/x-zip",
            "multipart/x-zip",
        }

    async def _detect_zip_upload_ids(self, upload_ids: list[str], __request__: Optional[Request]) -> list[str]:
        zip_ids: list[str] = []
        for fid in upload_ids:
            result = await self._request("GET", f"/api/v1/files/{fid}", __request__)
            if not result.get("ok"):
                continue
            data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
            meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
            filename = str(meta.get("name") or data.get("filename") or "")
            mime_type = str(meta.get("content_type") or "")
            if self._is_zip_like(filename, mime_type):
                zip_ids.append(fid)
        return zip_ids

    async def _request(
        self,
        method: str,
        path: str,
        __request__: Optional[Request],
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url(__request__)}{path}"
        headers = self._headers(__request__)

        # Disable env proxy inheritance to avoid localhost requests being routed to proxy
        # and returning opaque 502 errors.
        async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            response = await client.request(method=method, url=url, headers=headers, json=body)

        if response.status_code >= 400:
            return self._normalize_error(response)

        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": response.text}

        return {"ok": True, "status_code": response.status_code, "data": payload}

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
        credential_alias: Optional[str] = None,
        routing_group_id: Optional[str] = None,
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
            "provider": "ark",
            "provider_task_id": tid,
            "tool_name": "seedance_material_package_tool.generate_video_with_media_assets",
            "skill_name": "seedance",
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
        if credential_alias:
            payload["credential_alias"] = credential_alias
        if routing_group_id:
            payload["routing_group_id"] = routing_group_id
        if prompt_text is not None:
            payload["prompt_text"] = str(prompt_text)
        if generation_params is not None:
            payload["generation_params"] = generation_params
        if prompt_resources is not None:
            payload["prompt_resources"] = prompt_resources

        bridge = await self._request("POST", "/api/v1/tasks/bridge/upsert", __request__, payload)
        return bool(bridge.get("ok"))

    async def list_material_packages(self, __request__: Request = None, __user__: dict = None) -> str:
        """
        列出当前用户可用素材包，返回包 ID、包名、来源类型、可引用素材名。
        """
        user_id = self._user_id(__user__)
        if self.valves.PREFER_LOCAL_BACKEND and user_id:
            try:
                user_dir = self._manifest_dir(user_id)
                if not user_dir.exists():
                    return json.dumps({"ok": True, "packages": []}, ensure_ascii=False)

                packages: list[dict[str, Any]] = []
                for p in sorted(user_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                    item = json.loads(p.read_text(encoding="utf-8"))
                    packages.append(self._compact_package_item(item))
                return json.dumps({"ok": True, "packages": packages}, ensure_ascii=False)
            except Exception as e:
                # Fallback to HTTP path when local path fails.
                fallback_warning = {"local_backend_warning": str(e)}
            else:
                fallback_warning = {}
        else:
            fallback_warning = {}

        result = await self._request("GET", "/api/v1/material-packages/", __request__)
        if not result["ok"]:
            result.update(fallback_warning)
            return json.dumps(result, ensure_ascii=False)

        rows = []
        for item in result["data"]:
            if isinstance(item, dict):
                rows.append(self._compact_package_item(item))

        payload = {"ok": True, "packages": rows}
        payload.update(fallback_warning)
        return json.dumps(payload, ensure_ascii=False)

    async def get_material_package(
        self,
        asset_package_id: str,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        查询素材包详情，确认引用名与 TOS 上传状态。
        """
        user_id = self._user_id(__user__)
        if self.valves.PREFER_LOCAL_BACKEND and user_id:
            try:
                manifest = self._load_manifest(user_id, asset_package_id)
                return json.dumps({"ok": True, "status_code": 200, "data": manifest}, ensure_ascii=False)
            except Exception as e:
                local_warning = {"local_backend_warning": str(e)}
            else:
                local_warning = {}
        else:
            local_warning = {}

        path = f"/api/v1/material-packages/{asset_package_id}"
        result = await self._request("GET", path, __request__)
        result.update(local_warning)
        return json.dumps(result, ensure_ascii=False)

    async def create_material_package_from_chat_upload(
        self,
        upload_ids: Optional[list[str]] = None,
        chat_id: str = "",
        package_display_name: str = "",
        __files__: list | None = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        将聊天上传文件转为素材包。
        优先使用显式 upload_ids；若未传则尝试从 __files__ 上下文提取。
        """
        explicit_ids = [str(item).strip() for item in (upload_ids or []) if str(item).strip()]
        context_ids = self._extract_upload_ids_from_files_context(__files__)

        seen: set[str] = set()
        final_upload_ids: list[str] = []
        for item in [*explicit_ids, *context_ids]:
            if item in seen:
                continue
            seen.add(item)
            final_upload_ids.append(item)

        auto_selection_info: dict[str, Any] = {}
        user_id = self._user_id(__user__)
        if len(final_upload_ids) > 1:
            try:
                zip_ids = await self._detect_zip_upload_ids(final_upload_ids, __request__)
                # Backend requires ZIP to be sent alone; when multiple uploads include ZIP(s),
                # auto-select latest ZIP and drop the rest to avoid 400 blocking.
                if zip_ids:
                    selected_zip_id = zip_ids[-1]
                    dropped = [fid for fid in final_upload_ids if fid != selected_zip_id]
                    final_upload_ids = [selected_zip_id]
                    auto_selection_info = {
                        "auto_selected_zip_upload_id": selected_zip_id,
                        "dropped_upload_ids": dropped,
                        "warning": "Detected multiple uploads with ZIP; ZIP must be sent alone, so only the latest ZIP was used.",
                    }
            except Exception:
                pass

        if not final_upload_ids:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "No upload_ids found. Pass upload_ids or ensure chat files are available in context.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        body: dict[str, Any] = {
            "upload_ids": final_upload_ids,
        }
        if (chat_id or "").strip():
            body["chat_id"] = chat_id.strip()
        if (package_display_name or "").strip():
            body["package_display_name"] = package_display_name.strip()

        if self.valves.PREFER_LOCAL_BACKEND and user_id:
            try:
                from open_webui.routers import material_packages as mp

                class _LocalUser:
                    def __init__(self, uid: str):
                        self.id = uid

                form_data = mp.CreateMaterialPackageFromUploadRequest(**body)
                data = await mp.create_material_package_from_chat_upload(form_data=form_data, user=_LocalUser(user_id))
                obj = self._to_dict(data)
                refs = [
                    a.get("reference_name")
                    for a in obj.get("assets", [])
                    if isinstance(a, dict) and a.get("reference_name")
                ]
                return json.dumps(
                    {
                        "ok": True,
                        "asset_package_id": obj.get("asset_package_id") or obj.get("id"),
                        "package_display_name": obj.get("package_display_name"),
                        "source_filename": obj.get("source_filename") or obj.get("zip_filename"),
                        "source_kind": obj.get("source_kind"),
                        "status": obj.get("status"),
                        "asset_count": len(obj.get("assets", []) or []),
                        "references": refs,
                        "data": obj,
                        **auto_selection_info,
                    },
                    ensure_ascii=False,
                )
            except HTTPException as e:
                return json.dumps(self._normalize_http_exception(e), ensure_ascii=False)

        result = await self._request("POST", "/api/v1/material-packages/from-upload", __request__, body)
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
        refs = [
            a.get("reference_name")
            for a in data.get("assets", [])
            if isinstance(a, dict) and a.get("reference_name")
        ]
        return json.dumps(
            {
                "ok": True,
                "asset_package_id": data.get("asset_package_id") or data.get("id"),
                "package_display_name": data.get("package_display_name"),
                "source_filename": data.get("source_filename") or data.get("zip_filename"),
                "source_kind": data.get("source_kind"),
                "status": data.get("status"),
                "asset_count": len(data.get("assets", []) or []),
                "references": refs,
                "data": data,
                **auto_selection_info,
            },
            ensure_ascii=False,
        )

    async def get_material_package_assets(
        self,
        asset_package_id: str,
        include_temp_urls: bool = False,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        查询素材包内资产地址信息（tos_key/tos_status，可选 temp_url）。
        """
        user_id = self._user_id(__user__)
        if self.valves.PREFER_LOCAL_BACKEND and user_id:
            try:
                from open_webui.routers import material_packages as mp

                class _LocalUser:
                    def __init__(self, uid: str):
                        self.id = uid

                data = await mp.get_material_package_assets(
                    package_id=asset_package_id,
                    include_temp_urls=bool(include_temp_urls),
                    user=_LocalUser(user_id),
                )
                obj = self._to_dict(data)
                return json.dumps({"ok": True, "data": obj}, ensure_ascii=False)
            except HTTPException as e:
                return json.dumps(self._normalize_http_exception(e), ensure_ascii=False)

        query = urlencode({"include_temp_urls": "true" if include_temp_urls else "false"})
        path = f"/api/v1/material-packages/{asset_package_id}/assets?{query}"
        result = await self._request("GET", path, __request__)
        return json.dumps(result, ensure_ascii=False)

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
        列出当前用户媒体素材（独立素材链路）。
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
        解析 prompt 中 `%素材路径` 引用，返回命中素材和未命中列表。
        优先按完整相对路径匹配；若只给了文件名且存在重名，返回冲突提示。
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
                if basename not in basename_to_canonical:
                    basename_to_canonical[basename] = []
                if canonical not in basename_to_canonical[basename]:
                    basename_to_canonical[basename].append(canonical)

        missing: list[str] = []
        ambiguous: list[dict[str, Any]] = []
        resolved_assets: list[dict[str, Any]] = []
        for ref in refs:
            canonical = alias_to_canonical.get(ref)
            if not canonical:
                basename_hits = basename_to_canonical.get(ref) or []
                if len(basename_hits) == 1:
                    canonical = basename_hits[0]
                elif len(basename_hits) > 1:
                    missing.append(ref)
                    ambiguous.append(
                        {
                            "reference": ref,
                            "candidates": basename_hits,
                        }
                    )
                    continue

            if not canonical:
                missing.append(ref)
                continue

            item = canonical_to_item.get(canonical)
            if not isinstance(item, dict):
                missing.append(ref)
                continue
            resolved_assets.append(self._compact_media_asset_item(item))

        cleaned_prompt = self._clean_prompt_references(prompt, refs, "%")

        payload = {
            "ok": True,
            "status_code": 200,
            "data": {
                "references": refs,
                "missing_references": missing,
                "ambiguous_references": ambiguous,
                "available_references": sorted(list(dict.fromkeys(available_refs))),
                "cleaned_prompt": cleaned_prompt,
                "assets": resolved_assets,
            },
        }
        return json.dumps(payload, ensure_ascii=False)

    async def resolve_material_references(
        self,
        asset_package_id: str,
        prompt: str,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        解析 prompt 中 `@文件名` 引用，返回命中素材和未命中列表。
        """
        user_id = self._user_id(__user__)
        if self.valves.PREFER_LOCAL_BACKEND and user_id:
            try:
                from open_webui.routers import material_packages as mp

                manifest = self._load_manifest(user_id, asset_package_id)
                assets = [a for a in manifest.get("assets", [])]
                assets_map = {a.get("reference_name"): a for a in assets if a.get("reference_name")}

                refs = mp._extract_references(prompt)
                missing = [ref for ref in refs if ref not in assets_map]
                cleaned_prompt = mp._clean_prompt(prompt, refs)
                resolved_assets = [assets_map[ref] for ref in refs if ref in assets_map]

                payload = {
                    "ok": True,
                    "status_code": 200,
                    "data": {
                        "package_id": asset_package_id,
                        "references": refs,
                        "missing_references": missing,
                        "available_references": sorted(list(assets_map.keys())),
                        "cleaned_prompt": cleaned_prompt,
                        "assets": resolved_assets,
                    },
                }
                return json.dumps(payload, ensure_ascii=False)
            except Exception as e:
                local_warning = {"local_backend_warning": str(e)}
            else:
                local_warning = {}
        else:
            local_warning = {}

        path = f"/api/v1/material-packages/{asset_package_id}/resolve"
        result = await self._request("POST", path, __request__, {"prompt": prompt})
        result.update(local_warning)
        return json.dumps(result, ensure_ascii=False)

    async def generate_video_with_material_package(
        self,
        asset_package_id: str,
        prompt: str,
        model: str = "",
        instructions: str = "",
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        duration: Optional[int] = None,
        ratio: str = "",
        watermark: Optional[bool] = None,
        generate_audio: Optional[bool] = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        使用素材包触发 Seedance 生成。
        用户在 prompt 中用 `@文件名` 引用素材。
        """
        local_warning: dict[str, Any] = {}

        payload: dict[str, Any] = {
            "prompt": prompt,
            "model": model.strip() or self.valves.DEFAULT_SEEDANCE_MODEL,
        }
        if instructions.strip():
            payload["instructions"] = instructions
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        if duration is not None:
            payload["duration"] = duration
        if ratio.strip():
            payload["ratio"] = ratio.strip()
        if watermark is not None:
            payload["watermark"] = watermark
        if generate_audio is not None:
            payload["generate_audio"] = generate_audio

        user_id = self._user_id(__user__)
        if self.valves.PREFER_LOCAL_BACKEND and user_id:
            try:
                from open_webui.routers import material_packages as mp

                class _LocalUser:
                    def __init__(self, uid: str):
                        self.id = uid

                form_data = mp.GenerateWithPackageRequest(**payload)
                data = await mp.generate_with_material_package(
                    package_id=asset_package_id,
                    form_data=form_data,
                    user=_LocalUser(user_id),
                )
                obj = data.model_dump() if hasattr(data, "model_dump") else data.dict()
                simplified = {
                    "ok": True,
                    "asset_package_id": obj.get("package_id"),
                    "references": obj.get("references", []),
                    "response_id": obj.get("response_id"),
                    "status": obj.get("status"),
                    "output_text": obj.get("output_text"),
                    "raw_response": obj.get("raw_response"),
                }
                simplified.update(local_warning)
                return json.dumps(simplified, ensure_ascii=False)
            except HTTPException as e:
                err = self._normalize_http_exception(e)
                err.update(local_warning)
                return json.dumps(err, ensure_ascii=False)
            except Exception as e:
                local_warning["local_backend_warning"] = str(e)

        path = f"/api/v1/material-packages/{asset_package_id}/generate"
        result = await self._request("POST", path, __request__, payload)

        if not result.get("ok"):
            result.update(local_warning)
            return json.dumps(result, ensure_ascii=False)

        data = result.get("data", {})
        simplified = {
            "ok": True,
            "asset_package_id": data.get("package_id"),
            "references": data.get("references", []),
            "response_id": data.get("response_id"),
            "status": data.get("status"),
            "output_text": data.get("output_text"),
            "raw_response": data.get("raw_response"),
        }
        simplified.update(local_warning)
        return json.dumps(simplified, ensure_ascii=False)

    async def generate_video_with_media_assets(
        self,
        prompt: str,
        model: str = "",
        instructions: str = "",
        duration: Optional[int] = None,
        ratio: str = "",
        watermark: Optional[bool] = None,
        generate_audio: Optional[bool] = None,
        url_expires_in: int = 3600,
        chat_id: str = "",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        使用 `%素材路径` 解析媒体素材并直接提交 Seedance 任务（不依赖素材包）。
        """
        model_id = model.strip() or self.valves.DEFAULT_SEEDANCE_MODEL
        if not self._is_seedance_model(model_id):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": None,
                    "error_message": "Only seedance models are supported in media-assets mode.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        resolve_raw = await self.resolve_media_asset_references(
            prompt=prompt,
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
                    "error_message": "Missing referenced media assets",
                    "request_id": None,
                    "missing_references": missing,
                    "ambiguous_references": ambiguous,
                    "available_references": resolve_data.get("available_references") or [],
                },
                ensure_ascii=False,
            )

        cleaned_prompt = str(resolve_data.get("cleaned_prompt") or prompt)
        resolved_assets = [item for item in (resolve_data.get("assets") or []) if isinstance(item, dict)]
        prompt_text_for_task = str(prompt or "").strip() or cleaned_prompt

        seedance_content: list[dict[str, Any]] = [{"type": "text", "text": cleaned_prompt}]
        unresolved_references: list[dict[str, Any]] = []
        prompt_resources_for_task: list[dict[str, str]] = []
        for idx, asset in enumerate(resolved_assets):
            asset_id = str(asset.get("asset_id") or "").strip()
            media_type = str(asset.get("media_type") or "").strip().lower()
            ref_name = str((refs[idx] if idx < len(refs) else "") or "").strip()
            if not ref_name:
                ref_name = str(
                    asset.get("relative_path")
                    or asset.get("display_name")
                    or asset.get("original_filename")
                    or asset_id
                )
            if not asset_id:
                unresolved_references.append({"reference": ref_name, "error": "missing asset_id"})
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
                unresolved_references.append(
                    {
                        "reference": ref_name,
                        "asset_id": asset_id,
                        "error_message": url_payload.get("error_message") or url_payload.get("error"),
                    }
                )
                continue

            url_data = url_payload.get("data", {}) if isinstance(url_payload.get("data"), dict) else {}
            media_url = url_data.get("url")
            if not isinstance(media_url, str) or not media_url.startswith(("http://", "https://")):
                unresolved_references.append(
                    {
                        "reference": ref_name,
                        "asset_id": asset_id,
                        "error_message": "Invalid media asset url",
                    }
                )
                continue

            try:
                seedance_content.append(self._build_seedance_reference_block(media_type, media_url))
                prompt_resources_for_task.append({"name": ref_name, "url": media_url})
            except Exception as e:
                unresolved_references.append(
                    {
                        "reference": ref_name,
                        "asset_id": asset_id,
                        "error_message": str(e),
                    }
                )

        if unresolved_references:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "UnresolvedMediaAssets",
                    "error_message": "Unable to resolve url for some media assets",
                    "request_id": None,
                    "unresolved_references": unresolved_references,
                },
                ensure_ascii=False,
            )

        payload: dict[str, Any] = {"model": model_id, "content": seedance_content}
        if instructions.strip():
            payload["instructions"] = instructions.strip()
        if duration is not None:
            payload["duration"] = duration
        if ratio.strip():
            payload["ratio"] = ratio.strip()
        if watermark is not None:
            payload["watermark"] = watermark
        if generate_audio is not None:
            payload["generate_audio"] = generate_audio

        generation_params_for_task: dict[str, Any] = {}
        if duration is not None:
            generation_params_for_task["duration"] = duration
        if ratio.strip():
            generation_params_for_task["ratio"] = ratio.strip()
        if watermark is not None:
            generation_params_for_task["watermark"] = watermark
        if generate_audio is not None:
            generation_params_for_task["generate_audio"] = generate_audio

        submit = await self._request(
            "POST",
            "/api/v1/material-packages/providers/ark/generations/tasks",
            __request__,
            payload,
        )
        if not submit.get("ok"):
            return json.dumps(submit, ensure_ascii=False)

        submit_data = submit.get("data") if isinstance(submit.get("data"), dict) else {}
        response_json = submit_data.get("data") if isinstance(submit_data.get("data"), dict) else {}
        credential_alias = str(submit_data.get("credential_alias") or "").strip() or None
        routing_group_id = str(submit_data.get("routing_group_id") or "").strip() or None
        if not isinstance(response_json, dict):
            response_json = {}

        task_id = (
            response_json.get("task_id")
            or response_json.get("id")
            or (response_json.get("data") or {}).get("task_id")
            or (response_json.get("data") or {}).get("id")
        )
        task_status = response_json.get("status") or (response_json.get("data") or {}).get("status") or "submitted"

        if task_id:
            try:
                await self._bridge_upsert_task(
                    task_id=str(task_id),
                    status=str(task_status),
                    model=model_id,
                    chat_id=chat_id,
                    references=refs,
                    raw_submit_response=response_json,
                    raw_last_response=response_json,
                    credential_alias=credential_alias,
                    routing_group_id=routing_group_id,
                    prompt_text=prompt_text_for_task,
                    generation_params=generation_params_for_task or None,
                    prompt_resources=prompt_resources_for_task,
                    __request__=__request__,
                )
            except Exception:
                pass
            try:
                await self.get_generation_task_status(task_id=task_id, __request__=__request__, __user__=__user__)
            except Exception:
                pass

        result = {
            "ok": True,
            "references": refs,
            "response_id": task_id,
            "status": task_status,
            "output_text": None,
            "raw_response": response_json,
            "resolved_assets": resolved_assets,
        }
        return json.dumps(result, ensure_ascii=False)

    async def list_generation_tasks(
        self,
        package_id: str = "",
        status: str = "",
        chat_id: str = "",
        limit: int = 50,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        列出当前用户生成任务，支持按素材包/状态/chat 过滤。
        """
        limit = max(1, min(int(limit or 50), 200))
        package_id = (package_id or "").strip()
        desired_status = (status or "").strip().lower()
        chat_id = (chat_id or "").strip()

        user_id = self._user_id(__user__)
        if self.valves.PREFER_LOCAL_BACKEND and user_id:
            try:
                from open_webui.routers import material_packages as mp

                class _LocalUser:
                    def __init__(self, uid: str):
                        self.id = uid

                data = await mp.list_generation_tasks(
                    package_id=package_id or None,
                    task_status=desired_status or None,
                    chat_id=chat_id or None,
                    refresh_status=True,
                    limit=limit,
                    user=_LocalUser(user_id),
                )

                rows: list[dict[str, Any]] = []
                for item in data:
                    row = self._to_dict(item)
                    if isinstance(row, dict):
                        rows.append(self._compact_task_item(row))
                return json.dumps({"ok": True, "tasks": rows}, ensure_ascii=False)
            except Exception as e:
                local_warning = {"local_backend_warning": str(e)}
            else:
                local_warning = {}
        else:
            local_warning = {}

        query: dict[str, Any] = {"limit": limit}
        if package_id:
            query["package_id"] = package_id
        if desired_status:
            query["status"] = desired_status
        if chat_id:
            query["chat_id"] = chat_id
        query["refresh_status"] = "true"

        path = "/api/v1/material-packages/tasks"
        if query:
            path = f"{path}?{urlencode(query)}"

        result = await self._request("GET", path, __request__)
        if not result.get("ok"):
            result.update(local_warning)
            return json.dumps(result, ensure_ascii=False)

        tasks = []
        for item in result.get("data", []):
            if isinstance(item, dict):
                tasks.append(self._compact_task_item(item))

        payload = {"ok": True, "tasks": tasks}
        payload.update(local_warning)
        return json.dumps(payload, ensure_ascii=False)

    async def get_generation_task_status(
        self,
        task_id: str,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        查询生成任务状态，并尝试提取 video_url。
        """
        task_id = (task_id or "").strip()
        if not task_id:
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

        local_warning: dict[str, Any] = {}
        user_id = self._user_id(__user__)
        if self.valves.PREFER_LOCAL_BACKEND and user_id:
            try:
                from open_webui.routers import material_packages as mp

                class _LocalUser:
                    def __init__(self, uid: str):
                        self.id = uid

                data = await mp.get_generation_task_status(task_id=task_id, user=_LocalUser(user_id))
                obj = self._to_dict(data)
                raw = obj.get("raw_response")
                status = obj.get("status") or self._extract_status_from_raw(raw)
                video_url = self._normalize_video_url(self._find_first_video_url(raw))
                payload = {
                    "ok": True,
                    "task_id": obj.get("task_id") or task_id,
                    "status": status,
                    "video_url": video_url,
                    "video_url_markdown": self._to_video_url_markdown_or_na(video_url),
                    "raw_response": raw,
                }
                payload.update(local_warning)
                return json.dumps(payload, ensure_ascii=False)
            except HTTPException as e:
                err = self._normalize_http_exception(e)
                err["task_id"] = task_id
                err.update(local_warning)
                return json.dumps(err, ensure_ascii=False)
            except Exception as e:
                local_warning["local_backend_warning"] = str(e)

        result = await self._request("GET", f"/api/v1/material-packages/tasks/{task_id}", __request__)
        if not result.get("ok"):
            result["task_id"] = task_id
            result.update(local_warning)
            return json.dumps(result, ensure_ascii=False)

        data = result.get("data", {})
        raw = data.get("raw_response")
        status = data.get("status") or self._extract_status_from_raw(raw)
        video_url = self._normalize_video_url(self._find_first_video_url(raw))
        payload = {
            "ok": True,
            "task_id": data.get("task_id") or task_id,
            "status": status,
            "video_url": video_url,
            "video_url_markdown": self._to_video_url_markdown_or_na(video_url),
            "raw_response": raw,
        }
        payload.update(local_warning)
        return json.dumps(payload, ensure_ascii=False)

    async def wait_generation_task(
        self,
        task_id: str,
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 3,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        轮询任务直到终态（或超时），并返回最终状态和 video_url。
        """
        timeout_seconds = max(5, min(int(timeout_seconds or 600), 7200))
        poll_interval_seconds = max(1, min(int(poll_interval_seconds or 3), 60))

        start = time.monotonic()
        last_payload: dict[str, Any] = {}

        while True:
            status_raw = await self.get_generation_task_status(
                task_id=task_id, __request__=__request__, __user__=__user__
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
                    "task_id": task_id,
                }
                return json.dumps(payload, ensure_ascii=False)

            last_payload = payload
            if not payload.get("ok"):
                payload["task_id"] = payload.get("task_id") or task_id
                payload["elapsed_seconds"] = int(time.monotonic() - start)
                return json.dumps(payload, ensure_ascii=False)

            current_status = (payload.get("status") or "").strip().lower()
            if self._is_terminal_status(current_status):
                payload["elapsed_seconds"] = int(time.monotonic() - start)

                if self._is_success_status(current_status):
                    payload["ok"] = True
                    return json.dumps(payload, ensure_ascii=False)

                raw = payload.get("raw_response")
                err = self._extract_task_error(raw)
                failed_payload = {
                    "ok": False,
                    "status_code": 200,
                    "task_id": payload.get("task_id") or task_id,
                    "status": current_status,
                    "video_url": payload.get("video_url"),
                    "video_url_markdown": self._to_video_url_markdown_or_na(payload.get("video_url")),
                    "raw_response": raw,
                    "error_code": err.get("error_code") or "GenerationTaskFailed",
                    "error_message": err.get("error_message")
                    or f"Generation task ended with status={current_status}",
                    "request_id": err.get("request_id"),
                    "elapsed_seconds": payload["elapsed_seconds"],
                }
                return json.dumps(failed_payload, ensure_ascii=False)

            elapsed = time.monotonic() - start
            if elapsed >= timeout_seconds:
                timeout_payload = {
                    "ok": False,
                    "status_code": 408,
                    "task_id": task_id,
                    "status": last_payload.get("status"),
                    "video_url": last_payload.get("video_url"),
                    "video_url_markdown": self._to_video_url_markdown_or_na(last_payload.get("video_url")),
                    "raw_response": last_payload.get("raw_response"),
                    "error_code": "GenerationTaskPollingTimeout",
                    "error_message": "Generation task polling timeout",
                    "request_id": None,
                    "elapsed_seconds": int(elapsed),
                }
                return json.dumps(timeout_payload, ensure_ascii=False)

            await asyncio.sleep(poll_interval_seconds)

    async def generate_and_wait_with_material_package(
        self,
        asset_package_id: str,
        prompt: str,
        model: str = "",
        instructions: str = "",
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        duration: Optional[int] = None,
        ratio: str = "",
        watermark: Optional[bool] = None,
        generate_audio: Optional[bool] = None,
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 3,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        一步完成：提交生成任务并等待到终态。
        """
        submit_raw = await self.generate_video_with_material_package(
            asset_package_id=asset_package_id,
            prompt=prompt,
            model=model,
            instructions=instructions,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            duration=duration,
            ratio=ratio,
            watermark=watermark,
            generate_audio=generate_audio,
            __request__=__request__,
            __user__=__user__,
        )
        try:
            submit_payload = json.loads(submit_raw)
        except Exception:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 500,
                    "error_code": "InvalidToolPayload",
                    "error_message": "Failed to parse generate response payload",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        if not submit_payload.get("ok"):
            return json.dumps(submit_payload, ensure_ascii=False)

        task_id = (
            submit_payload.get("response_id")
            or submit_payload.get("task_id")
            or (submit_payload.get("raw_response") or {}).get("id")
        )
        if not task_id:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 502,
                    "error_code": "MissingTaskId",
                    "error_message": "Generate call succeeded but task_id(response_id) is missing",
                    "request_id": None,
                    "asset_package_id": submit_payload.get("asset_package_id") or asset_package_id,
                    "references": submit_payload.get("references", []),
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

        wait_payload["asset_package_id"] = submit_payload.get("asset_package_id") or asset_package_id
        wait_payload["references"] = submit_payload.get("references", [])
        wait_payload["response_id"] = task_id
        wait_payload["submit_status"] = submit_payload.get("status")
        wait_payload["raw_submit_response"] = submit_payload.get("raw_response")
        return json.dumps(wait_payload, ensure_ascii=False)

    async def generate_and_wait_with_media_assets(
        self,
        prompt: str,
        model: str = "",
        instructions: str = "",
        duration: Optional[int] = None,
        ratio: str = "",
        watermark: Optional[bool] = None,
        generate_audio: Optional[bool] = None,
        url_expires_in: int = 3600,
        chat_id: str = "",
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 3,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        一步完成：使用 `%素材路径` 提交生成任务并等待终态。
        """
        submit_raw = await self.generate_video_with_media_assets(
            prompt=prompt,
            model=model,
            instructions=instructions,
            duration=duration,
            ratio=ratio,
            watermark=watermark,
            generate_audio=generate_audio,
            url_expires_in=url_expires_in,
            chat_id=chat_id,
            __request__=__request__,
            __user__=__user__,
        )
        try:
            submit_payload = json.loads(submit_raw)
        except Exception:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 500,
                    "error_code": "InvalidToolPayload",
                    "error_message": "Failed to parse media generate response payload",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        if not submit_payload.get("ok"):
            return json.dumps(submit_payload, ensure_ascii=False)

        task_id = (
            submit_payload.get("response_id")
            or submit_payload.get("task_id")
            or (submit_payload.get("raw_response") or {}).get("id")
        )
        if not task_id:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 502,
                    "error_code": "MissingTaskId",
                    "error_message": "Generate call succeeded but task_id(response_id) is missing",
                    "request_id": None,
                    "references": submit_payload.get("references", []),
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

        wait_payload["references"] = submit_payload.get("references", [])
        wait_payload["response_id"] = task_id
        wait_payload["submit_status"] = submit_payload.get("status")
        wait_payload["resolved_assets"] = submit_payload.get("resolved_assets", [])
        wait_payload["raw_submit_response"] = submit_payload.get("raw_response")
        return json.dumps(wait_payload, ensure_ascii=False)

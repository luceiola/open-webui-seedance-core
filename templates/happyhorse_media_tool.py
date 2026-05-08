"""
title: HappyHorse Media Tool
author: local-dev
version: 0.1.0
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import Request
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
        DEFAULT_HAPPYHORSE_MODEL: str = Field(
            default="happyhorse-1.0-r2v",
            description="默认 HappyHorse 模型 ID",
        )
        DEFAULT_HAPPYHORSE_RESOLUTION: str = Field(
            default="720P",
            description="默认分辨率（未指定时）",
        )
        DEFAULT_HAPPYHORSE_RATIO: str = Field(
            default="9:16",
            description="默认宽高比（未指定时）",
        )
        DEFAULT_HAPPYHORSE_DURATION: int = Field(
            default=5,
            ge=3,
            le=15,
            description="默认时长（秒，未指定时）",
        )
        DEFAULT_HAPPYHORSE_WATERMARK: bool = Field(
            default=False,
            description="默认水印开关（未指定时）",
        )
        DASHSCOPE_BASE_URL: str = Field(
            default="https://dashscope.aliyuncs.com/api/v1",
            description="DashScope API Base URL",
        )
        REQUEST_TIMEOUT_SECONDS: int = Field(default=180, ge=30, le=600)
        TASK_POLL_INTERVAL_SECONDS: int = Field(default=15, ge=5, le=60)
        TASK_MAX_WAIT_SECONDS: int = Field(default=1200, ge=60, le=7200)

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

    def _extract_request_id(self, text: str) -> Optional[str]:
        if not text:
            return None
        m = re.search(r"request[_ ]id\s*[:=]\s*([A-Za-z0-9_-]+)", text, flags=re.IGNORECASE)
        return m.group(1) if m else None

    def _normalize_error(self, response: httpx.Response) -> dict[str, Any]:
        raw_text = response.text
        code: Optional[str] = None
        message: Optional[str] = None
        request_id: Optional[str] = None

        try:
            payload = response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            code = payload.get("code")
            message = payload.get("message")
            request_id = payload.get("request_id")

            detail = payload.get("detail")
            if isinstance(detail, dict):
                code = code or detail.get("code")
                message = message or detail.get("message") or detail.get("error")
                request_id = request_id or detail.get("request_id")
            elif isinstance(detail, str):
                message = message or detail
                request_id = request_id or self._extract_request_id(detail)

            output = payload.get("output")
            if isinstance(output, dict):
                code = code or output.get("code")
                message = message or output.get("message")

        if not message:
            message = raw_text
        if not request_id:
            request_id = self._extract_request_id(message or "")

        return {
            "ok": False,
            "status_code": response.status_code,
            "error": raw_text,
            "error_code": code,
            "error_message": message,
            "request_id": request_id,
        }

    def _read_env_value_from_file(self, key: str) -> Optional[str]:
        candidates: list[Path] = []
        env_path = (os.getenv("DASHSCOPE_ENV_FILE") or "").strip()
        if env_path:
            candidates.append(Path(env_path).expanduser())

        candidates.extend(
            [
                Path.cwd() / "config" / "happyhorse.env",
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

    def _get_dashscope_api_key(self) -> str:
        return (os.getenv("DASHSCOPE_API_KEY") or self._read_env_value_from_file("DASHSCOPE_API_KEY") or "").strip()

    def _get_dashscope_base_url(self) -> str:
        base_url = (
            os.getenv("DASHSCOPE_BASE_URL")
            or self._read_env_value_from_file("DASHSCOPE_BASE_URL")
            or self.valves.DASHSCOPE_BASE_URL
        )
        base = str(base_url).strip().rstrip("/")
        if "/api/" not in base:
            base = f"{base}/api/v1"
        return base

    async def _request(
        self,
        method: str,
        path: str,
        __request__: Optional[Request],
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url(__request__)}{path}"
        headers = self._headers(__request__)

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
        __request__: Optional[Request] = None,
    ) -> bool:
        tid = (task_id or "").strip()
        if not tid:
            return False

        payload: dict[str, Any] = {
            "task_id": tid,
            "provider": "happyhorse",
            "provider_task_id": tid,
            "tool_name": "happyhorse_media_tool.generate_video_with_happyhorse",
            "skill_name": "happyhorse",
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

        bridge = await self._request("POST", "/api/v1/tasks/bridge/upsert", __request__, payload)
        return bool(bridge.get("ok"))

    def _extract_media_asset_references(self, prompt: str) -> list[str]:
        refs = re.findall(r"%([^\s%,，。；;:：!！?？)）\]】}》>\"“”'`]+)", prompt or "")
        cleaned: list[str] = []
        for ref in refs:
            value = ref.strip().rstrip(".,;:!?)\\]}>'\"")
            if value:
                cleaned.append(value)
        return list(dict.fromkeys(cleaned))

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

    def _normalize_resolution(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = str(value or "").strip()
        if not raw:
            return None, None
        normalized = raw.upper()
        if normalized in {"720P", "1080P"}:
            return normalized, None
        return None, "parameters.resolution must be one of: 720P, 1080P"

    def _normalize_ratio(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = str(value or "").strip()
        if not raw:
            return None, None
        normalized = raw.replace("：", ":")
        allowed = {"16:9", "9:16", "3:4", "4:3", "1:1"}
        if normalized in allowed:
            return normalized, None
        return None, "parameters.ratio must be one of: 16:9, 9:16, 3:4, 4:3, 1:1"

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

    async def generate_video_with_happyhorse(
        self,
        prompt: str,
        model: str = "",
        resolution: str = "",
        ratio: str = "",
        duration: Optional[int] = None,
        watermark: Optional[bool] = None,
        seed: Optional[int] = None,
        chat_id: str = "",
        url_expires_in: int = 3600,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        使用 `%素材路径` 引用图片素材，提交 HappyHorse 参考生视频任务。
        """
        model_id = (model or "").strip() or self.valves.DEFAULT_HAPPYHORSE_MODEL
        if "happyhorse" not in model_id.lower():
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "Only happyhorse models are supported in this tool.",
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
                    "error_message": "Missing or ambiguous referenced media assets",
                    "request_id": None,
                    "missing_references": missing,
                    "ambiguous_references": ambiguous,
                    "available_references": resolve_data.get("available_references") or [],
                },
                ensure_ascii=False,
            )

        if not refs:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "HappyHorse requires at least one %素材路径 reference image.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        resolved_assets = [item for item in (resolve_data.get("assets") or []) if isinstance(item, dict)]
        unsupported_refs: list[dict[str, Any]] = []
        media_items: list[dict[str, Any]] = []
        character_mapping: list[dict[str, Any]] = []

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
                        "error_message": "HappyHorse supports reference_image only.",
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
            if not isinstance(media_url, str) or not media_url.startswith(("http://", "https://")):
                unsupported_refs.append(
                    {
                        "reference": ref_name,
                        "asset_id": asset_id,
                        "media_type": media_type,
                        "error_message": "Invalid media asset url",
                    }
                )
                continue

            media_items.append({"type": "reference_image", "url": media_url})
            character_mapping.append(
                {
                    "character": f"character{idx}",
                    "reference": ref_name,
                    "asset_id": asset_id,
                }
            )

        if unsupported_refs:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "UnsupportedHappyHorseReferences",
                    "error_message": "HappyHorse accepts image references only, or some reference urls are invalid.",
                    "request_id": None,
                    "unsupported_references": unsupported_refs,
                },
                ensure_ascii=False,
            )

        transformed_prompt = self._replace_refs_with_characters(prompt=prompt, references=refs)
        if len(media_items) > 9:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "HappyHorse supports at most 9 reference images.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        api_key = self._get_dashscope_api_key()
        if not api_key:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": None,
                    "error_message": "DASHSCOPE_API_KEY is not configured",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        payload: dict[str, Any] = {
            "model": model_id,
            "input": {
                "prompt": transformed_prompt,
                "media": media_items,
            },
        }

        parameters: dict[str, Any] = {}
        resolution_input = resolution if str(resolution or "").strip() else self.valves.DEFAULT_HAPPYHORSE_RESOLUTION
        normalized_resolution, resolution_err = self._normalize_resolution(resolution_input)
        if resolution_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": resolution_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        parameters["resolution"] = normalized_resolution

        ratio_input = ratio if str(ratio or "").strip() else self.valves.DEFAULT_HAPPYHORSE_RATIO
        normalized_ratio, ratio_err = self._normalize_ratio(ratio_input)
        if ratio_err:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": ratio_err,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        parameters["ratio"] = normalized_ratio

        duration_input = int(duration) if duration is not None else int(self.valves.DEFAULT_HAPPYHORSE_DURATION)
        if duration_input < 3 or duration_input > 15:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "parameters.duration must be an integer between 3 and 15",
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        parameters["duration"] = duration_input

        watermark_input = bool(watermark) if watermark is not None else bool(self.valves.DEFAULT_HAPPYHORSE_WATERMARK)
        parameters["watermark"] = watermark_input

        if seed is not None:
            seed_int = int(seed)
            if seed_int < 0 or seed_int > 2147483647:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": "parameters.seed must be in [0, 2147483647]",
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            parameters["seed"] = seed_int

        payload["parameters"] = parameters

        base_url = self._get_dashscope_base_url()
        url = f"{base_url}/services/aigc/video-generation/video-synthesis"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

        async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code >= 400:
            return json.dumps(self._normalize_error(resp), ensure_ascii=False)

        try:
            response_json = resp.json()
        except Exception:
            response_json = {"raw_text": resp.text}

        output = response_json.get("output", {}) if isinstance(response_json.get("output"), dict) else {}
        task_id = output.get("task_id") or response_json.get("task_id")
        task_status = output.get("task_status") or response_json.get("task_status") or "PENDING"

        result = {
            "ok": True,
            "references": refs,
            "character_mapping": character_mapping,
            "response_id": task_id,
            "task_id": task_id,
            "status": task_status,
            "video_url": None,
            "video_url_markdown": "暂无",
            "raw_response": response_json,
        }

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
                    __request__=__request__,
                )
            except Exception:
                pass
        return json.dumps(result, ensure_ascii=False)

    async def get_happyhorse_task_status(
        self,
        task_id: str,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        查询 HappyHorse 任务状态，并提取 video_url。
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

        api_key = self._get_dashscope_api_key()
        if not api_key:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": None,
                    "error_message": "DASHSCOPE_API_KEY is not configured",
                    "request_id": None,
                    "task_id": tid,
                },
                ensure_ascii=False,
            )

        base_url = self._get_dashscope_base_url()
        url = f"{base_url}/tasks/{tid}"
        headers = {"Authorization": f"Bearer {api_key}"}

        async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code >= 400:
            err = self._normalize_error(resp)
            err["task_id"] = tid
            try:
                await self._bridge_upsert_task(
                    task_id=tid,
                    status="FAILED",
                    error_code=str(err.get("error_code") or "").strip() or None,
                    error_message=str(err.get("error_message") or err.get("error") or "").strip() or None,
                    request_id=str(err.get("request_id") or "").strip() or None,
                    __request__=__request__,
                )
            except Exception:
                pass
            return json.dumps(err, ensure_ascii=False)

        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": resp.text}

        output = data.get("output", {}) if isinstance(data.get("output"), dict) else {}
        task_status = output.get("task_status") or data.get("task_status")
        video_url = self._normalize_video_url(output.get("video_url"))

        payload = {
            "ok": True,
            "task_id": output.get("task_id") or tid,
            "status": task_status,
            "video_url": video_url,
            "video_url_markdown": self._to_video_url_markdown_or_na(video_url),
            "raw_response": data,
            "request_id": data.get("request_id"),
            "error_code": output.get("code"),
            "error_message": output.get("message"),
        }
        try:
            await self._bridge_upsert_task(
                task_id=str(payload.get("task_id") or tid),
                status=str(task_status or ""),
                raw_last_response=data if isinstance(data, dict) else None,
                video_url=video_url,
                request_id=str(payload.get("request_id") or "").strip() or None,
                error_code=str(payload.get("error_code") or "").strip() or None,
                error_message=str(payload.get("error_message") or "").strip() or None,
                __request__=__request__,
            )
        except Exception:
            pass
        return json.dumps(payload, ensure_ascii=False)

    async def wait_happyhorse_task(
        self,
        task_id: str,
        timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        轮询 HappyHorse 任务，直到终态或超时。
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
        timeout = max(60, min(timeout, 7200))
        poll = max(5, min(poll, 60))

        start = time.monotonic()
        last_payload: dict[str, Any] = {}
        terminal = {"succeeded", "failed", "canceled", "cancelled", "unknown"}

        while True:
            status_raw = await self.get_happyhorse_task_status(
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
                        "video_url": last_payload.get("video_url"),
                        "video_url_markdown": self._to_video_url_markdown_or_na(last_payload.get("video_url")),
                        "raw_response": last_payload.get("raw_response"),
                        "error_code": "GenerationTaskPollingTimeout",
                        "error_message": "Generation task polling timeout",
                        "request_id": None,
                        "elapsed_seconds": int(elapsed),
                    },
                    ensure_ascii=False,
                )

            await asyncio.sleep(poll)

    async def generate_and_wait_with_happyhorse(
        self,
        prompt: str,
        model: str = "",
        resolution: str = "",
        ratio: str = "",
        duration: Optional[int] = None,
        watermark: Optional[bool] = None,
        seed: Optional[int] = None,
        chat_id: str = "",
        url_expires_in: int = 3600,
        timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        提交 HappyHorse 任务并等待终态。
        """
        submit_raw = await self.generate_video_with_happyhorse(
            prompt=prompt,
            model=model,
            resolution=resolution,
            ratio=ratio,
            duration=duration,
            watermark=watermark,
            seed=seed,
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

        wait_raw = await self.wait_happyhorse_task(
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

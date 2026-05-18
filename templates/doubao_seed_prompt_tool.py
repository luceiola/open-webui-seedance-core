"""
title: Doubao Seed Prompt Tool
author: local-dev
version: 0.4.0
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.append(str(_TOOL_DIR))

from shared.template_registry import (
    DEFAULT_MISSING_FIELD_POLICY as SHARED_DEFAULT_MISSING_FIELD_POLICY,
    DEFAULT_STORYBOARD_TEMPLATE_ID as SHARED_DEFAULT_STORYBOARD_TEMPLATE_ID,
    TEMPLATE_REGISTRY as SHARED_TEMPLATE_REGISTRY,
    list_template_catalog,
    match_template,
    normalize_template_id,
)
from shared.toolkit import (
    build_auth_headers,
    build_base_url,
    compact_media_asset_item,
    extract_media_asset_references,
    extract_request_id,
    normalize_httpx_error,
    request_openwebui_json,
)


class Tools:
    VIDEO_FPS_MIN = 0.2
    VIDEO_FPS_MAX = 5.0
    IMAGE_MIN_PIXELS_FLOOR = 196
    IMAGE_MAX_PIXELS_CEILING = 36_000_000
    DEFAULT_STORYBOARD_TEMPLATE_ID = SHARED_DEFAULT_STORYBOARD_TEMPLATE_ID
    DEFAULT_MISSING_FIELD_POLICY = SHARED_DEFAULT_MISSING_FIELD_POLICY
    TEMPLATE_REGISTRY: dict[str, dict[str, Any]] = SHARED_TEMPLATE_REGISTRY

    class Valves(BaseModel):
        OPENWEBUI_BASE_URL: str = Field(
            default="http://127.0.0.1:8080",
            description="OpenWebUI base url; used when request context is unavailable.",
        )
        OPENWEBUI_API_KEY: str = Field(
            default="",
            description="Fallback OpenWebUI API key when request context has no auth headers.",
        )
        ARK_BASE_URL: str = Field(
            default="https://ark.cn-beijing.volces.com/api/v3",
            description="ARK base url.",
        )
        DEFAULT_MODEL: str = Field(
            default="doubao-seed-2-0-pro-260215",
            description="Ark model id or endpoint id (recommended: ep-xxxxxx).",
        )
        REQUEST_TIMEOUT_SECONDS: int = Field(default=120, ge=30, le=600)
        DEFAULT_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=2.0)
        DEFAULT_TOP_P: float = Field(default=0.95, ge=0.0, le=1.0)
        DEFAULT_MAX_OUTPUT_TOKENS: int = Field(default=1200, ge=64, le=4096)

        DEFAULT_IMAGE_DETAIL: str = Field(
            default="high",
            description="Default image detail level for image_url input.",
        )
        DEFAULT_VIDEO_FPS: float = Field(
            default=1.0,
            ge=0.2,
            le=5.0,
            description="Default fps used when video reference is provided.",
        )
        DEFAULT_AUDIO_FORMAT: str = Field(
            default="mp3",
            description="Default audio format for base64 audio input.",
        )

        MAX_INLINE_IMAGE_BYTES: int = Field(
            default=50 * 1024 * 1024,
            ge=1 * 1024 * 1024,
            le=200 * 1024 * 1024,
            description="Safety cap for inline image base64/data-URI payload size.",
        )
        MAX_INLINE_VIDEO_BYTES: int = Field(
            default=50 * 1024 * 1024,
            ge=1 * 1024 * 1024,
            le=200 * 1024 * 1024,
            description="Safety cap for inline video base64/data-URI payload size.",
        )
        MAX_INLINE_AUDIO_BYTES: int = Field(
            default=25 * 1024 * 1024,
            ge=1 * 1024 * 1024,
            le=100 * 1024 * 1024,
            description="Safety cap for inline audio payload size.",
        )
        FORCE_MEDIA_ASSET_TOS: bool = Field(
            default=True,
            description="When true, multimedia references must be resolved from media-assets TOS URLs by asset_id.",
        )
        MEDIA_ASSET_URL_EXPIRES_IN: int = Field(
            default=3600,
            ge=60,
            le=604800,
            description="Temporary URL ttl seconds for /api/v1/media-assets/{asset_id}/url.",
        )
        ENFORCE_KEY_ROUTING: bool = Field(
            default=True,
            description="When true, resolve ARK key by backend key routing (group -> alias -> env).",
        )
        KEY_ROUTING_PROVIDER: str = Field(
            default="seedance",
            description="Provider name used in key_routing.json.",
        )
        KEY_ROUTING_PREFERRED_ALIAS: str = Field(
            default="",
            description="Optional preferred alias override for debugging.",
        )
        OPTIMIZER_ENABLED: bool = Field(
            default=True,
            description="Enable KB-backed optimizer capability in this merged agent.",
        )
        OPTIMIZER_FIXED_MODEL: str = Field(
            default="seedance-2.0",
            description="Fixed model label for optimizer mode; cannot be overridden by user input.",
        )
        OPTIMIZER_KB_NAMES: str = Field(
            default="KB-01-规则库,KB-02-模板库",
            description="Comma-separated knowledge base names used by optimizer retrieval.",
        )
        OPTIMIZER_KB_MIN_EVIDENCE: int = Field(
            default=2,
            ge=1,
            le=20,
            description="Minimum KB evidence items required to return optimized output.",
        )
        OPTIMIZER_KB_QUERY_TOP_K: int = Field(
            default=8,
            ge=2,
            le=50,
            description="Top-K retrieval size for KB query.",
        )
        OPTIMIZER_KB_MIN_DISTANCE: float = Field(
            default=0.2,
            ge=0.0,
            le=1.0,
            description="Minimum retrieval distance/score threshold used to keep KB evidence.",
        )
        OPTIMIZER_KB_VERSION: str = Field(
            default="kb-unset",
            description="Knowledge version label included in optimizer output trace.",
        )
        DESCRIPTION_DEFAULT_GRANULARITY: str = Field(
            default="brief",
            description="Default granularity for media description mode: brief or detailed.",
        )
        DESCRIPTION_DEFAULT_OUTPUT_FORMAT: str = Field(
            default="text",
            description="Default output format for media description mode: text or structured.",
        )
        DESCRIPTION_DEFAULT_FOCUS: str = Field(
            default="people,scene,motion,camera,style",
            description="Default focus dimensions for media description mode.",
        )
        DESCRIPTION_ENABLE_KB_ENHANCEMENT: bool = Field(
            default=False,
            description="Enable optional KB enhancement for media description mode.",
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

    def _extract_request_id(self, text: str) -> Optional[str]:
        return extract_request_id(text)

    def _normalize_error(self, response: httpx.Response) -> dict[str, Any]:
        return normalize_httpx_error(response)

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

        if not request_id:
            request_id = self._extract_request_id(error_message or "")

        return {
            "ok": False,
            "status_code": status_code,
            "error": raw,
            "error_code": error_code,
            "error_message": error_message or raw,
            "request_id": request_id,
        }

    async def _resolve_ark_credential(
        self,
        *,
        __user__: Optional[dict],
        preferred_alias: str = "",
    ) -> dict[str, Any]:
        preferred_alias_value = (preferred_alias or "").strip() or str(self.valves.KEY_ROUTING_PREFERRED_ALIAS or "").strip()
        provider = str(self.valves.KEY_ROUTING_PROVIDER or "seedance").strip().lower() or "seedance"

        if not bool(self.valves.ENFORCE_KEY_ROUTING):
            api_key = self._get_ark_api_key()
            if not api_key:
                return {
                    "ok": False,
                    "status_code": 400,
                    "error": "ARK_API_KEY is not configured",
                    "error_code": None,
                    "error_message": "ARK_API_KEY is not configured",
                    "request_id": None,
                }
            return {
                "ok": True,
                "provider": provider,
                "credential_alias": "legacy_env",
                "routing_group_id": None,
                "api_key": api_key,
                "source": "legacy_env",
            }

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
        except Exception as e:
            return {
                "ok": False,
                "status_code": 500,
                "error": str(e),
                "error_code": "KEY_ROUTING_RESOLVE_FAILED",
                "error_message": f"Failed to import key routing resolver: {e}",
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
        except Exception as e:
            return {
                "ok": False,
                "status_code": 500,
                "error": str(e),
                "error_code": "KEY_ROUTING_RESOLVE_FAILED",
                "error_message": f"Failed to resolve key routing credential: {e}",
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

    def _normalize_http_url(self, value: Any) -> Optional[str]:
        url = str(value or "").strip()
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            return None
        return url

    async def _internal_request(
        self,
        *,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        __request__: Optional[Request],
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

    def _split_csv_values(self, raw: str) -> list[str]:
        values = [item.strip() for item in str(raw or "").split(",")]
        values = [item for item in values if item]
        return list(dict.fromkeys(values))

    async def _resolve_optimizer_kb_collections(
        self,
        *,
        __request__: Optional[Request],
    ) -> tuple[list[dict[str, Any]], list[str], Optional[str]]:
        kb_names = self._split_csv_values(self.valves.OPTIMIZER_KB_NAMES)
        if not kb_names:
            return [], [], "OPTIMIZER_KB_NAMES is empty"

        resolved: list[dict[str, Any]] = []
        missing: list[str] = []
        for kb_name in kb_names:
            query = quote(kb_name, safe="")
            search = await self._internal_request(
                method="GET",
                path=f"/api/v1/knowledge/search?query={query}&page=1",
                __request__=__request__,
            )
            if not search.get("ok"):
                return [], [], (
                    f"Failed to search knowledge base {kb_name}: "
                    f"{search.get('error_code') or search.get('status_code')} {search.get('error_message')}"
                )

            payload = search.get("data") if isinstance(search.get("data"), dict) else {}
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            exact_match: Optional[dict[str, Any]] = None
            fuzzy_match: Optional[dict[str, Any]] = None

            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                if name == kb_name:
                    exact_match = item
                    break
                if fuzzy_match is None and kb_name in name:
                    fuzzy_match = item

            picked = exact_match or fuzzy_match
            if not picked:
                missing.append(kb_name)
                continue

            kb_id = str(picked.get("id") or "").strip()
            kb_name_picked = str(picked.get("name") or kb_name).strip() or kb_name
            if not kb_id:
                missing.append(kb_name)
                continue
            resolved.append({"id": kb_id, "name": kb_name_picked})

        return resolved, missing, None

    def _build_optimizer_query_text(
        self,
        *,
        raw_prompt: str,
        material_references: str,
        shot_script: str,
        style_hint: str,
        kb_query: str,
    ) -> str:
        query = (kb_query or "").strip()
        if query:
            return query
        parts = [
            (raw_prompt or "").strip(),
            (material_references or "").strip(),
            (shot_script or "").strip(),
            (style_hint or "").strip(),
        ]
        joined = "\n\n".join([part for part in parts if part])
        return joined.strip()

    async def _query_optimizer_kb_evidence(
        self,
        *,
        collection_names: list[str],
        query_text: str,
        __request__: Optional[Request],
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        if not collection_names:
            return [], "No collections were provided"

        body = {
            "collection_names": collection_names,
            "query": query_text,
            "k": int(self.valves.OPTIMIZER_KB_QUERY_TOP_K),
        }
        result = await self._internal_request(
            method="POST",
            path="/api/v1/retrieval/query/collection",
            body=body,
            __request__=__request__,
        )
        if not result.get("ok"):
            return [], (
                f"Failed to query retrieval collections: "
                f"{result.get('error_code') or result.get('status_code')} {result.get('error_message')}"
            )

        payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        distances_raw = payload.get("distances")
        documents_raw = payload.get("documents")
        metadatas_raw = payload.get("metadatas")

        distances = distances_raw[0] if isinstance(distances_raw, list) and distances_raw else []
        documents = documents_raw[0] if isinstance(documents_raw, list) and documents_raw else []
        metadatas = metadatas_raw[0] if isinstance(metadatas_raw, list) and metadatas_raw else []
        count = min(len(distances), len(documents), len(metadatas))

        rows: list[dict[str, Any]] = []
        for idx in range(count):
            text = str(documents[idx] or "").strip()
            if not text:
                continue
            metadata = metadatas[idx] if isinstance(metadatas[idx], dict) else {}
            try:
                distance = float(distances[idx])
            except Exception:
                distance = 0.0

            source_ref = (
                str(metadata.get("file_id") or "").strip()
                or str(metadata.get("source") or "").strip()
                or str(metadata.get("name") or "").strip()
                or str(metadata.get("title") or "").strip()
                or "unknown"
            )
            rows.append(
                {
                    "distance": distance,
                    "text": text,
                    "metadata": metadata,
                    "source_ref": source_ref,
                }
            )

        return rows, None

    def _prepare_optimizer_evidence(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        min_distance = float(self.valves.OPTIMIZER_KB_MIN_DISTANCE)
        dedup: dict[str, dict[str, Any]] = {}
        for row in rows:
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            distance = float(row.get("distance") or 0.0)
            if distance < min_distance:
                continue

            key = text
            current = dedup.get(key)
            if current is None or float(current.get("distance") or 0.0) < distance:
                dedup[key] = row

        kept = list(dedup.values())
        kept.sort(key=lambda item: float(item.get("distance") or 0.0), reverse=True)
        return kept

    def _clip_text(self, value: str, max_chars: int = 400) -> str:
        text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "..."

    def _coerce_text_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            rows = [str(item).strip() for item in value if str(item).strip()]
            return rows
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _extract_json_object(self, text: str) -> Optional[dict[str, Any]]:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

        left = raw.find("{")
        right = raw.rfind("}")
        if left < 0 or right <= left:
            return None
        candidate = raw[left : right + 1]
        try:
            parsed = json.loads(candidate)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _build_optimizer_messages(
        self,
        *,
        fixed_model: str,
        language: str,
        raw_prompt: str,
        material_references: str,
        shot_script: str,
        style_hint: str,
        keep_length: str,
        evidence: list[dict[str, Any]],
        kb_scope_names: list[str],
    ) -> list[dict[str, Any]]:
        evidence_lines: list[str] = []
        for idx, item in enumerate(evidence, start=1):
            evidence_lines.append(
                f"[{idx}] score={round(float(item.get('distance') or 0.0), 4)} "
                f"source={item.get('source_ref')}\n"
                f"{self._clip_text(str(item.get('text') or ''), 420)}"
            )

        system_text = (
            "You are a strict video prompt optimizer.\n"
            "You must rely on provided KB evidence and optimize the prompt for Seedance 2.0.\n"
            "Output must be valid JSON only (no markdown, no explanation outside JSON).\n"
            "Required JSON fields:\n"
            "- model (string)\n"
            "- optimized_prompt (string)\n"
            "- negative_prompt (string, can be empty)\n"
            "- reasoning (array of string)\n"
            "- risk_checks (array of string)\n"
            "- readable_summary (string)\n"
            "If evidence is insufficient, you must not invent facts.\n"
        )

        user_parts = [
            f"target_model={fixed_model}",
            f"language={language or 'zh'}",
            f"kb_scope={','.join(kb_scope_names)}",
            f"raw_prompt:\n{(raw_prompt or '').strip()}",
            f"material_references:\n{(material_references or '').strip() or '(none)'}",
            f"shot_script:\n{(shot_script or '').strip() or '(none)'}",
        ]
        if (style_hint or "").strip():
            user_parts.append(f"style_hint:\n{style_hint.strip()}")
        if (keep_length or "").strip():
            user_parts.append(f"keep_length:\n{keep_length.strip()}")
        user_parts.append("kb_evidence:\n" + ("\n\n".join(evidence_lines) if evidence_lines else "(none)"))
        return [
            {"role": "system", "content": system_text},
            {"role": "user", "content": [{"type": "text", "text": "\n\n".join(user_parts)}]},
        ]

    async def _resolve_media_asset_to_url(
        self,
        *,
        asset_id: str,
        expected_media_type: str,
        __request__: Optional[Request],
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        aid = (asset_id or "").strip()
        if not aid:
            return None, None

        detail = await self._internal_request(
            method="GET",
            path=f"/api/v1/media-assets/{quote(aid, safe='')}",
            __request__=__request__,
        )
        if not detail.get("ok"):
            return None, (
                f"Failed to load media asset {aid}: "
                f"{detail.get('error_code') or detail.get('status_code')} {detail.get('error_message')}"
            )

        row = detail.get("data") if isinstance(detail.get("data"), dict) else {}
        media_type = str(row.get("media_type") or "").strip().lower()
        if expected_media_type and media_type != expected_media_type:
            return None, (
                f"Media asset {aid} type mismatch: expected {expected_media_type}, got {media_type or 'unknown'}"
            )

        expires_in = int(self.valves.MEDIA_ASSET_URL_EXPIRES_IN)
        url_res = await self._internal_request(
            method="GET",
            path=f"/api/v1/media-assets/{quote(aid, safe='')}/url?expires_in={expires_in}",
            __request__=__request__,
        )
        if not url_res.get("ok"):
            return None, (
                f"Failed to build TOS url for media asset {aid}: "
                f"{url_res.get('error_code') or url_res.get('status_code')} {url_res.get('error_message')}"
            )

        data = url_res.get("data") if isinstance(url_res.get("data"), dict) else {}
        url = self._normalize_http_url(data.get("url"))
        if not url:
            return None, f"Media asset {aid} returned invalid url"

        return {
            "asset_id": aid,
            "media_type": media_type,
            "url": url,
            "expires_in": int(data.get("expires_in") or expires_in),
        }, None

    def _media_asset_reference_candidates(self, item: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("relative_path", "display_name", "original_filename"):
            value = str(item.get(key) or "").strip()
            if value:
                candidates.append(value)
        return list(dict.fromkeys(candidates))

    def _normalize_reference_token(self, raw: str) -> Optional[str]:
        value = str(raw or "").strip()
        if not value:
            return None
        if value.startswith("%"):
            token = value[1:].strip().rstrip(".,;:!?)\\]}>'\"，。；：！？】）》")
            return token or None
        return None

    def _looks_like_media_reference_name(self, raw: str) -> bool:
        value = str(raw or "").strip()
        if not value:
            return False
        lowered = value.lower()
        if lowered.startswith(("asset_", "http://", "https://", "data:", "file_id://", "asset://", "tos://")):
            return False
        if re.fullmatch(r"[a-z0-9_-]{16,}", lowered):
            return False
        return any(ch in value for ch in (".", "/", "\\"))

    def _normalize_media_reference_inputs(
        self,
        *,
        asset_id: str,
        ref_url: str,
    ) -> tuple[str, str, Optional[str]]:
        aid = str(asset_id or "").strip()
        raw_url = str(ref_url or "").strip()
        ref_token = self._normalize_reference_token(raw_url)

        if aid and not raw_url:
            # Tolerate common tool-call mapping mistakes:
            # 1) user passed %reference_name into *_asset_id
            # 2) user passed filename/reference-name into *_asset_id
            token_from_asset = self._normalize_reference_token(aid)
            if token_from_asset:
                raw_url = aid
                aid = ""
                ref_token = token_from_asset
            elif self._looks_like_media_reference_name(aid):
                cleaned = aid.rstrip(".,;:!?)\\]}>'\"，。；：！？】）》")
                if cleaned:
                    aid = ""
                    ref_token = cleaned

        return aid, raw_url, ref_token

    def _extract_media_asset_references(self, prompt: str) -> list[str]:
        return extract_media_asset_references(prompt)

    def _compact_media_asset_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return compact_media_asset_item(item)

    def _normalize_description_granularity(self, value: str) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            raw = str(self.valves.DESCRIPTION_DEFAULT_GRANULARITY or "brief").strip().lower()

        mapping = {
            "brief": "brief",
            "short": "brief",
            "summary": "brief",
            "simple": "brief",
            "大致": "brief",
            "简略": "brief",
            "简要": "brief",
            "detailed": "detailed",
            "detail": "detailed",
            "full": "detailed",
            "verbose": "detailed",
            "详细": "detailed",
            "细致": "detailed",
            "完整": "detailed",
        }
        return mapping.get(raw, "brief")

    def _normalize_description_output_format(self, value: str) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            raw = str(self.valves.DESCRIPTION_DEFAULT_OUTPUT_FORMAT or "text").strip().lower()
        if raw in {"structured", "json", "schema"}:
            return "structured"
        return "text"

    def _normalize_description_focus(self, value: str) -> list[str]:
        raw = str(value or "").strip()
        if not raw:
            raw = str(self.valves.DESCRIPTION_DEFAULT_FOCUS or "").strip()

        chunks = re.split(r"[,，/|、\s]+", raw)
        mapping = {
            "人物": "people",
            "角色": "people",
            "人": "people",
            "people": "people",
            "scene": "scene",
            "场景": "scene",
            "环境": "scene",
            "motion": "motion",
            "动作": "motion",
            "运动": "motion",
            "camera": "camera",
            "镜头": "camera",
            "运镜": "camera",
            "audio": "audio",
            "声音": "audio",
            "音频": "audio",
            "style": "style",
            "风格": "style",
            "lighting": "lighting",
            "光线": "lighting",
            "props": "props",
            "道具": "props",
            "custom": "custom",
            "其他": "custom",
            "overall": "overall",
            "整体": "overall",
        }
        normalized: list[str] = []
        for chunk in chunks:
            key = str(chunk or "").strip().lower()
            if not key:
                continue
            normalized_value = mapping.get(key, "custom")
            normalized.append(normalized_value)

        if not normalized:
            normalized = ["overall"]
        return list(dict.fromkeys(normalized))

    def _list_template_catalog(self) -> list[dict[str, Any]]:
        return list_template_catalog()

    def _normalize_template_id(self, value: str) -> Optional[str]:
        normalized = normalize_template_id(value)
        return normalized or None

    def _match_template_from_request(
        self,
        *,
        description_request: str,
        template_id: str,
        enforce_template_output: bool,
    ) -> Optional[dict[str, Any]]:
        return match_template(
            description_request=description_request,
            template_id=template_id,
            enforce_template_output=enforce_template_output,
        )

    def _normalize_template_text_value(self, value: Any, *, missing_policy: str) -> str:
        if isinstance(value, str):
            text = value.strip()
            return text or missing_policy
        if isinstance(value, (int, float)):
            text = str(value).strip()
            return text or missing_policy
        if isinstance(value, list):
            rows = [str(item).strip() for item in value if str(item).strip()]
            if rows:
                return "；".join(rows)
        return missing_policy

    def _pick_template_value(self, source: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            if key in source:
                value = source.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(value, list) and not value:
                    continue
                if isinstance(value, dict) and not value:
                    continue
                return value
        return None

    def _coerce_dict_rows(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            rows: list[dict[str, Any]] = []
            for item in value:
                if isinstance(item, dict):
                    rows.append(item)
            return rows
        return []

    def _normalize_storyboard_structured(
        self,
        *,
        payload: Optional[dict[str, Any]],
        provider_text: str,
        missing_policy: str,
    ) -> dict[str, Any]:
        source = payload if isinstance(payload, dict) else {}
        is_empty_source = not source

        video_type = self._normalize_template_text_value(
            self._pick_template_value(source, ["视频类型", "video_type", "videoType", "type"]),
            missing_policy=missing_policy,
        )
        total_duration = self._normalize_template_text_value(
            self._pick_template_value(source, ["总时长", "total_duration", "duration"]),
            missing_policy=missing_policy,
        )
        core_theme = self._normalize_template_text_value(
            self._pick_template_value(source, ["核心主题", "core_theme", "theme"]),
            missing_policy=missing_policy,
        )
        scene_summary = self._normalize_template_text_value(
            self._pick_template_value(source, ["场景概况", "scene_summary", "scene"]),
            missing_policy=missing_policy,
        )
        environment_value = self._pick_template_value(source, ["环境描述", "environment", "environment_description"])
        if environment_value is None and is_empty_source:
            environment_value = self._clip_text(provider_text, 800)
        environment_description = self._normalize_template_text_value(
            environment_value,
            missing_policy=missing_policy,
        )

        people_rows = self._coerce_dict_rows(
            self._pick_template_value(source, ["人物设定", "人物列表", "people", "characters"])
        )
        if not people_rows:
            people_rows = [{}]
        normalized_people: list[dict[str, Any]] = []
        for idx, row in enumerate(people_rows, start=1):
            person_id_raw = self._pick_template_value(row, ["人物ID", "person_id", "id", "character_id"])
            if person_id_raw is None:
                person_id = str(idx)
            else:
                person_id = self._normalize_template_text_value(person_id_raw, missing_policy=missing_policy)
            normalized_people.append(
                {
                    "人物ID": person_id,
                    "人物名称": self._normalize_template_text_value(
                        self._pick_template_value(row, ["人物名称", "name", "character_name"]),
                        missing_policy=missing_policy,
                    ),
                    "年龄/性别": self._normalize_template_text_value(
                        self._pick_template_value(row, ["年龄/性别", "age_gender", "ageGender"]),
                        missing_policy=missing_policy,
                    ),
                    "外貌特征": self._normalize_template_text_value(
                        self._pick_template_value(row, ["外貌特征", "appearance", "look"]),
                        missing_policy=missing_policy,
                    ),
                    "性格标签": self._normalize_template_text_value(
                        self._pick_template_value(row, ["性格标签", "personality", "traits"]),
                        missing_policy=missing_policy,
                    ),
                }
            )

        shot_rows = self._coerce_dict_rows(
            self._pick_template_value(source, ["分镜详细内容", "分镜", "shots", "storyboard"])
        )
        if not shot_rows:
            shot_rows = [{}]
        normalized_shots: list[dict[str, Any]] = []
        for idx, row in enumerate(shot_rows, start=1):
            shot_id_raw = self._pick_template_value(row, ["镜号", "shot_id", "shot", "id"])
            if shot_id_raw is None:
                shot_id = str(idx)
            else:
                shot_id = self._normalize_template_text_value(shot_id_raw, missing_policy=missing_policy)
            normalized_shots.append(
                {
                    "镜号": shot_id,
                    "景别": self._normalize_template_text_value(
                        self._pick_template_value(row, ["景别", "shot_size", "framing"]),
                        missing_policy=missing_policy,
                    ),
                    "画面内容": self._normalize_template_text_value(
                        self._pick_template_value(row, ["画面内容", "content", "visual_content"]),
                        missing_policy=missing_policy,
                    ),
                    "运镜方式": self._normalize_template_text_value(
                        self._pick_template_value(row, ["运镜方式", "camera_movement", "camera_motion"]),
                        missing_policy=missing_policy,
                    ),
                    "时长": self._normalize_template_text_value(
                        self._pick_template_value(row, ["时长", "duration"]),
                        missing_policy=missing_policy,
                    ),
                    "台词/旁白": self._normalize_template_text_value(
                        self._pick_template_value(row, ["台词/旁白", "dialogue_voiceover", "dialogue", "voiceover"]),
                        missing_policy=missing_policy,
                    ),
                    "音效": self._normalize_template_text_value(
                        self._pick_template_value(row, ["音效", "sound_effects", "audio"]),
                        missing_policy=missing_policy,
                    ),
                }
            )

        video_name = self._normalize_template_text_value(
            self._pick_template_value(source, ["视频名称", "video_name", "title"]),
            missing_policy=missing_policy,
        )
        target_audience = self._normalize_template_text_value(
            self._pick_template_value(source, ["目标受众", "target_audience", "audience"]),
            missing_policy=missing_policy,
        )

        return {
            "视频类型": video_type,
            "总时长": total_duration,
            "核心主题": core_theme,
            "场景概况": scene_summary,
            "环境描述": environment_description,
            "人物设定": normalized_people,
            "分镜详细内容": normalized_shots,
            "视频名称": video_name,
            "目标受众": target_audience,
        }

    def _render_storyboard_template_text(self, structured: dict[str, Any]) -> str:
        people_rows = self._coerce_dict_rows(structured.get("人物设定"))
        shot_rows = self._coerce_dict_rows(structured.get("分镜详细内容"))
        lines: list[str] = []
        lines.append("### 专业视频分镜脚本模板")
        lines.append("")
        lines.append("##### 一、核心基础信息")
        lines.append(f"- 视频类型：{structured.get('视频类型')}")
        lines.append(f"- 总时长：{structured.get('总时长')}")
        lines.append(f"- 核心主题：{structured.get('核心主题')}")
        lines.append("")
        lines.append("##### 二、整体场景设定")
        lines.append(f"- 场景概况：{structured.get('场景概况')}")
        lines.append(f"- 环境描述：{structured.get('环境描述')}")
        lines.append("")
        lines.append("##### 三、专业人物设定")
        for row in people_rows:
            lines.append(f"- 人物ID：{row.get('人物ID')}")
            lines.append(f"- 人物名称：{row.get('人物名称')}")
            lines.append(f"- 年龄/性别：{row.get('年龄/性别')}")
            lines.append(f"- 外貌特征：{row.get('外貌特征')}")
            lines.append(f"- 性格标签：{row.get('性格标签')}")
        lines.append("")
        lines.append("##### 四、分镜详细内容")
        for row in shot_rows:
            lines.append(f"- 镜号：{row.get('镜号')}")
            lines.append(f"- 景别：{row.get('景别')}")
            lines.append(f"- 画面内容：{row.get('画面内容')}")
            lines.append(f"- 运镜方式：{row.get('运镜方式')}")
            lines.append(f"- 时长：{row.get('时长')}")
            lines.append(f"- 台词/旁白：{row.get('台词/旁白')}")
            lines.append(f"- 音效：{row.get('音效')}")
        lines.append("")
        lines.append("#### 后处理")
        lines.append(f"- 视频名称：{structured.get('视频名称')}")
        lines.append(f"- 目标受众：{structured.get('目标受众')}")
        return "\n".join(lines).strip()

    def _build_storyboard_template_messages(
        self,
        *,
        language: str,
        template_id: str,
        description_request: str,
        reference_blocks: list[dict[str, Any]],
        trace_rows: list[dict[str, Any]],
        kb_hints: list[dict[str, Any]],
        missing_policy: str,
    ) -> list[dict[str, Any]]:
        trace_lines: list[str] = []
        for row in trace_rows:
            trace_lines.append(
                f"- {row.get('media_type')} asset_id={row.get('asset_id')} "
                f"matched={row.get('matched_reference') or '-'} input={row.get('reference_input') or '-'}"
            )

        kb_lines: list[str] = []
        for item in kb_hints[:5]:
            kb_lines.append(
                f"[kb] score={round(float(item.get('distance') or 0.0), 4)} src={item.get('source_ref')}\n"
                f"{self._clip_text(str(item.get('text') or ''), 280)}"
            )

        system_text = (
            "You are a strict storyboard template renderer.\n"
            "Describe only what can be grounded from provided video references.\n"
            "Do not fabricate unseen details.\n"
            "Return JSON only. Do not output markdown.\n"
            "Top-level JSON keys (exact):\n"
            "- 视频类型\n"
            "- 总时长\n"
            "- 核心主题\n"
            "- 场景概况\n"
            "- 环境描述\n"
            "- 人物设定 (array of objects with keys: 人物ID, 人物名称, 年龄/性别, 外貌特征, 性格标签)\n"
            "- 分镜详细内容 (array of objects with keys: 镜号, 景别, 画面内容, 运镜方式, 时长, 台词/旁白, 音效)\n"
            "- 视频名称\n"
            "- 目标受众\n"
            f'If unknown, fill value with "{missing_policy}".\n'
            "Do not omit required keys.\n"
        )

        user_parts = [
            f"language={language or 'zh'}",
            f"template_id={template_id}",
            f"description_request={(description_request or '').strip() or '(none)'}",
            "reference_trace:\n" + ("\n".join(trace_lines) if trace_lines else "(none)"),
        ]
        if kb_lines:
            user_parts.append("optional_kb_hints:\n" + "\n\n".join(kb_lines))

        user_content: list[dict[str, Any]] = [{"type": "text", "text": "\n\n".join(user_parts)}]
        user_content.extend(reference_blocks)
        return [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ]

    def _build_media_description_messages(
        self,
        *,
        language: str,
        granularity: str,
        focus: list[str],
        output_format: str,
        description_request: str,
        reference_blocks: list[dict[str, Any]],
        trace_rows: list[dict[str, Any]],
        kb_hints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        focus_text = ",".join(focus) if focus else "overall"
        trace_lines = []
        for row in trace_rows:
            trace_lines.append(
                f"- {row.get('media_type')} asset_id={row.get('asset_id')} "
                f"matched={row.get('matched_reference') or '-'} input={row.get('reference_input') or '-'}"
            )

        kb_lines = []
        for item in kb_hints[:5]:
            kb_lines.append(
                f"[kb] score={round(float(item.get('distance') or 0.0), 4)} src={item.get('source_ref')}\n"
                f"{self._clip_text(str(item.get('text') or ''), 280)}"
            )

        output_rule = (
            "Return JSON only with fields: summary, people, scene, motion, camera, audio, style, lighting, props, risks."
            if output_format == "structured"
            else "Return plain text only. Do not output JSON or markdown."
        )
        system_text = (
            "You are a media describer for prompt co-creation.\n"
            "Describe only what can be grounded from provided media references.\n"
            "Do not fabricate unseen details.\n"
            f"{output_rule}\n"
        )

        user_parts = [
            f"language={language or 'zh'}",
            f"granularity={granularity}",
            f"focus={focus_text}",
            f"description_request={(description_request or '').strip() or '(none)'}",
            "reference_trace:\n" + ("\n".join(trace_lines) if trace_lines else "(none)"),
        ]
        if kb_lines:
            user_parts.append("optional_kb_hints:\n" + "\n\n".join(kb_lines))

        user_content: list[dict[str, Any]] = [{"type": "text", "text": "\n\n".join(user_parts)}]
        user_content.extend(reference_blocks)
        return [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ]

    def _build_reuse_payload_from_description(
        self,
        *,
        description_text: str,
        structured: Optional[dict[str, Any]],
        focus: list[str],
    ) -> dict[str, Any]:
        style_hint = ""
        if structured:
            style_chunks: list[str] = []
            for key in ("style", "camera", "lighting"):
                value = str(structured.get(key) or "").strip()
                if value:
                    style_chunks.append(value)
            if style_chunks:
                style_hint = "；".join(style_chunks)

        return {
            "material_references": description_text,
            "style_hint": style_hint,
            "focus": focus,
        }

    async def _list_media_assets_raw(
        self,
        *,
        expected_media_type: str,
        chat_id: str,
        status_value: str,
        __request__: Optional[Request],
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        page_size = 200
        offset = 0
        rows: list[dict[str, Any]] = []

        media_type = (expected_media_type or "").strip().lower()
        chat_id_value = (chat_id or "").strip()
        status_filter = (status_value or "").strip() or "active"

        while True:
            query: dict[str, Any] = {"limit": page_size, "offset": offset}
            if media_type:
                query["media_type"] = media_type
            if status_filter:
                query["status"] = status_filter
            if chat_id_value:
                query["chat_id"] = chat_id_value

            path = f"/api/v1/media-assets/?{urlencode(query)}"
            result = await self._internal_request(method="GET", path=path, __request__=__request__)
            if not result.get("ok"):
                return [], (
                    f"Failed to list media assets: "
                    f"{result.get('error_code') or result.get('status_code')} {result.get('error_message')}"
                )

            page_rows = [item for item in (result.get("data") or []) if isinstance(item, dict)]
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                break

            offset += page_size
            if offset >= 4000:
                break

        return rows, None

    async def _resolve_media_asset_reference_token(
        self,
        *,
        token: str,
        expected_media_type: str,
        chat_id: str,
        status_value: str,
        __request__: Optional[Request],
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        ref = str(token or "").strip()
        if not ref:
            return None, {
                "error_code": "InvalidParameter",
                "error_message": "Reference token is empty",
                "reference_input": ref,
            }

        assets_raw, list_error = await self._list_media_assets_raw(
            expected_media_type=expected_media_type,
            chat_id=chat_id,
            status_value=status_value,
            __request__=__request__,
        )
        if list_error:
            return None, {
                "error_code": "ReferenceLookupFailed",
                "error_message": list_error,
                "reference_input": ref,
            }

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

            for candidate in candidates:
                if candidate and candidate not in alias_to_canonical:
                    alias_to_canonical[candidate] = canonical

            basename = Path(canonical).name
            if basename:
                values = basename_to_canonical.setdefault(basename, [])
                if canonical not in values:
                    values.append(canonical)

        canonical = alias_to_canonical.get(ref)
        if not canonical:
            basename_hits = basename_to_canonical.get(Path(ref).name) or []
            if len(basename_hits) == 1:
                canonical = basename_hits[0]
            elif len(basename_hits) > 1:
                return None, {
                    "error_code": "AmbiguousMediaAssetReference",
                    "error_message": (
                        f"Reference %{ref} is ambiguous. Candidates: {', '.join(basename_hits)}. "
                        "Use full relative_path."
                    ),
                    "reference_input": ref,
                    "candidates": basename_hits,
                }

        if not canonical:
            available = sorted(list(dict.fromkeys(available_refs)))
            preview = ", ".join(available[:30])
            suffix = " ..." if len(available) > 30 else ""
            return None, {
                "error_code": "MissingMediaAssetReference",
                "error_message": f"Reference %{ref} not found. Available: {preview}{suffix}",
                "reference_input": ref,
                "available_references": available,
            }

        row = canonical_to_item.get(canonical) or {}
        asset_id = str(row.get("asset_id") or "").strip()
        if not asset_id:
            return None, {
                "error_code": "InvalidMediaAssetReference",
                "error_message": f"Reference %{ref} matched but asset_id is missing",
                "reference_input": ref,
                "matched_reference": canonical,
            }

        return {
            "asset_id": asset_id,
            "matched_reference": canonical,
            "reference_input": ref,
            "media_type": str(row.get("media_type") or "").strip().lower() or expected_media_type,
        }, None

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

    def _get_ark_api_key(self) -> str:
        return (os.getenv("ARK_API_KEY") or self._read_env_value_from_file("ARK_API_KEY") or "").strip()

    def _get_ark_base_url(self) -> str:
        base_url = (
            os.getenv("ARK_BASE_URL")
            or self._read_env_value_from_file("ARK_BASE_URL")
            or self.valves.ARK_BASE_URL
        )
        normalized = str(base_url).strip().rstrip("/")
        if "/api/" not in normalized:
            normalized = f"{normalized}/api/v3"
        return normalized

    def _is_supported_model(self, model: str) -> bool:
        normalized = (model or "").strip().lower().replace("_", "-")
        if not normalized:
            return False
        # Ark endpoint id is the most common deploy form in production.
        if normalized.startswith("ep-"):
            return True

        base = self.valves.DEFAULT_MODEL.strip().lower().replace("_", "-")
        if normalized == base:
            return True
        # Accept close aliases like doubao-seed-2-0-pro-xxxxx.
        return normalized.startswith("doubao-seed-2.0-pro") or normalized.startswith("doubao-seed-2-0-pro")

    def _validate_mime_type(self, value: str, *, field_name: str, expected_prefix: str) -> tuple[Optional[str], Optional[str]]:
        mime = (value or "").strip().lower()
        if not mime or "/" not in mime:
            return None, f"{field_name} must be valid MIME type, e.g. {expected_prefix}/mp4"
        if not mime.startswith(f"{expected_prefix}/"):
            return None, f"{field_name} must start with {expected_prefix}/"
        return mime, None

    def _normalize_image_detail(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = (value or "").strip().lower()
        if not raw:
            raw = str(self.valves.DEFAULT_IMAGE_DETAIL or "high").strip().lower() or "high"
        allowed = {"low", "high", "xhigh"}
        if raw not in allowed:
            return None, "image_detail must be one of: low, high, xhigh"
        return raw, None

    def _normalize_image_pixels(
        self,
        image_min_pixels: Optional[int],
        image_max_pixels: Optional[int],
    ) -> tuple[Optional[int], Optional[int], Optional[str]]:
        min_pixels = None if image_min_pixels is None else int(image_min_pixels)
        max_pixels = None if image_max_pixels is None else int(image_max_pixels)

        if min_pixels is not None and min_pixels < self.IMAGE_MIN_PIXELS_FLOOR:
            return None, None, f"image_min_pixels must be >= {self.IMAGE_MIN_PIXELS_FLOOR}"
        if max_pixels is not None and max_pixels > self.IMAGE_MAX_PIXELS_CEILING:
            return None, None, f"image_max_pixels must be <= {self.IMAGE_MAX_PIXELS_CEILING}"
        if min_pixels is not None and max_pixels is not None and min_pixels > max_pixels:
            return None, None, "image_min_pixels must be <= image_max_pixels"

        return min_pixels, max_pixels, None

    def _normalize_video_fps(self, value: Optional[float]) -> tuple[Optional[float], Optional[str]]:
        fps = self.valves.DEFAULT_VIDEO_FPS if value is None else float(value)
        if fps < self.VIDEO_FPS_MIN or fps > self.VIDEO_FPS_MAX:
            return None, f"video_fps must be in [{self.VIDEO_FPS_MIN}, {self.VIDEO_FPS_MAX}]"
        return round(float(fps), 3), None

    def _estimate_inline_base64_bytes(self, value: str, *, field_name: str) -> tuple[Optional[int], Optional[str]]:
        raw = (value or "").strip()
        if not raw:
            return None, f"{field_name} content is empty"

        if raw.startswith("data:"):
            comma = raw.find(",")
            if comma < 0:
                return None, f"{field_name} data URI is invalid: missing comma separator"
            meta = raw[:comma].lower()
            if ";base64" not in meta:
                return None, f"{field_name} data URI must use base64 encoding"
            raw = raw[comma + 1 :]

        cleaned = re.sub(r"\s+", "", raw)
        if not cleaned:
            return None, f"{field_name} content is empty"
        if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", cleaned):
            return None, f"{field_name} has invalid base64 characters"

        padding = len(cleaned) - len(cleaned.rstrip("="))
        estimated = max((len(cleaned) * 3) // 4 - padding, 0)
        return estimated, None

    def _extract_data_uri_parts(self, value: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        raw = (value or "").strip()
        if not raw.startswith("data:"):
            return None, None, "data URI must start with data:"

        comma = raw.find(",")
        if comma < 0:
            return None, None, "data URI missing comma separator"

        meta = raw[5:comma]
        data = raw[comma + 1 :]
        chunks = [chunk.strip() for chunk in meta.split(";") if chunk.strip()]
        mime = chunks[0].lower() if chunks and "/" in chunks[0] else ""
        if "base64" not in {chunk.lower() for chunk in chunks[1:] if chunk} and not meta.lower().endswith(";base64"):
            return None, None, "data URI must use base64 encoding"
        if not data:
            return None, None, "data URI payload is empty"
        return mime, data, None

    def _normalize_media_url(
        self,
        value: str,
        *,
        field_name: str,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        url = (value or "").strip()
        if not url:
            return None, None, None

        allowed_prefixes = ("http://", "https://", "data:", "file_id://", "asset://", "tos://")
        if not url.startswith(allowed_prefixes):
            return (
                None,
                None,
                f"{field_name} must start with http://, https://, data:, file_id://, asset://, or tos://",
            )

        if url.startswith("data:"):
            return url, "data_uri", None
        if url.startswith("http://") or url.startswith("https://"):
            return url, "url", None
        return url, "file_ref", None

    def _infer_audio_format_from_mime(self, mime: str) -> Optional[str]:
        normalized = (mime or "").strip().lower()
        mapping = {
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/mp4": "m4a",
            "audio/aac": "aac",
            "audio/flac": "flac",
            "audio/ogg": "ogg",
            "audio/webm": "webm",
        }
        return mapping.get(normalized)

    def _normalize_audio_format(self, value: str) -> tuple[Optional[str], Optional[str]]:
        fmt = (value or "").strip().lower()
        if not fmt:
            fmt = (self.valves.DEFAULT_AUDIO_FORMAT or "mp3").strip().lower()
        if not re.fullmatch(r"[a-z0-9]{2,16}", fmt):
            return None, "reference_audio_format must be simple token like mp3/wav/m4a"
        return fmt, None

    def _normalize_image_reference(
        self,
        *,
        reference_image_url: str,
        reference_image_base64: str,
        reference_image_mime_type: str,
        image_detail: str,
        image_min_pixels: Optional[int],
        image_max_pixels: Optional[int],
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], Optional[str]]:
        image_url = (reference_image_url or "").strip()
        image_base64 = (reference_image_base64 or "").strip()

        if image_url and image_base64:
            return None, None, "Provide either reference_image_url or reference_image_base64, not both."
        if not image_url and not image_base64:
            return None, None, None

        detail, detail_error = self._normalize_image_detail(image_detail)
        if detail_error:
            return None, None, detail_error

        min_pixels, max_pixels, pixel_error = self._normalize_image_pixels(image_min_pixels, image_max_pixels)
        if pixel_error:
            return None, None, pixel_error

        mode = "image_url"
        source = image_url

        if image_url:
            source, url_mode, url_error = self._normalize_media_url(image_url, field_name="reference_image_url")
            if url_error:
                return None, None, url_error
            if url_mode == "data_uri":
                mode = "image_data_uri"
                estimated, estimate_error = self._estimate_inline_base64_bytes(image_url, field_name="reference_image_url")
                if estimate_error:
                    return None, None, estimate_error
                if int(estimated or 0) > int(self.valves.MAX_INLINE_IMAGE_BYTES):
                    limit_mb = round(self.valves.MAX_INLINE_IMAGE_BYTES / 1024 / 1024, 1)
                    return None, None, f"Inline image payload too large; keep within {limit_mb} MB."
            elif url_mode == "file_ref":
                mode = "image_file_ref"
        else:
            mode = "image_base64"
            estimated, estimate_error = self._estimate_inline_base64_bytes(image_base64, field_name="reference_image_base64")
            if estimate_error:
                return None, None, estimate_error
            if int(estimated or 0) > int(self.valves.MAX_INLINE_IMAGE_BYTES):
                limit_mb = round(self.valves.MAX_INLINE_IMAGE_BYTES / 1024 / 1024, 1)
                return None, None, f"Inline image payload too large; keep within {limit_mb} MB."

            mime, mime_error = self._validate_mime_type(
                reference_image_mime_type or "image/png",
                field_name="reference_image_mime_type",
                expected_prefix="image",
            )
            if mime_error:
                return None, None, mime_error

            if image_base64.startswith("data:"):
                source = image_base64
            else:
                source = f"data:{mime};base64,{image_base64}"

        image_obj: dict[str, Any] = {"url": source, "detail": detail}
        if min_pixels is not None or max_pixels is not None:
            pixel_limit: dict[str, Any] = {}
            if min_pixels is not None:
                pixel_limit["min_pixels"] = min_pixels
            if max_pixels is not None:
                pixel_limit["max_pixels"] = max_pixels
            image_obj["image_pixel_limit"] = pixel_limit

        block = {"type": "image_url", "image_url": image_obj}
        meta = {
            "mode": mode,
            "detail": detail,
            "min_pixels": min_pixels,
            "max_pixels": max_pixels,
        }
        return block, meta, None

    def _normalize_video_reference(
        self,
        *,
        reference_video_url: str,
        reference_video_base64: str,
        reference_video_mime_type: str,
        video_fps: Optional[float],
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], Optional[str]]:
        video_url = (reference_video_url or "").strip()
        video_base64 = (reference_video_base64 or "").strip()

        if video_url and video_base64:
            return None, None, "Provide either reference_video_url or reference_video_base64, not both."
        if not video_url and not video_base64:
            return None, None, None

        fps, fps_error = self._normalize_video_fps(video_fps)
        if fps_error:
            return None, None, fps_error

        mode = "video_url"
        source = video_url

        if video_url:
            source, url_mode, url_error = self._normalize_media_url(video_url, field_name="reference_video_url")
            if url_error:
                return None, None, url_error
            if url_mode == "data_uri":
                mode = "video_data_uri"
                estimated, estimate_error = self._estimate_inline_base64_bytes(video_url, field_name="reference_video_url")
                if estimate_error:
                    return None, None, estimate_error
                if int(estimated or 0) > int(self.valves.MAX_INLINE_VIDEO_BYTES):
                    limit_mb = round(self.valves.MAX_INLINE_VIDEO_BYTES / 1024 / 1024, 1)
                    return None, None, f"Inline video payload too large; keep within {limit_mb} MB."
            elif url_mode == "file_ref":
                mode = "video_file_ref"
        else:
            mode = "video_base64"
            estimated, estimate_error = self._estimate_inline_base64_bytes(video_base64, field_name="reference_video_base64")
            if estimate_error:
                return None, None, estimate_error
            if int(estimated or 0) > int(self.valves.MAX_INLINE_VIDEO_BYTES):
                limit_mb = round(self.valves.MAX_INLINE_VIDEO_BYTES / 1024 / 1024, 1)
                return None, None, f"Inline video payload too large; keep within {limit_mb} MB."

            mime, mime_error = self._validate_mime_type(
                reference_video_mime_type or "video/mp4",
                field_name="reference_video_mime_type",
                expected_prefix="video",
            )
            if mime_error:
                return None, None, mime_error

            if video_base64.startswith("data:"):
                source = video_base64
            else:
                source = f"data:{mime};base64,{video_base64}"

        block = {
            "type": "video_url",
            "video_url": {
                "url": source,
                "fps": fps,
            },
        }
        meta = {
            "mode": mode,
            "fps": fps,
        }
        return block, meta, None

    def _normalize_audio_reference(
        self,
        *,
        reference_audio_url: str,
        reference_audio_base64: str,
        reference_audio_format: str,
        reference_audio_mime_type: str,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], Optional[str]]:
        audio_url = (reference_audio_url or "").strip()
        audio_base64 = (reference_audio_base64 or "").strip()

        if audio_url and audio_base64:
            return None, None, "Provide either reference_audio_url or reference_audio_base64, not both."
        if not audio_url and not audio_base64:
            return None, None, None

        if audio_url:
            normalized_url, url_mode, url_error = self._normalize_media_url(audio_url, field_name="reference_audio_url")
            if url_error:
                return None, None, url_error

            if url_mode == "data_uri":
                estimated, estimate_error = self._estimate_inline_base64_bytes(audio_url, field_name="reference_audio_url")
                if estimate_error:
                    return None, None, estimate_error
                if int(estimated or 0) > int(self.valves.MAX_INLINE_AUDIO_BYTES):
                    limit_mb = round(self.valves.MAX_INLINE_AUDIO_BYTES / 1024 / 1024, 1)
                    return None, None, f"Inline audio payload too large; keep within {limit_mb} MB."

                mime, data, parse_error = self._extract_data_uri_parts(audio_url)
                if parse_error:
                    return None, None, parse_error

                inferred = self._infer_audio_format_from_mime(mime or "")
                fmt, fmt_error = self._normalize_audio_format(reference_audio_format or inferred or "")
                if fmt_error:
                    return None, None, fmt_error

                block = {
                    "type": "input_audio",
                    "input_audio": {
                        "data": data,
                        "format": fmt,
                    },
                }
                meta = {
                    "mode": "audio_data_uri",
                    "format": fmt,
                }
                return block, meta, None

            block = {
                "type": "input_audio",
                "input_audio": {
                    "url": normalized_url,
                },
            }
            meta = {
                "mode": "audio_file_ref" if url_mode == "file_ref" else "audio_url",
                "format": None,
            }
            return block, meta, None

        estimated, estimate_error = self._estimate_inline_base64_bytes(audio_base64, field_name="reference_audio_base64")
        if estimate_error:
            return None, None, estimate_error
        if int(estimated or 0) > int(self.valves.MAX_INLINE_AUDIO_BYTES):
            limit_mb = round(self.valves.MAX_INLINE_AUDIO_BYTES / 1024 / 1024, 1)
            return None, None, f"Inline audio payload too large; keep within {limit_mb} MB."

        if audio_base64.startswith("data:"):
            mime, data, parse_error = self._extract_data_uri_parts(audio_base64)
            if parse_error:
                return None, None, parse_error
            inferred = self._infer_audio_format_from_mime(mime or "")
            fmt, fmt_error = self._normalize_audio_format(reference_audio_format or inferred or "")
            if fmt_error:
                return None, None, fmt_error
            block = {
                "type": "input_audio",
                "input_audio": {
                    "data": data,
                    "format": fmt,
                },
            }
            meta = {
                "mode": "audio_base64",
                "format": fmt,
            }
            return block, meta, None

        fmt_source = reference_audio_format
        if not fmt_source and reference_audio_mime_type:
            mime, mime_error = self._validate_mime_type(
                reference_audio_mime_type,
                field_name="reference_audio_mime_type",
                expected_prefix="audio",
            )
            if mime_error:
                return None, None, mime_error
            fmt_source = self._infer_audio_format_from_mime(mime or "") or ""

        fmt, fmt_error = self._normalize_audio_format(fmt_source)
        if fmt_error:
            return None, None, fmt_error

        block = {
            "type": "input_audio",
            "input_audio": {
                "data": audio_base64,
                "format": fmt,
            },
        }
        meta = {
            "mode": "audio_base64",
            "format": fmt,
        }
        return block, meta, None

    def _extract_message_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
        return ""

    def _build_seed_prompt_messages(
        self,
        *,
        user_requirement: str,
        grid_requirements: str,
        current_draft: str,
        revision_feedback: str,
        language: str,
        style_hint: str,
        keep_length: str,
        reference_blocks: list[dict[str, Any]],
        input_mode: str,
        modalities: list[str],
        image_meta: Optional[dict[str, Any]],
        video_meta: Optional[dict[str, Any]],
        audio_meta: Optional[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        system_text = (
            "You are a video-prompt co-creator. "
            "Your only task is to produce a final prompt text for video generation.\n"
            "Rules:\n"
            "1) Do not force any fixed schema or template unless user explicitly requests one.\n"
            "2) If grid_requirements are provided, follow them strictly.\n"
            "3) Keep strong visual direction: subject, scene, camera language, lighting, motion, style, and atmosphere.\n"
            "4) If revising an existing draft, preserve valid parts and only adjust what feedback asks.\n"
            "5) If image/video/audio references are provided, use them as factual anchors and avoid inventing unseen details.\n"
            "6) Output only the final prompt body text, without explanation, markdown headings, JSON, or code block.\n"
        )

        user_parts = [
            f"language={language or 'zh'}",
            f"input_mode={input_mode}",
            f"reference_modalities={','.join(modalities) if modalities else 'none'}",
            f"user_requirement:\n{(user_requirement or '').strip()}",
        ]
        if (grid_requirements or "").strip():
            user_parts.append(f"grid_requirements:\n{grid_requirements.strip()}")
        if (current_draft or "").strip():
            user_parts.append(f"current_draft:\n{current_draft.strip()}")
        if (revision_feedback or "").strip():
            user_parts.append(f"revision_feedback:\n{revision_feedback.strip()}")
        if (style_hint or "").strip():
            user_parts.append(f"style_hint:\n{style_hint.strip()}")
        if (keep_length or "").strip():
            user_parts.append(f"keep_length:\n{keep_length.strip()}")

        if image_meta is not None:
            user_parts.append(
                "image_reference: "
                f"mode={image_meta.get('mode')}, detail={image_meta.get('detail')}, "
                f"min_pixels={image_meta.get('min_pixels')}, max_pixels={image_meta.get('max_pixels')}"
            )
        if video_meta is not None:
            user_parts.append(
                "video_reference: "
                f"mode={video_meta.get('mode')}, fps={video_meta.get('fps')}"
            )
        if audio_meta is not None:
            user_parts.append(
                "audio_reference: "
                f"mode={audio_meta.get('mode')}, format={audio_meta.get('format')}"
            )

        user_content: list[dict[str, Any]] = [{"type": "text", "text": "\n\n".join(user_parts)}]
        user_content.extend(reference_blocks)

        return [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ]

    async def _ark_chat_completions(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        api_key: str,
        temperature: float,
        top_p: float,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        api_key = str(api_key or "").strip()
        if not api_key:
            return {
                "ok": False,
                "status_code": 400,
                "error": "Resolved ARK api_key is empty",
                "error_code": "KEY_ROUTING_ENV_MISSING",
                "error_message": "Resolved ARK api_key is empty",
                "request_id": None,
            }

        base_url = self._get_ark_base_url()
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": float(temperature),
            "top_p": float(top_p),
            "max_tokens": int(max_output_tokens),
        }

        async with httpx.AsyncClient(timeout=self.valves.REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code >= 400:
            return self._normalize_error(response)

        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": response.text}
        return {"ok": True, "status_code": response.status_code, "data": payload}

    async def co_create_video_prompt_with_seed_pro(
        self,
        user_requirement: str,
        grid_requirements: str = "",
        current_draft: str = "",
        revision_feedback: str = "",
        style_hint: str = "",
        keep_length: str = "",
        reference_chat_id: str = "",
        reference_status: str = "active",
        reference_image_asset_id: str = "",
        reference_image_url: str = "",
        reference_image_base64: str = "",
        reference_image_mime_type: str = "image/png",
        image_detail: str = "",
        image_min_pixels: Optional[int] = None,
        image_max_pixels: Optional[int] = None,
        reference_video_asset_id: str = "",
        reference_video_url: str = "",
        reference_video_base64: str = "",
        reference_video_mime_type: str = "video/mp4",
        video_fps: Optional[float] = None,
        reference_audio_asset_id: str = "",
        reference_audio_url: str = "",
        reference_audio_base64: str = "",
        reference_audio_format: str = "",
        reference_audio_mime_type: str = "audio/mpeg",
        credential_alias: str = "",
        language: str = "zh",
        model: str = "",
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        Co-create a video-generation prompt with doubao-seed-2.0-pro.

        This tool is prompt-only:
        - no generation task submit
        - no task polling
        - no forced fixed prompt format
        - supports optional multimodal references via Chat API content[]:
          image_url / video_url / input_audio (any combination)
        - by default references are resolved from media-assets TOS URLs using asset_id
          or %reference_name (matched by relative_path/display_name/original_filename)
        """
        requirement = (user_requirement or "").strip()
        draft = (current_draft or "").strip()
        feedback = (revision_feedback or "").strip()
        if not requirement and not (draft and feedback):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "user_requirement is required (or provide current_draft + revision_feedback).",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        image_asset_id = (reference_image_asset_id or "").strip()
        video_asset_id = (reference_video_asset_id or "").strip()
        audio_asset_id = (reference_audio_asset_id or "").strip()

        raw_image_url = (reference_image_url or "").strip()
        raw_image_base64 = (reference_image_base64 or "").strip()
        raw_video_url = (reference_video_url or "").strip()
        raw_video_base64 = (reference_video_base64 or "").strip()
        raw_audio_url = (reference_audio_url or "").strip()
        raw_audio_base64 = (reference_audio_base64 or "").strip()
        reference_chat_id_value = (reference_chat_id or "").strip()
        reference_status_value = (reference_status or "").strip() or "active"

        image_ref_token = self._normalize_reference_token(raw_image_url)
        video_ref_token = self._normalize_reference_token(raw_video_url)
        audio_ref_token = self._normalize_reference_token(raw_audio_url)

        image_ref_meta: Optional[dict[str, Any]] = None
        video_ref_meta: Optional[dict[str, Any]] = None
        audio_ref_meta: Optional[dict[str, Any]] = None

        if self.valves.FORCE_MEDIA_ASSET_TOS:
            if image_asset_id and (raw_image_url or raw_image_base64):
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": "FORCE_MEDIA_ASSET_TOS=true: use reference_image_asset_id only.",
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            if video_asset_id and (raw_video_url or raw_video_base64):
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": "FORCE_MEDIA_ASSET_TOS=true: use reference_video_asset_id only.",
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            if audio_asset_id and (raw_audio_url or raw_audio_base64):
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": "FORCE_MEDIA_ASSET_TOS=true: use reference_audio_asset_id only.",
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            if not image_asset_id and raw_image_base64:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": (
                            "FORCE_MEDIA_ASSET_TOS=true: image reference must use "
                            "reference_image_asset_id or reference_image_url as %reference_name."
                        ),
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            if not video_asset_id and raw_video_base64:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": (
                            "FORCE_MEDIA_ASSET_TOS=true: video reference must use "
                            "reference_video_asset_id or reference_video_url as %reference_name."
                        ),
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            if not audio_asset_id and raw_audio_base64:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": (
                            "FORCE_MEDIA_ASSET_TOS=true: audio reference must use "
                            "reference_audio_asset_id or reference_audio_url as %reference_name."
                        ),
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )

            if not image_asset_id and image_ref_token:
                image_ref_meta, image_ref_error = await self._resolve_media_asset_reference_token(
                    token=image_ref_token,
                    expected_media_type="image",
                    chat_id=reference_chat_id_value,
                    status_value=reference_status_value,
                    __request__=__request__,
                )
                if image_ref_error:
                    image_ref_error_payload = dict(image_ref_error)
                    return json.dumps(
                        {
                            "ok": False,
                            "status_code": 400,
                            "error_code": image_ref_error_payload.pop("error_code", "InvalidParameter"),
                            "error_message": image_ref_error_payload.pop("error_message", "Reference resolve failed"),
                            "request_id": None,
                            **image_ref_error_payload,
                        },
                        ensure_ascii=False,
                    )
                image_asset_id = str((image_ref_meta or {}).get("asset_id") or "").strip()
                reference_image_url = ""

            if not video_asset_id and video_ref_token:
                video_ref_meta, video_ref_error = await self._resolve_media_asset_reference_token(
                    token=video_ref_token,
                    expected_media_type="video",
                    chat_id=reference_chat_id_value,
                    status_value=reference_status_value,
                    __request__=__request__,
                )
                if video_ref_error:
                    video_ref_error_payload = dict(video_ref_error)
                    return json.dumps(
                        {
                            "ok": False,
                            "status_code": 400,
                            "error_code": video_ref_error_payload.pop("error_code", "InvalidParameter"),
                            "error_message": video_ref_error_payload.pop("error_message", "Reference resolve failed"),
                            "request_id": None,
                            **video_ref_error_payload,
                        },
                        ensure_ascii=False,
                    )
                video_asset_id = str((video_ref_meta or {}).get("asset_id") or "").strip()
                reference_video_url = ""

            if not audio_asset_id and audio_ref_token:
                audio_ref_meta, audio_ref_error = await self._resolve_media_asset_reference_token(
                    token=audio_ref_token,
                    expected_media_type="audio",
                    chat_id=reference_chat_id_value,
                    status_value=reference_status_value,
                    __request__=__request__,
                )
                if audio_ref_error:
                    audio_ref_error_payload = dict(audio_ref_error)
                    return json.dumps(
                        {
                            "ok": False,
                            "status_code": 400,
                            "error_code": audio_ref_error_payload.pop("error_code", "InvalidParameter"),
                            "error_message": audio_ref_error_payload.pop("error_message", "Reference resolve failed"),
                            "request_id": None,
                            **audio_ref_error_payload,
                        },
                        ensure_ascii=False,
                    )
                audio_asset_id = str((audio_ref_meta or {}).get("asset_id") or "").strip()
                reference_audio_url = ""

            if not image_asset_id and raw_image_url:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": (
                            "FORCE_MEDIA_ASSET_TOS=true: image reference must use "
                            "reference_image_asset_id or %reference_name."
                        ),
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            if not video_asset_id and raw_video_url:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": (
                            "FORCE_MEDIA_ASSET_TOS=true: video reference must use "
                            "reference_video_asset_id or %reference_name."
                        ),
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            if not audio_asset_id and raw_audio_url:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": (
                            "FORCE_MEDIA_ASSET_TOS=true: audio reference must use "
                            "reference_audio_asset_id or %reference_name."
                        ),
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )

        image_asset: Optional[dict[str, Any]] = None
        if image_asset_id:
            image_asset, image_asset_error = await self._resolve_media_asset_to_url(
                asset_id=image_asset_id,
                expected_media_type="image",
                __request__=__request__,
            )
            if image_asset_error:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": image_asset_error,
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            reference_image_url = str(image_asset.get("url") or "")
            reference_image_base64 = ""

        video_asset: Optional[dict[str, Any]] = None
        if video_asset_id:
            video_asset, video_asset_error = await self._resolve_media_asset_to_url(
                asset_id=video_asset_id,
                expected_media_type="video",
                __request__=__request__,
            )
            if video_asset_error:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": video_asset_error,
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            reference_video_url = str(video_asset.get("url") or "")
            reference_video_base64 = ""

        audio_asset: Optional[dict[str, Any]] = None
        if audio_asset_id:
            audio_asset, audio_asset_error = await self._resolve_media_asset_to_url(
                asset_id=audio_asset_id,
                expected_media_type="audio",
                __request__=__request__,
            )
            if audio_asset_error:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 400,
                        "error_code": "InvalidParameter",
                        "error_message": audio_asset_error,
                        "request_id": None,
                    },
                    ensure_ascii=False,
                )
            reference_audio_url = str(audio_asset.get("url") or "")
            reference_audio_base64 = ""

        image_block, image_meta, image_error = self._normalize_image_reference(
            reference_image_url=reference_image_url,
            reference_image_base64=reference_image_base64,
            reference_image_mime_type=reference_image_mime_type,
            image_detail=image_detail,
            image_min_pixels=image_min_pixels,
            image_max_pixels=image_max_pixels,
        )
        if image_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": image_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        if image_meta is not None and image_asset is not None:
            image_meta["mode"] = "image_asset_id"
            image_meta["asset_id"] = image_asset.get("asset_id")
            image_meta["expires_in"] = image_asset.get("expires_in")
            if image_ref_meta is not None:
                image_meta["reference_input"] = image_ref_meta.get("reference_input")
                image_meta["matched_reference"] = image_ref_meta.get("matched_reference")

        video_block, video_meta, video_error = self._normalize_video_reference(
            reference_video_url=reference_video_url,
            reference_video_base64=reference_video_base64,
            reference_video_mime_type=reference_video_mime_type,
            video_fps=video_fps,
        )
        if video_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": video_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        if video_meta is not None and video_asset is not None:
            video_meta["mode"] = "video_asset_id"
            video_meta["asset_id"] = video_asset.get("asset_id")
            video_meta["expires_in"] = video_asset.get("expires_in")
            if video_ref_meta is not None:
                video_meta["reference_input"] = video_ref_meta.get("reference_input")
                video_meta["matched_reference"] = video_ref_meta.get("matched_reference")

        audio_block, audio_meta, audio_error = self._normalize_audio_reference(
            reference_audio_url=reference_audio_url,
            reference_audio_base64=reference_audio_base64,
            reference_audio_format=reference_audio_format,
            reference_audio_mime_type=reference_audio_mime_type,
        )
        if audio_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": audio_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        if audio_meta is not None and audio_asset is not None:
            audio_meta["mode"] = "audio_asset_id"
            audio_meta["asset_id"] = audio_asset.get("asset_id")
            audio_meta["expires_in"] = audio_asset.get("expires_in")
            if audio_ref_meta is not None:
                audio_meta["reference_input"] = audio_ref_meta.get("reference_input")
                audio_meta["matched_reference"] = audio_ref_meta.get("matched_reference")

        model_id = (model or "").strip() or self.valves.DEFAULT_MODEL
        if not self._is_supported_model(model_id):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": (
                        "model must be doubao-seed-2.0-pro family or Ark endpoint id (ep-xxxx). "
                        f"Current: {model_id}"
                    ),
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        reference_blocks: list[dict[str, Any]] = []
        modalities: list[str] = []
        if image_block is not None:
            reference_blocks.append(image_block)
            modalities.append("image")
        if video_block is not None:
            reference_blocks.append(video_block)
            modalities.append("video")
        if audio_block is not None:
            reference_blocks.append(audio_block)
            modalities.append("audio")

        input_mode = "text_only" if not reference_blocks else "multimodal"

        credential = await self._resolve_ark_credential(
            __user__=__user__,
            preferred_alias=credential_alias,
        )
        if not credential.get("ok"):
            return json.dumps(credential, ensure_ascii=False)
        api_key = str(credential.get("api_key") or "").strip()

        messages = self._build_seed_prompt_messages(
            user_requirement=requirement,
            grid_requirements=(grid_requirements or "").strip(),
            current_draft=draft,
            revision_feedback=feedback,
            style_hint=(style_hint or "").strip(),
            keep_length=(keep_length or "").strip(),
            language=(language or "zh").strip(),
            reference_blocks=reference_blocks,
            input_mode=input_mode,
            modalities=modalities,
            image_meta=image_meta,
            video_meta=video_meta,
            audio_meta=audio_meta,
        )
        result = await self._ark_chat_completions(
            messages=messages,
            model=model_id,
            api_key=api_key,
            temperature=temperature if temperature is not None else self.valves.DEFAULT_TEMPERATURE,
            top_p=top_p if top_p is not None else self.valves.DEFAULT_TOP_P,
            max_output_tokens=max_output_tokens if max_output_tokens is not None else self.valves.DEFAULT_MAX_OUTPUT_TOKENS,
        )
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        prompt = self._extract_message_text(data)
        if not prompt:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 502,
                    "error_code": "InvalidProviderResponse",
                    "error_message": "Provider response does not include prompt text.",
                    "request_id": data.get("request_id"),
                    "raw_response": data,
                },
                ensure_ascii=False,
            )

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        resolved_assets = [item for item in [image_asset, video_asset, audio_asset] if item is not None]
        payload = {
            "ok": True,
            "status_code": result.get("status_code"),
            "model": model_id,
            "prompt": prompt,
            "input_mode": input_mode,
            "reference_modalities": modalities,
            "image_meta": image_meta,
            "video_meta": video_meta,
            "audio_meta": audio_meta,
            "resolved_assets": resolved_assets,
            "force_media_asset_tos": bool(self.valves.FORCE_MEDIA_ASSET_TOS),
            "credential_provider": credential.get("provider"),
            "credential_alias": credential.get("credential_alias"),
            "routing_group_id": credential.get("routing_group_id"),
            "credential_source": credential.get("source"),
            "request_id": data.get("request_id"),
            "usage": usage,
            "raw_response": data,
        }
        payload["reference_resolution"] = {
            "chat_id": reference_chat_id_value or None,
            "status": reference_status_value or None,
            "image": image_ref_meta,
            "video": video_ref_meta,
            "audio": audio_ref_meta,
        }
        return json.dumps(payload, ensure_ascii=False)

    async def optimize_video_prompt_with_kb_for_seedance2(
        self,
        raw_prompt: str,
        material_references: str = "",
        shot_script: str = "",
        language: str = "zh",
        style_hint: str = "",
        keep_length: str = "",
        kb_query: str = "",
        min_evidence: Optional[int] = None,
        credential_alias: str = "",
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        Optimize a video prompt with mandatory KB evidence.

        Constraints:
        - KB scope is resolved from OPTIMIZER_KB_NAMES (default: KB-01 + KB-02).
        - Evidence count must meet OPTIMIZER_KB_MIN_EVIDENCE (default: 2).
        - Fixed model label for this mode is OPTIMIZER_FIXED_MODEL.
        """
        if not bool(self.valves.OPTIMIZER_ENABLED):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "OPTIMIZER_DISABLED",
                    "error_message": "Optimizer mode is disabled by valve OPTIMIZER_ENABLED.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        prompt_text = str(raw_prompt or "").strip()
        if not prompt_text:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "raw_prompt is required.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        resolved_kbs, missing_kbs, kb_error = await self._resolve_optimizer_kb_collections(__request__=__request__)
        if kb_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "KB_SCOPE_INVALID",
                    "error_message": kb_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        if missing_kbs:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "KB_SCOPE_INVALID",
                    "error_message": f"Required knowledge bases not found: {', '.join(missing_kbs)}",
                    "missing_kbs": missing_kbs,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        kb_scope_names = [str(item.get("name") or "").strip() for item in resolved_kbs if item.get("name")]
        kb_collection_ids = [str(item.get("id") or "").strip() for item in resolved_kbs if item.get("id")]

        query_text = self._build_optimizer_query_text(
            raw_prompt=prompt_text,
            material_references=material_references,
            shot_script=shot_script,
            style_hint=style_hint,
            kb_query=kb_query,
        )
        if not query_text:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "Query text is empty. Provide raw_prompt or kb_query.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        raw_evidence_rows, retrieval_error = await self._query_optimizer_kb_evidence(
            collection_names=kb_collection_ids,
            query_text=query_text,
            __request__=__request__,
        )
        if retrieval_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "KB_RETRIEVAL_FAILED",
                    "error_message": retrieval_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        kept_evidence = self._prepare_optimizer_evidence(raw_evidence_rows)
        required_evidence = int(self.valves.OPTIMIZER_KB_MIN_EVIDENCE)
        if min_evidence is not None:
            required_evidence = max(required_evidence, int(min_evidence))

        if len(kept_evidence) < required_evidence:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "KB_EVIDENCE_INSUFFICIENT",
                    "error_message": (
                        f"Knowledge evidence is insufficient: got {len(kept_evidence)}, "
                        f"require >= {required_evidence}. Please enrich KB-01/KB-02 or provide more specific context."
                    ),
                    "kb_trace": {
                        "kb_scope": kb_scope_names,
                        "kb_collections": kb_collection_ids,
                        "evidence_count": len(kept_evidence),
                        "required_evidence": required_evidence,
                        "source_refs": [item.get("source_ref") for item in kept_evidence],
                        "kb_version": str(self.valves.OPTIMIZER_KB_VERSION or "kb-unset"),
                    },
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        credential = await self._resolve_ark_credential(
            __user__=__user__,
            preferred_alias=credential_alias,
        )
        if not credential.get("ok"):
            return json.dumps(credential, ensure_ascii=False)

        fixed_model = str(self.valves.OPTIMIZER_FIXED_MODEL or "").strip()
        if not fixed_model:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "OPTIMIZER_FIXED_MODEL is empty.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        messages = self._build_optimizer_messages(
            fixed_model=fixed_model,
            language=(language or "zh").strip(),
            raw_prompt=prompt_text,
            material_references=(material_references or "").strip(),
            shot_script=(shot_script or "").strip(),
            style_hint=(style_hint or "").strip(),
            keep_length=(keep_length or "").strip(),
            evidence=kept_evidence[: int(self.valves.OPTIMIZER_KB_QUERY_TOP_K)],
            kb_scope_names=kb_scope_names,
        )
        result = await self._ark_chat_completions(
            messages=messages,
            model=fixed_model,
            api_key=str(credential.get("api_key") or "").strip(),
            temperature=temperature if temperature is not None else self.valves.DEFAULT_TEMPERATURE,
            top_p=top_p if top_p is not None else self.valves.DEFAULT_TOP_P,
            max_output_tokens=max_output_tokens if max_output_tokens is not None else self.valves.DEFAULT_MAX_OUTPUT_TOKENS,
        )
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        provider_payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        provider_text = self._extract_message_text(provider_payload)
        parsed = self._extract_json_object(provider_text)
        if parsed is None:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 502,
                    "error_code": "InvalidProviderResponse",
                    "error_message": "Provider response is not valid JSON for optimizer mode.",
                    "request_id": provider_payload.get("request_id"),
                    "provider_text": provider_text,
                },
                ensure_ascii=False,
            )

        optimized_prompt = str(parsed.get("optimized_prompt") or "").strip()
        if not optimized_prompt:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 502,
                    "error_code": "InvalidProviderResponse",
                    "error_message": "optimizer response missing optimized_prompt.",
                    "request_id": provider_payload.get("request_id"),
                    "provider_payload": parsed,
                },
                ensure_ascii=False,
            )

        negative_prompt = str(parsed.get("negative_prompt") or "").strip()
        reasoning = self._coerce_text_list(parsed.get("reasoning"))
        risk_checks = self._coerce_text_list(parsed.get("risk_checks"))
        readable_summary = str(parsed.get("readable_summary") or "").strip()
        if not readable_summary:
            readable_summary = self._clip_text(
                "；".join(reasoning[:2] + risk_checks[:2]) or "优化完成，请查看 optimized_prompt 与风险检查。",
                240,
            )

        kb_trace = {
            "kb_scope": kb_scope_names,
            "kb_collections": kb_collection_ids,
            "evidence_count": len(kept_evidence),
            "required_evidence": required_evidence,
            "source_refs": [item.get("source_ref") for item in kept_evidence],
            "kb_version": str(self.valves.OPTIMIZER_KB_VERSION or "kb-unset"),
        }

        output = {
            "ok": True,
            "status_code": result.get("status_code"),
            "mode": "optimizer",
            "model": fixed_model,
            "optimized_prompt": optimized_prompt,
            "negative_prompt": negative_prompt,
            "reasoning": reasoning,
            "risk_checks": risk_checks,
            "readable_summary": readable_summary,
            "kb_trace": kb_trace,
            "credential_provider": credential.get("provider"),
            "credential_alias": credential.get("credential_alias"),
            "routing_group_id": credential.get("routing_group_id"),
            "credential_source": credential.get("source"),
            "request_id": provider_payload.get("request_id"),
            "usage": provider_payload.get("usage"),
            "raw_response": provider_payload,
        }
        return json.dumps(output, ensure_ascii=False)

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
        List media assets under current user scope.
        """
        page_limit = max(1, min(int(limit or 100), 200))
        page_offset = max(0, int(offset or 0))
        query: dict[str, Any] = {"limit": page_limit, "offset": page_offset}
        if (media_type or "").strip():
            query["media_type"] = media_type.strip()
        if (status or "").strip():
            query["status"] = status.strip()
        if (chat_id or "").strip():
            query["chat_id"] = chat_id.strip()

        result = await self._internal_request(
            method="GET",
            path=f"/api/v1/media-assets/?{urlencode(query)}",
            __request__=__request__,
        )
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        rows = []
        for item in (result.get("data") or []):
            if isinstance(item, dict):
                rows.append(self._compact_media_asset_item(item))
        return json.dumps(
            {
                "ok": True,
                "assets": rows,
                "count": len(rows),
                "query": query,
            },
            ensure_ascii=False,
        )

    async def get_media_asset(
        self,
        asset_id: str,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        Get one media asset detail.
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
        result = await self._internal_request(
            method="GET",
            path=f"/api/v1/media-assets/{quote(aid, safe='')}",
            __request__=__request__,
        )
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)
        row = result.get("data") if isinstance(result.get("data"), dict) else {}
        return json.dumps({"ok": True, "asset": self._compact_media_asset_item(row)}, ensure_ascii=False)

    async def get_media_asset_url(
        self,
        asset_id: str,
        expires_in: int = 3600,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        Get pre-signed URL for media asset.
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
        result = await self._internal_request(
            method="GET",
            path=f"/api/v1/media-assets/{quote(aid, safe='')}/url?{urlencode({'expires_in': ttl})}",
            __request__=__request__,
        )
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
        Resolve %reference_name tokens from prompt.
        """
        prompt_text = str(prompt or "").strip()
        refs = self._extract_media_asset_references(prompt_text)
        assets_raw, list_error = await self._list_media_assets_raw(
            expected_media_type="",
            chat_id=(chat_id or "").strip(),
            status_value=(status or "").strip() or "active",
            __request__=__request__,
        )
        if list_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "ReferenceLookupFailed",
                    "error_message": list_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )

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
        resolved: list[dict[str, Any]] = []
        for ref in refs:
            canonical = alias_to_canonical.get(ref)
            if not canonical:
                basename_hits = basename_to_canonical.get(Path(ref).name) or []
                if len(basename_hits) == 1:
                    canonical = basename_hits[0]
                elif len(basename_hits) > 1:
                    missing.append(ref)
                    ambiguous.append({"reference": ref, "candidates": basename_hits})
                    continue

            if not canonical:
                missing.append(ref)
                continue
            item = canonical_to_item.get(canonical) or {}
            resolved.append(
                {
                    "reference": ref,
                    "matched_reference": canonical,
                    "asset": self._compact_media_asset_item(item),
                }
            )

        return json.dumps(
            {
                "ok": True,
                "references": refs,
                "resolved": resolved,
                "missing_references": missing,
                "ambiguous_references": ambiguous,
                "available_references": sorted(list(dict.fromkeys(available_refs))),
            },
            ensure_ascii=False,
        )

    async def describe_media_assets_for_prompt(
        self,
        description_request: str = "",
        granularity: str = "",
        focus: str = "",
        output_format: str = "",
        template_id: str = "",
        enforce_template_output: bool = False,
        reference_chat_id: str = "",
        reference_status: str = "active",
        reference_image_asset_id: str = "",
        reference_image_url: str = "",
        reference_video_asset_id: str = "",
        reference_video_url: str = "",
        reference_audio_asset_id: str = "",
        reference_audio_url: str = "",
        language: str = "zh",
        model: str = "",
        enable_kb_enhancement: Optional[bool] = None,
        kb_query: str = "",
        credential_alias: str = "",
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """
        Describe user-provided media assets as reusable prompt context.

        Supports:
        - granularity: brief / detailed
        - focus: people / scene / motion / camera / style / audio / lighting / props / custom
        - output_format: text (default) / structured
        - template_id: optional template id (e.g. storyboard_list_v1)
        - enforce_template_output: when true, force template rendering
        """
        resolved_blocks: list[dict[str, Any]] = []
        trace_rows: list[dict[str, Any]] = []
        resolved_assets: list[dict[str, Any]] = []

        chat_scope = (reference_chat_id or "").strip()
        status_scope = (reference_status or "").strip() or "active"

        async def _resolve_one(
            media_type: str,
            asset_id: str,
            ref_url: str,
        ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], Optional[str]]:
            aid, raw_url, ref_token = self._normalize_media_reference_inputs(
                asset_id=asset_id,
                ref_url=ref_url,
            )
            ref_meta: Optional[dict[str, Any]] = None

            if self.valves.FORCE_MEDIA_ASSET_TOS:
                if aid and raw_url:
                    return None, None, (
                        f"FORCE_MEDIA_ASSET_TOS=true: use reference_{media_type}_asset_id only "
                        f"or use reference_{media_type}_url as %reference_name."
                    )
                if not aid and raw_url and not ref_token:
                    return None, None, (
                        f"FORCE_MEDIA_ASSET_TOS=true: reference_{media_type}_url must be %reference_name."
                    )

            if not aid and ref_token:
                ref_meta, ref_error = await self._resolve_media_asset_reference_token(
                    token=ref_token,
                    expected_media_type=media_type,
                    chat_id=chat_scope,
                    status_value=status_scope,
                    __request__=__request__,
                )
                if ref_error:
                    return None, None, str(ref_error.get("error_message") or "Reference resolve failed")
                aid = str((ref_meta or {}).get("asset_id") or "").strip()

            if aid:
                asset, asset_error = await self._resolve_media_asset_to_url(
                    asset_id=aid,
                    expected_media_type=media_type,
                    __request__=__request__,
                )
                if asset_error:
                    return None, None, asset_error
                return asset, ref_meta, None

            if raw_url and not self.valves.FORCE_MEDIA_ASSET_TOS:
                normalized = self._normalize_http_url(raw_url)
                if not normalized:
                    return None, None, f"Invalid direct url for {media_type}: {raw_url}"
                return {
                    "asset_id": None,
                    "media_type": media_type,
                    "url": normalized,
                    "expires_in": None,
                    "direct_url": True,
                }, ref_meta, None

            return None, None, None

        image_asset, image_ref_meta, image_error = await _resolve_one(
            "image",
            reference_image_asset_id,
            reference_image_url,
        )
        if image_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": image_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        if image_asset:
            resolved_blocks.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": str(image_asset.get("url") or ""),
                        "detail": str(self.valves.DEFAULT_IMAGE_DETAIL or "high"),
                    },
                }
            )
            trace_rows.append(
                {
                    "media_type": "image",
                    "asset_id": image_asset.get("asset_id"),
                    "reference_input": image_ref_meta.get("reference_input") if image_ref_meta else None,
                    "matched_reference": image_ref_meta.get("matched_reference") if image_ref_meta else None,
                    "url_mode": "asset_id" if image_asset.get("asset_id") else "direct_url",
                }
            )
            resolved_assets.append(image_asset)

        video_asset, video_ref_meta, video_error = await _resolve_one(
            "video",
            reference_video_asset_id,
            reference_video_url,
        )
        if video_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": video_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        if video_asset:
            fps, _ = self._normalize_video_fps(None)
            resolved_blocks.append(
                {
                    "type": "video_url",
                    "video_url": {
                        "url": str(video_asset.get("url") or ""),
                        "fps": fps,
                    },
                }
            )
            trace_rows.append(
                {
                    "media_type": "video",
                    "asset_id": video_asset.get("asset_id"),
                    "reference_input": video_ref_meta.get("reference_input") if video_ref_meta else None,
                    "matched_reference": video_ref_meta.get("matched_reference") if video_ref_meta else None,
                    "url_mode": "asset_id" if video_asset.get("asset_id") else "direct_url",
                }
            )
            resolved_assets.append(video_asset)

        audio_asset, audio_ref_meta, audio_error = await _resolve_one(
            "audio",
            reference_audio_asset_id,
            reference_audio_url,
        )
        if audio_error:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": audio_error,
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        if audio_asset:
            resolved_blocks.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "url": str(audio_asset.get("url") or ""),
                    },
                }
            )
            trace_rows.append(
                {
                    "media_type": "audio",
                    "asset_id": audio_asset.get("asset_id"),
                    "reference_input": audio_ref_meta.get("reference_input") if audio_ref_meta else None,
                    "matched_reference": audio_ref_meta.get("matched_reference") if audio_ref_meta else None,
                    "url_mode": "asset_id" if audio_asset.get("asset_id") else "direct_url",
                }
            )
            resolved_assets.append(audio_asset)

        if not resolved_blocks:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": (
                        "No media reference provided. Use reference_*_asset_id or %reference_name in reference_*_url."
                    ),
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        normalized_granularity = self._normalize_description_granularity(granularity)
        normalized_focus = self._normalize_description_focus(focus)
        normalized_output_format = self._normalize_description_output_format(output_format)
        selected_template = self._match_template_from_request(
            description_request=(description_request or "").strip(),
            template_id=template_id,
            enforce_template_output=bool(enforce_template_output),
        )
        template_mode = bool(selected_template)
        template_media_scope = str((selected_template or {}).get("media_scope") or "").strip().lower()
        if template_mode and template_media_scope == "video" and not video_asset:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "Template storyboard_list_v1 requires a video reference.",
                    "request_id": None,
                },
                ensure_ascii=False,
            )
        llm_output_format = "structured" if template_mode else normalized_output_format

        kb_enabled = bool(self.valves.DESCRIPTION_ENABLE_KB_ENHANCEMENT)
        if enable_kb_enhancement is not None:
            kb_enabled = bool(enable_kb_enhancement)

        kb_hints: list[dict[str, Any]] = []
        kb_enhancement: dict[str, Any] = {"enabled": kb_enabled, "used": False, "error": None, "evidence_count": 0}
        if kb_enabled:
            resolved_kbs, missing_kbs, kb_error = await self._resolve_optimizer_kb_collections(__request__=__request__)
            if kb_error:
                kb_enhancement["error"] = kb_error
            elif missing_kbs:
                kb_enhancement["error"] = f"missing_kbs: {', '.join(missing_kbs)}"
            else:
                query_text = (kb_query or "").strip()
                if not query_text:
                    query_text = "\n".join(
                        [
                            str(description_request or "").strip(),
                            f"granularity={normalized_granularity}",
                            f"focus={','.join(normalized_focus)}",
                            ",".join([str(item.get("matched_reference") or "") for item in trace_rows if item.get("matched_reference")]),
                        ]
                    ).strip()
                collection_ids = [str(item.get("id") or "").strip() for item in resolved_kbs if item.get("id")]
                rows, retrieval_error = await self._query_optimizer_kb_evidence(
                    collection_names=collection_ids,
                    query_text=query_text,
                    __request__=__request__,
                )
                if retrieval_error:
                    kb_enhancement["error"] = retrieval_error
                else:
                    kb_hints = self._prepare_optimizer_evidence(rows)
                    kb_enhancement["used"] = len(kb_hints) > 0
                    kb_enhancement["evidence_count"] = len(kb_hints)
                    kb_enhancement["source_refs"] = [item.get("source_ref") for item in kb_hints[:5]]

        model_id = (model or "").strip() or self.valves.DEFAULT_MODEL
        if not self._is_supported_model(model_id):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": (
                        "model must be doubao-seed-2.0-pro family or Ark endpoint id (ep-xxxx). "
                        f"Current: {model_id}"
                    ),
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        credential = await self._resolve_ark_credential(
            __user__=__user__,
            preferred_alias=credential_alias,
        )
        if not credential.get("ok"):
            return json.dumps(credential, ensure_ascii=False)

        if template_mode:
            template_id_value = str((selected_template or {}).get("template_id") or self.DEFAULT_STORYBOARD_TEMPLATE_ID)
            missing_policy = str((selected_template or {}).get("missing_policy") or self.DEFAULT_MISSING_FIELD_POLICY)
            messages = self._build_storyboard_template_messages(
                language=(language or "zh").strip(),
                template_id=template_id_value,
                description_request=(description_request or "").strip(),
                reference_blocks=resolved_blocks,
                trace_rows=trace_rows,
                kb_hints=kb_hints,
                missing_policy=missing_policy,
            )
        else:
            messages = self._build_media_description_messages(
                language=(language or "zh").strip(),
                granularity=normalized_granularity,
                focus=normalized_focus,
                output_format=llm_output_format,
                description_request=(description_request or "").strip(),
                reference_blocks=resolved_blocks,
                trace_rows=trace_rows,
                kb_hints=kb_hints,
            )
        result = await self._ark_chat_completions(
            messages=messages,
            model=model_id,
            api_key=str(credential.get("api_key") or "").strip(),
            temperature=temperature if temperature is not None else self.valves.DEFAULT_TEMPERATURE,
            top_p=top_p if top_p is not None else self.valves.DEFAULT_TOP_P,
            max_output_tokens=max_output_tokens if max_output_tokens is not None else self.valves.DEFAULT_MAX_OUTPUT_TOKENS,
        )
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)

        provider_payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        provider_text = self._extract_message_text(provider_payload)
        if not provider_text:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 502,
                    "error_code": "InvalidProviderResponse",
                    "error_message": "Provider response does not include description text.",
                    "request_id": provider_payload.get("request_id"),
                    "raw_response": provider_payload,
                },
                ensure_ascii=False,
            )

        description_structured: Optional[dict[str, Any]] = None
        description_text = provider_text.strip()
        if template_mode:
            parsed = self._extract_json_object(provider_text)
            missing_policy = str((selected_template or {}).get("missing_policy") or self.DEFAULT_MISSING_FIELD_POLICY)
            normalized_storyboard = self._normalize_storyboard_structured(
                payload=parsed if isinstance(parsed, dict) else None,
                provider_text=provider_text,
                missing_policy=missing_policy,
            )
            description_structured = normalized_storyboard
            description_text = self._render_storyboard_template_text(normalized_storyboard)
        elif normalized_output_format == "structured":
            parsed = self._extract_json_object(provider_text)
            if parsed is None:
                return json.dumps(
                    {
                        "ok": False,
                        "status_code": 502,
                        "error_code": "InvalidProviderResponse",
                        "error_message": "Structured output requested but provider response is not valid JSON.",
                        "request_id": provider_payload.get("request_id"),
                        "provider_text": provider_text,
                    },
                    ensure_ascii=False,
                )
            description_structured = parsed
            description_text = str(parsed.get("summary") or "").strip() or self._clip_text(provider_text, 800)

        reuse_payload = self._build_reuse_payload_from_description(
            description_text=description_text,
            structured=description_structured,
            focus=normalized_focus,
        )

        payload = {
            "ok": True,
            "status_code": result.get("status_code"),
            "mode": "media_describe",
            "model": model_id,
            "granularity": normalized_granularity,
            "focus": normalized_focus,
            "output_format": normalized_output_format,
            "template_mode": template_mode,
            "template_id": (selected_template or {}).get("template_id") if template_mode else None,
            "description_text": description_text,
            "description_structured": description_structured,
            "reuse_payload": reuse_payload,
            "evidence_trace": trace_rows,
            "resolved_assets": resolved_assets,
            "kb_enhancement": kb_enhancement,
            "credential_provider": credential.get("provider"),
            "credential_alias": credential.get("credential_alias"),
            "routing_group_id": credential.get("routing_group_id"),
            "credential_source": credential.get("source"),
            "request_id": provider_payload.get("request_id"),
            "usage": provider_payload.get("usage"),
            "raw_response": provider_payload,
        }
        return json.dumps(payload, ensure_ascii=False)

    async def get_seed_pro_multimodal_input_limits(self) -> str:
        """
        Return practical limits and request shape for doubao-seed-2.0-pro multimodal calls.
        """
        sample_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "基于下列素材生成可直接用于视频生成的提示词"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://example.com/reference.png",
                            "detail": self.valves.DEFAULT_IMAGE_DETAIL,
                        },
                    },
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": "https://example.com/reference.mp4",
                            "fps": self.valves.DEFAULT_VIDEO_FPS,
                        },
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "url": "https://example.com/reference.mp3",
                        },
                    },
                ],
            }
        ]
        payload = {
            "ok": True,
            "force_media_asset_tos": bool(self.valves.FORCE_MEDIA_ASSET_TOS),
            "key_routing": {
                "enforced": bool(self.valves.ENFORCE_KEY_ROUTING),
                "provider": str(self.valves.KEY_ROUTING_PROVIDER or "seedance").strip().lower() or "seedance",
                "preferred_alias": str(self.valves.KEY_ROUTING_PREFERRED_ALIAS or "").strip() or None,
                "note": "When enforced, API key is resolved by backend key_routing.json using current user groups.",
            },
            "chat_api": {
                "method": "POST",
                "path": "/chat/completions",
                "model_field": "model",
                "messages_field": "messages",
                "model_note": "Use account-available Ark model or endpoint id (recommended ep-xxxx).",
            },
            "preferred_reference_fields": {
                "image": "reference_image_asset_id",
                "image_percent_reference": "reference_image_url=%relative_or_display_or_original_name",
                "video": "reference_video_asset_id",
                "video_percent_reference": "reference_video_url=%relative_or_display_or_original_name",
                "audio": "reference_audio_asset_id",
                "audio_percent_reference": "reference_audio_url=%relative_or_display_or_original_name",
            },
            "reference_lookup": {
                "query_params": {
                    "chat_id": "reference_chat_id (optional)",
                    "status": "reference_status (default: active)",
                },
                "match_order": ["relative_path", "display_name", "original_filename", "basename fallback"],
                "ambiguous_policy": "When basename hits multiple assets, return candidates and ask for full relative_path.",
            },
            "image_input": {
                "supported_message_type": "image_url",
                "fields": [
                    "messages[].content[].image_url.url",
                    "messages[].content[].image_url.detail",
                    "messages[].content[].image_url.image_pixel_limit.min_pixels",
                    "messages[].content[].image_url.image_pixel_limit.max_pixels",
                ],
                "detail_options": ["low", "high", "xhigh"],
                "pixel_range": [self.IMAGE_MIN_PIXELS_FLOOR, self.IMAGE_MAX_PIXELS_CEILING],
                "inline_payload_limit_bytes": int(self.valves.MAX_INLINE_IMAGE_BYTES),
            },
            "video_input": {
                "supported_message_type": "video_url",
                "fields": [
                    "messages[].content[].video_url.url",
                    "messages[].content[].video_url.fps",
                ],
                "fps_range": [self.VIDEO_FPS_MIN, self.VIDEO_FPS_MAX],
                "default_fps": self.valves.DEFAULT_VIDEO_FPS,
                "inline_payload_limit_bytes": int(self.valves.MAX_INLINE_VIDEO_BYTES),
            },
            "audio_input": {
                "supported_message_type": "input_audio",
                "fields": [
                    "messages[].content[].input_audio.url",
                    "messages[].content[].input_audio.data",
                    "messages[].content[].input_audio.format",
                ],
                "default_format": self.valves.DEFAULT_AUDIO_FORMAT,
                "inline_payload_limit_bytes": int(self.valves.MAX_INLINE_AUDIO_BYTES),
            },
            "url_modes": ["http(s) URL", "data URI base64", "file_id://", "asset://", "tos://"],
            "media_asset_resolve_api": {
                "detail": "/api/v1/media-assets/{asset_id}",
                "url": "/api/v1/media-assets/{asset_id}/url?expires_in=...",
                "default_expires_in": int(self.valves.MEDIA_ASSET_URL_EXPIRES_IN),
            },
            "sample_request_body": {
                "model": self.valves.DEFAULT_MODEL,
                "messages": sample_messages,
            },
            "notes": [
                "When FORCE_MEDIA_ASSET_TOS=true, pass *_asset_id or *_url as %reference_name to resolve TOS URLs.",
                "Large media files should prefer URL/file_id style inputs over inline base64.",
                "This tool is prompt-only and does not submit generation tasks.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    async def get_seed_pro_video_input_limits(self) -> str:
        """
        Backward-compatible alias of get_seed_pro_multimodal_input_limits.
        """
        return await self.get_seed_pro_multimodal_input_limits()

    async def get_agent_policy_summary(self) -> str:
        """
        Return business-level policy summary for this merged agent.
        """
        kb_scope = self._split_csv_values(self.valves.OPTIMIZER_KB_NAMES)
        payload = {
            "ok": True,
            "agent": "Doubao Seed Prompt Merged Agent",
            "capabilities": [
                "共创改稿（prompt co-create/revise）",
                "知识库优化（KB-backed optimizer）",
                "素材描述（media describe/reuse）",
            ],
            "routing_priority": [
                "显式口令",
                "会话上下文",
                "默认路由",
            ],
            "optimizer_policy": {
                "enabled": bool(self.valves.OPTIMIZER_ENABLED),
                "target_model_label": str(self.valves.OPTIMIZER_FIXED_MODEL or "seedance-2.0"),
                "kb_scope": kb_scope,
                "min_evidence": int(self.valves.OPTIMIZER_KB_MIN_EVIDENCE),
            },
            "media_describe_policy": {
                "default_granularity": str(self.valves.DESCRIPTION_DEFAULT_GRANULARITY or "brief"),
                "default_output_format": str(self.valves.DESCRIPTION_DEFAULT_OUTPUT_FORMAT or "text"),
                "default_focus": self._normalize_description_focus(""),
                "single_media_routing": {
                    "image": "auto_describe_then_return_raw",
                    "video_or_audio": "ask_intent_before_describe",
                    "intent_options": ["概览", "详细描述", "专业级维度描述", "按专业分镜模板输出（需确认模板）"],
                },
                "template_policy": {
                    "trigger": "explicit_user_request_only",
                    "template_source": "KB-02-模板库",
                    "missing_field_policy": "[待补充]",
                    "confirmation_each_turn": True,
                    "default_template_id": self.DEFAULT_STORYBOARD_TEMPLATE_ID,
                    "templates": self._list_template_catalog(),
                },
                "output_policy": {
                    "default": "return_api_description_text_raw",
                    "allow_minimal_header": True,
                    "no_summary_or_extension_unless_requested": True,
                },
            },
            "ephemeral_instruction_policy": {
                "ttl": "current_turn_only",
                "reset_to_default_each_turn": True,
                "persist_requires_explicit_user_confirmation": True,
                "forbid_persist_to_kb_or_registry_without_explicit_request": True,
            },
        }
        return json.dumps(payload, ensure_ascii=False)

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import Request

REQUEST_ID_PATTERNS = (
    re.compile(r"request[_ ]id\s*[:=]\s*([A-Za-z0-9_-]+)", flags=re.IGNORECASE),
    re.compile(r"request\s+id\s*[:=]?\s*([A-Za-z0-9_-]+)", flags=re.IGNORECASE),
)

_REFERENCE_PATTERN = re.compile(r"%([^\s%,，。；;:：!！?？)）\]】}》>\"“”'`]+)")
_REFERENCE_SUFFIX = ".,;:!?)\\]}>'\"，。；：！？】）》"
_MEDIA_REF_SUFFIX = ".,;:!?)\\]}>'\"，。；：！？】）》"
_MEDIA_TYPES = {"image", "video", "audio"}


def build_base_url(__request__: Optional[Request], fallback_base_url: str) -> str:
    if __request__ is not None and __request__.url is not None:
        return f"{__request__.url.scheme}://{__request__.url.netloc}"
    return str(fallback_base_url or "").rstrip("/")


def build_auth_headers(
    __request__: Optional[Request],
    fallback_api_key: str,
    *,
    include_content_type: bool = True,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if include_content_type:
        headers["Content-Type"] = "application/json"

    if __request__ is not None:
        auth_header = __request__.headers.get("Authorization")
        if auth_header:
            headers["Authorization"] = auth_header
            return headers

        token_cookie = __request__.cookies.get("token")
        if token_cookie:
            headers["Authorization"] = f"Bearer {token_cookie}"
            return headers

    fallback = str(fallback_api_key or "").strip()
    if fallback:
        headers["Authorization"] = f"Bearer {fallback}"

    return headers


def extract_request_id(text: str) -> Optional[str]:
    value = str(text or "")
    if not value:
        return None

    for pattern in REQUEST_ID_PATTERNS:
        match = pattern.search(value)
        if match:
            rid = str(match.group(1) or "").strip()
            if rid:
                return rid

    return None


def _extract_nested_json_error(detail_text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    text = str(detail_text or "")
    pos = text.find("{")
    if pos < 0:
        return None, None, extract_request_id(text)

    try:
        nested = json.loads(text[pos:])
    except Exception:
        return None, None, extract_request_id(text)

    if not isinstance(nested, dict):
        return None, None, extract_request_id(text)

    error = nested.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        request_id = error.get("request_id") or extract_request_id(str(message or ""))
        return code, message, request_id

    return None, None, extract_request_id(text)


def normalize_error_payload(status_code: int, raw_text: str, payload: Any) -> dict[str, Any]:
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    request_id: Optional[str] = None

    if isinstance(payload, dict):
        detail = payload.get("detail")

        if isinstance(detail, dict):
            error_code = detail.get("error_code") or detail.get("code")
            error_message = detail.get("error_message") or detail.get("message") or detail.get("error")
            request_id = detail.get("request_id")

            nested_error = detail.get("error")
            if isinstance(nested_error, dict):
                error_code = error_code or nested_error.get("code")
                error_message = error_message or nested_error.get("message")
                request_id = request_id or nested_error.get("request_id")
            elif isinstance(nested_error, str) and not error_message:
                error_message = nested_error

        elif isinstance(detail, str):
            nested_code, nested_message, nested_request_id = _extract_nested_json_error(detail)
            error_code = error_code or nested_code
            error_message = error_message or nested_message or detail
            request_id = request_id or nested_request_id

        error = payload.get("error")
        if isinstance(error, dict):
            error_code = error_code or error.get("code")
            error_message = error_message or error.get("message")
            request_id = request_id or error.get("request_id")
        elif isinstance(error, str) and not error_message:
            error_message = error

        output = payload.get("output")
        if isinstance(output, dict):
            error_code = error_code or output.get("code")
            error_message = error_message or output.get("message")

        error_code = error_code or payload.get("error_code") or payload.get("code")
        error_message = error_message or payload.get("error_message") or payload.get("message")
        request_id = request_id or payload.get("request_id")

    if not error_message:
        error_message = str(raw_text or "")
    if not request_id:
        request_id = extract_request_id(error_message)

    return {
        "ok": False,
        "status_code": int(status_code),
        "error": str(raw_text or ""),
        "error_code": error_code,
        "error_message": error_message,
        "request_id": request_id,
    }


def normalize_httpx_error(response: httpx.Response) -> dict[str, Any]:
    raw_text = response.text
    try:
        payload = response.json()
    except Exception:
        payload = None
    return normalize_error_payload(response.status_code, raw_text, payload)


async def request_openwebui_json(
    *,
    method: str,
    path: str,
    __request__: Optional[Request],
    timeout_seconds: int,
    openwebui_base_url: str,
    openwebui_api_key: str,
    body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    base_url = build_base_url(__request__, openwebui_base_url).rstrip("/")
    url = f"{base_url}{path}"
    headers = build_auth_headers(__request__, openwebui_api_key, include_content_type=True)

    request_kwargs: dict[str, Any] = {}
    if body is not None:
        request_kwargs["json"] = body

    async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=False) as client:
        response = await client.request(method=method, url=url, headers=headers, **request_kwargs)

    if response.status_code >= 400:
        return normalize_httpx_error(response)

    try:
        payload = response.json()
    except Exception:
        payload = {"raw_text": response.text}

    return {"ok": True, "status_code": response.status_code, "data": payload}


def extract_media_asset_references(prompt: str) -> list[str]:
    refs = _REFERENCE_PATTERN.findall(prompt or "")
    cleaned: list[str] = []
    for ref in refs:
        value = str(ref).strip().rstrip(_REFERENCE_SUFFIX)
        if value:
            cleaned.append(value)
    return list(dict.fromkeys(cleaned))


def compact_media_asset_item(item: dict[str, Any]) -> dict[str, Any]:
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


def _is_http_url(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith(("http://", "https://"))


def _normalize_media_asset_candidates(item: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("relative_path", "display_name", "original_filename"):
        value = str(item.get(key) or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _media_ref_tokens(value: str, *, workdir: str = "") -> list[str]:
    raw = str(value or "").strip().strip("\"'")
    if not raw:
        return []

    out: list[str] = []

    def add_token(token: str) -> None:
        text = str(token or "").strip().strip("\"'")
        if not text:
            return
        text = text.rstrip(_MEDIA_REF_SUFFIX)
        if text.startswith("%"):
            text = text[1:]
        if text.startswith("./"):
            text = text[2:]
        if text.startswith("/"):
            text = text.lstrip("/")
        if text and text not in out:
            out.append(text)

    add_token(raw)

    p = Path(raw)
    add_token(p.name)

    resolved_workdir = str(Path(workdir).expanduser().resolve()) if str(workdir or "").strip() else ""
    if resolved_workdir and raw.startswith(resolved_workdir):
        rel = raw[len(resolved_workdir):].lstrip("/\\")
        add_token(rel)
        add_token(Path(rel).name)

    if "/%" in raw or "\\%" in raw:
        add_token(Path(raw).name)

    return out


class AUMediaReferenceBridge:
    """
    Resolve WebUI-style media references (for example `%image_001.png`) into usable
    http(s) URLs before handing arguments to `au vendor ...`.
    """

    def __init__(
        self,
        *,
        __request__: Optional[Request],
        request_timeout_seconds: int,
        openwebui_base_url: str,
        openwebui_api_key: str,
        chat_id: str = "",
        status: str = "active",
        url_expires_in: int = 3600,
    ) -> None:
        self.__request__ = __request__
        self.request_timeout_seconds = int(request_timeout_seconds)
        self.openwebui_base_url = str(openwebui_base_url or "").strip()
        self.openwebui_api_key = str(openwebui_api_key or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.status = str(status or "active").strip() or "active"
        self.url_expires_in = max(60, min(int(url_expires_in or 3600), 7 * 24 * 3600))

        self._loaded = False
        self._asset_item_by_type: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in _MEDIA_TYPES}
        self._alias_to_ref_by_type: dict[str, dict[str, str]] = {t: {} for t in _MEDIA_TYPES}
        self._basename_to_ref_by_type: dict[str, dict[str, list[str]]] = {t: {} for t in _MEDIA_TYPES}
        self._available_refs_by_type: dict[str, list[str]] = {t: [] for t in _MEDIA_TYPES}
        self._asset_url_cache: dict[str, Optional[str]] = {}

    async def _request_openwebui(self, method: str, path: str) -> dict[str, Any]:
        return await request_openwebui_json(
            method=method,
            path=path,
            __request__=self.__request__,
            timeout_seconds=self.request_timeout_seconds,
            openwebui_base_url=self.openwebui_base_url,
            openwebui_api_key=self.openwebui_api_key,
            body=None,
        )

    async def _load_assets(self) -> None:
        if self._loaded:
            return

        page_size = 200
        offset = 0
        rows: list[dict[str, Any]] = []
        while True:
            query: dict[str, Any] = {"limit": page_size, "offset": offset}
            if self.status:
                query["status"] = self.status
            if self.chat_id:
                query["chat_id"] = self.chat_id
            path = f"/api/v1/media-assets/?{urlencode(query)}"
            result = await self._request_openwebui("GET", path)
            if not result.get("ok"):
                self._loaded = True
                return

            page_rows = [row for row in (result.get("data") or []) if isinstance(row, dict)]
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                break
            offset += page_size
            if offset >= 4000:
                break

        for item in rows:
            media_type = str(item.get("media_type") or "").strip().lower()
            if media_type not in _MEDIA_TYPES:
                continue

            candidates = _normalize_media_asset_candidates(item)
            if not candidates:
                continue
            canonical_ref = candidates[0]
            item_map = self._asset_item_by_type[media_type]
            alias_map = self._alias_to_ref_by_type[media_type]
            basename_map = self._basename_to_ref_by_type[media_type]
            available = self._available_refs_by_type[media_type]

            if canonical_ref not in item_map:
                item_map[canonical_ref] = item
                available.append(canonical_ref)

            for candidate in candidates:
                if candidate and candidate not in alias_map:
                    alias_map[candidate] = canonical_ref

            basename = Path(canonical_ref).name
            if basename:
                refs = basename_map.setdefault(basename, [])
                if canonical_ref not in refs:
                    refs.append(canonical_ref)

        self._loaded = True

    async def _asset_url(self, asset_id: str) -> Optional[str]:
        aid = str(asset_id or "").strip()
        if not aid:
            return None
        if aid in self._asset_url_cache:
            return self._asset_url_cache[aid]

        path = f"/api/v1/media-assets/{aid}/url?{urlencode({'expires_in': self.url_expires_in})}"
        result = await self._request_openwebui("GET", path)
        if not result.get("ok"):
            self._asset_url_cache[aid] = None
            return None

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        url = str(data.get("url") or "").strip()
        if not _is_http_url(url):
            url = ""
        self._asset_url_cache[aid] = url or None
        return self._asset_url_cache[aid]

    def _match_reference(self, *, media_type: str, tokens: list[str]) -> tuple[Optional[str], list[str]]:
        alias_map = self._alias_to_ref_by_type.get(media_type) or {}
        basename_map = self._basename_to_ref_by_type.get(media_type) or {}

        for token in tokens:
            ref = alias_map.get(token)
            if ref:
                return ref, []

        ambiguous: list[str] = []
        for token in tokens:
            basename = Path(token).name
            if not basename:
                continue
            refs = basename_map.get(basename) or []
            if len(refs) == 1:
                return refs[0], []
            if len(refs) > 1:
                ambiguous.extend(refs)

        return None, sorted(list(dict.fromkeys(ambiguous)))

    async def resolve_media_inputs(
        self,
        *,
        values: Optional[list[str]],
        media_type: str,
        workdir: str = "",
    ) -> dict[str, Any]:
        target_type = str(media_type or "").strip().lower()
        if target_type not in _MEDIA_TYPES:
            return {
                "ok": False,
                "status_code": 400,
                "error_code": "InvalidParameter",
                "error_message": f"Unsupported media_type: {media_type}",
                "resolved_values": [],
                "prompt_resources": [],
            }

        cleaned_values: list[str] = []
        for value in values or []:
            text = str(value or "").strip()
            if text:
                cleaned_values.append(text)

        if not cleaned_values:
            return {
                "ok": True,
                "status_code": 200,
                "resolved_values": [],
                "prompt_resources": [],
                "missing_references": [],
                "ambiguous_references": [],
                "available_references": [],
                "unresolved_inputs": [],
            }

        await self._load_assets()

        resolved_values: list[str] = []
        prompt_resources: list[dict[str, str]] = []
        missing_references: list[str] = []
        ambiguous_references: list[dict[str, Any]] = []
        unresolved_inputs: list[dict[str, Any]] = []

        workdir_path = Path(workdir).expanduser().resolve() if str(workdir or "").strip() else None
        item_map = self._asset_item_by_type.get(target_type) or {}
        available_references = sorted(list(dict.fromkeys(self._available_refs_by_type.get(target_type) or [])))

        for raw in cleaned_values:
            if _is_http_url(raw):
                resolved_values.append(raw)
                prompt_resources.append({"name": Path(raw).name or f"{target_type}_ref", "url": raw})
                continue

            candidate_path = Path(raw).expanduser()
            if candidate_path.exists() and candidate_path.is_file():
                resolved_values.append(str(candidate_path.resolve()))
                continue
            if not candidate_path.is_absolute() and workdir_path is not None:
                joined = (workdir_path / raw).resolve()
                if joined.exists() and joined.is_file():
                    resolved_values.append(str(joined))
                    continue

            tokens = _media_ref_tokens(raw, workdir=str(workdir_path or ""))
            ref, ambiguous = self._match_reference(media_type=target_type, tokens=tokens)
            if ambiguous:
                ambiguous_references.append(
                    {
                        "input": raw,
                        "tokens": tokens,
                        "candidates": ambiguous,
                    }
                )
                unresolved_inputs.append({"input": raw, "reason": "ambiguous_reference"})
                continue

            if not ref:
                missing_references.append(raw)
                unresolved_inputs.append({"input": raw, "reason": "missing_reference"})
                continue

            item = item_map.get(ref) or {}
            asset_id = str(item.get("asset_id") or "").strip()
            url = await self._asset_url(asset_id)
            if not url:
                unresolved_inputs.append(
                    {
                        "input": raw,
                        "asset_id": asset_id or None,
                        "reason": "asset_url_unavailable",
                    }
                )
                continue

            resolved_values.append(url)
            prompt_resources.append({"name": ref, "url": url})

        if unresolved_inputs:
            return {
                "ok": False,
                "status_code": 400,
                "error_code": "MissingMediaAssetReferences",
                "error_message": f"Failed to resolve some {target_type} references",
                "resolved_values": resolved_values,
                "prompt_resources": prompt_resources,
                "missing_references": sorted(list(dict.fromkeys(missing_references))),
                "ambiguous_references": ambiguous_references,
                "available_references": available_references,
                "unresolved_inputs": unresolved_inputs,
            }

        return {
            "ok": True,
            "status_code": 200,
            "resolved_values": resolved_values,
            "prompt_resources": prompt_resources,
            "missing_references": [],
            "ambiguous_references": [],
            "available_references": available_references,
            "unresolved_inputs": [],
        }


async def bridge_upsert(
    *,
    requester,
    payload: dict[str, Any],
    __request__: Optional[Request],
) -> bool:
    result = await requester("POST", "/api/v1/tasks/bridge/upsert", __request__, payload)
    return bool(result.get("ok"))

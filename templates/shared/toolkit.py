from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from fastapi import Request

REQUEST_ID_PATTERNS = (
    re.compile(r"request[_ ]id\s*[:=]\s*([A-Za-z0-9_-]+)", flags=re.IGNORECASE),
    re.compile(r"request\s+id\s*[:=]?\s*([A-Za-z0-9_-]+)", flags=re.IGNORECASE),
)

_REFERENCE_PATTERN = re.compile(r"%([^\s%,，。；;:：!！?？)）\]】}》>\"“”'`]+)")
_REFERENCE_SUFFIX = ".,;:!?)\\]}>'\"，。；：！？】）》"


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


async def bridge_upsert(
    *,
    requester,
    payload: dict[str, Any],
    __request__: Optional[Request],
) -> bool:
    result = await requester("POST", "/api/v1/tasks/bridge/upsert", __request__, payload)
    return bool(result.get("ok"))

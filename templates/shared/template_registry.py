from __future__ import annotations

from typing import Any

DEFAULT_STORYBOARD_TEMPLATE_ID = "storyboard_list_v1"
DEFAULT_MISSING_FIELD_POLICY = "[待补充]"

TEMPLATE_REGISTRY: dict[str, dict[str, Any]] = {
    "storyboard_list_v1": {
        "template_id": "storyboard_list_v1",
        "name": "专业视频分镜脚本模板",
        "media_scope": "video",
        "missing_policy": DEFAULT_MISSING_FIELD_POLICY,
        "trigger_phrases": [
            "按专业分镜模板输出",
            "按分镜模板返回",
            "按模板输出分镜脚本",
            "按模板输出",
            "模板输出",
        ],
        "menu_hint": "适用于广告/剧情类视频的完整分镜脚本描述",
    },
}

_TEMPLATE_ALIASES = {
    "storyboard_list_v1": "storyboard_list_v1",
    "storyboard": "storyboard_list_v1",
    "storyboard_v1": "storyboard_list_v1",
    "分镜模板": "storyboard_list_v1",
    "专业分镜模板": "storyboard_list_v1",
    "专业视频分镜脚本模板": "storyboard_list_v1",
}


def list_template_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in TEMPLATE_REGISTRY.values():
        rows.append(
            {
                "template_id": item.get("template_id"),
                "name": item.get("name"),
                "media_scope": item.get("media_scope"),
                "trigger_phrases": item.get("trigger_phrases") or [],
                "menu_hint": item.get("menu_hint"),
                "missing_policy": item.get("missing_policy") or DEFAULT_MISSING_FIELD_POLICY,
            }
        )
    return rows


def normalize_template_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in TEMPLATE_REGISTRY:
        return raw
    lowered = raw.lower()
    if lowered in TEMPLATE_REGISTRY:
        return lowered
    alias = _TEMPLATE_ALIASES.get(raw) or _TEMPLATE_ALIASES.get(lowered)
    return alias or ""


def match_template(
    *,
    description_request: str,
    template_id: str,
    enforce_template_output: bool,
) -> dict[str, Any] | None:
    normalized_template_id = normalize_template_id(template_id)
    if normalized_template_id and normalized_template_id in TEMPLATE_REGISTRY:
        return TEMPLATE_REGISTRY.get(normalized_template_id)

    request_text = str(description_request or "")
    if request_text:
        for item in TEMPLATE_REGISTRY.values():
            phrases = item.get("trigger_phrases") or []
            if any(str(phrase).strip() and str(phrase) in request_text for phrase in phrases):
                return item

    if enforce_template_output:
        default_item = TEMPLATE_REGISTRY.get(DEFAULT_STORYBOARD_TEMPLATE_ID)
        if default_item is not None:
            return default_item
        first = next(iter(TEMPLATE_REGISTRY.values()), None)
        if isinstance(first, dict):
            return first

    return None

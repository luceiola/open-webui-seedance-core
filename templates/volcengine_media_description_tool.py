"""
title: Volcengine Media Description Tool
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
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from pydantic import BaseModel, Field


_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.append(str(_TOOL_DIR))


def _ensure_shared_toolkit_loaded(*, force_reload: bool = False) -> None:
    import types

    toolkit_mod = sys.modules.get("shared.toolkit")
    if toolkit_mod and hasattr(toolkit_mod, "AUMediaReferenceBridge") and not force_reload:
        return

    candidates = [
        _TOOL_DIR / "shared" / "toolkit.py",
        Path.cwd() / "templates" / "shared" / "toolkit.py",
        Path.cwd().parent / "templates" / "shared" / "toolkit.py",
    ]
    toolkit_path = next((path for path in candidates if path.exists() and path.is_file()), None)
    if toolkit_path is None:
        return

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
try:
    from shared.toolkit import AUMediaReferenceBridge
except Exception:
    _ensure_shared_toolkit_loaded(force_reload=True)
    from shared.toolkit import AUMediaReferenceBridge


DESCRIPTION_METHODS: dict[str, dict[str, str]] = {
    "quick_overview": {
        "label": "快速概述",
        "instruction": (
            "用简洁段落概括主体、主要动作、场景和最重要的信息。"
            "只保留能帮助用户快速理解素材的细节。"
        ),
    },
    "detailed_visual": {
        "label": "详细视觉分析",
        "instruction": (
            "系统描述主体、动作、环境、构图、景别、视角、色彩、光线、风格和关键细节。"
            "视频还要说明时间变化、镜头运动、可辨识声音及画面文字。"
        ),
    },
    "video_timeline": {
        "label": "视频时间线",
        "instruction": (
            "按 MM:SS 时间戳分段描述视频。每段包含镜头变化、主体动作、场景变化、"
            "运镜、可辨识声音或对白以及字幕；无法精确定位时标注约略时间。"
        ),
    },
    "accessibility": {
        "label": "无障碍描述",
        "instruction": (
            "生成面向无障碍使用的描述，优先传达素材在当前上下文中的意义和必要视觉信息。"
            "避免重复用户已经提供的文字，也不要堆砌无关装饰细节。"
        ),
    },
    "prompt_reconstruction": {
        "label": "生成提示词反推",
        "instruction": (
            "将可观察内容整理为可供图像或视频生成模型使用的提示词，清楚覆盖主体、动作、"
            "环境、构图、镜头、光线、色彩和风格。推断项必须明确标记，不得虚构品牌、人物身份或创作来源。"
        ),
    },
    "text_extraction": {
        "label": "OCR/字幕提取",
        "instruction": (
            "提取所有可辨识文字，尽量保持阅读顺序、分组和换行。视频文字附 MM:SS 时间戳，"
            "区分画面文字、字幕和可辨识对白；无法确认的字符使用 [无法辨认]。"
        ),
    },
}


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
            default="ARK_API_KEY",
            description="Environment variable containing the Volcengine API key.",
        )
        DEFAULT_MODEL: str = Field(
            default="",
            description="Optional model override. Empty uses the current ai-utility registry default.",
        )
        REQUEST_TIMEOUT_SECONDS: int = Field(default=180, ge=30, le=1800)
        MEDIA_URL_EXPIRES_IN_SECONDS: int = Field(default=3600, ge=60, le=604800)
        SUBPROCESS_TIMEOUT_SECONDS: int = Field(default=900, ge=30, le=7200)

    def __init__(self) -> None:
        self.valves = self.Valves()

    def _error(
        self,
        *,
        status_code: int,
        error_code: str,
        error_message: str,
        request_id: Optional[str] = None,
        **extra: Any,
    ) -> str:
        payload: dict[str, Any] = {
            "ok": False,
            "status_code": int(status_code),
            "error_code": str(error_code),
            "error_message": str(error_message),
            "request_id": request_id,
        }
        payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)

    def _validate_method(self, description_method: str, media_type: str) -> tuple[Optional[str], Optional[str]]:
        method = str(description_method or "").strip() or "detailed_visual"
        if method not in DESCRIPTION_METHODS:
            return None, f"description_method must be one of: {', '.join(DESCRIPTION_METHODS)}"
        if method == "video_timeline" and media_type != "video":
            return None, "video_timeline is only available for video"
        return method, None

    def _build_prompt(
        self,
        *,
        media_type: str,
        description_method: str,
        custom_instruction: str,
        output_language: str,
    ) -> str:
        method = DESCRIPTION_METHODS[description_method]
        media_label = "图片" if media_type == "image" else "视频"
        language = str(output_language or "").strip() or "zh-CN"
        custom = str(custom_instruction or "").strip()
        parts = [
            f"请分析随本消息提供的单个{media_label}。",
            f"描述方法：{method['label']}。{method['instruction']}",
            f"使用 {language} 输出。",
            "只陈述素材中可观察到的内容；不确定的信息必须明确写为不确定或无法确认。",
            "不要猜测人物真实身份、地点、品牌、时间、事件背景或素材来源。",
            "直接输出最终描述，不解释分析过程，也不要复述这些指令。",
        ]
        if custom:
            parts.insert(3, f"用户补充关注点：{custom}")
        return "\n".join(parts)

    async def _resolve_media(
        self,
        *,
        reference: str,
        media_type: str,
        __request__: Optional[Request],
    ) -> dict[str, Any]:
        raw = str(reference or "").strip()
        if not raw:
            return {
                "ok": False,
                "status_code": 400,
                "error_code": "InvalidParameter",
                "error_message": f"{media_type} is required",
                "request_id": None,
            }
        if raw.startswith(("http://", "https://")):
            return {"ok": True, "url": raw, "prompt_resources": []}

        bridge = AUMediaReferenceBridge(
            __request__=__request__,
            request_timeout_seconds=self.valves.REQUEST_TIMEOUT_SECONDS,
            openwebui_base_url=self.valves.OPENWEBUI_BASE_URL,
            openwebui_api_key=self.valves.OPENWEBUI_API_KEY,
            chat_id="",
            status="active",
            url_expires_in=self.valves.MEDIA_URL_EXPIRES_IN_SECONDS,
        )
        resolved = await bridge.resolve_media_inputs(
            values=[raw],
            media_type=media_type,
            workdir=self.valves.AU_WORKDIR,
        )
        if not resolved.get("ok"):
            return resolved

        values = [str(value or "").strip() for value in resolved.get("resolved_values") or []]
        values = [value for value in values if value]
        if len(values) != 1:
            return {
                "ok": False,
                "status_code": 400,
                "error_code": "SingleMediaRequired",
                "error_message": f"exactly one {media_type} must resolve, got {len(values)}",
                "request_id": None,
            }
        if not values[0].startswith(("http://", "https://")):
            return {
                "ok": False,
                "status_code": 400,
                "error_code": "MediaUrlRequired",
                "error_message": "media must resolve to an absolute HTTP(S) URL; upload it to media-assets first",
                "request_id": None,
            }
        return {
            "ok": True,
            "url": values[0],
            "prompt_resources": list(resolved.get("prompt_resources") or []),
        }

    async def _run_au_vendor_json(
        self,
        *,
        command_args: list[str],
    ) -> dict[str, Any]:
        au_bin = str(self.valves.AU_BIN or "").strip()
        if not au_bin:
            raise RuntimeError("AU_BIN is empty")
        workdir = Path(str(self.valves.AU_WORKDIR or "")).expanduser().resolve()
        if not workdir.exists() or not workdir.is_dir():
            raise RuntimeError(f"AU_WORKDIR does not exist or is not a directory: {workdir}")

        resolved_env = str(self.valves.AU_API_KEY_ENV or "").strip()
        if not resolved_env:
            raise RuntimeError("api key env is required")

        argv = [au_bin, "vendor", *command_args, "--api-key-env", resolved_env, "--full-json", "--quiet"]
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                argv,
                cwd=str(workdir),
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=int(self.valves.SUBPROCESS_TIMEOUT_SECONDS),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"au command timed out after {exc.timeout}s") from exc
        except FileNotFoundError as exc:
            raise RuntimeError(f"au executable not found: {au_bin}") from exc

        stdout = str(completed.stdout or "").strip()
        stderr = str(completed.stderr or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(stderr or stdout or f"au command failed with exit code {completed.returncode}")
        if not stdout:
            raise RuntimeError("au command returned empty stdout")

        try:
            payload = json.loads(stdout)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        for line in reversed([line.strip() for line in stdout.splitlines() if line.strip()]):
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                continue
        raise RuntimeError("failed to parse JSON from au command output")

    def _extract_content(self, payload: dict[str, Any]) -> tuple[Optional[str], dict[str, Any]]:
        response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        if not isinstance(response, dict):
            return None, {}
        choices = response.get("choices") if isinstance(response.get("choices"), list) else []
        if not choices or not isinstance(choices[0], dict):
            return None, response
        message = choices[0].get("message") if isinstance(choices[0].get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip() or None, response
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text = part["text"].strip()
                    if text:
                        text_parts.append(text)
            combined = "\n".join(text_parts).strip()
            return combined or None, response
        return None, response

    def _extract_request_id(self, text: str) -> Optional[str]:
        match = re.search(r"request[_ ]id\s*[:=]\s*([A-Za-z0-9_-]+)", str(text or ""), flags=re.I)
        return str(match.group(1)) if match else None

    async def _describe(
        self,
        *,
        reference: str,
        media_type: str,
        description_method: str,
        custom_instruction: str,
        output_language: str,
        __request__: Optional[Request],
    ) -> str:
        method, method_error = self._validate_method(description_method, media_type)
        if method_error or method is None:
            return self._error(
                status_code=400,
                error_code="InvalidDescriptionMethod",
                error_message=method_error or "invalid description method",
            )
        if len(str(custom_instruction or "")) > 4000:
            return self._error(
                status_code=400,
                error_code="InvalidParameter",
                error_message="custom_instruction must not exceed 4000 characters",
            )

        resolved = await self._resolve_media(
            reference=reference,
            media_type=media_type,
            __request__=__request__,
        )
        if not resolved.get("ok"):
            return json.dumps(resolved, ensure_ascii=False)

        prompt = self._build_prompt(
            media_type=media_type,
            description_method=method,
            custom_instruction=custom_instruction,
            output_language=output_language,
        )
        command_args = ["ve-multimodal-chat", "--prompt", prompt]
        command_args.extend(["--image-url" if media_type == "image" else "--file-url", str(resolved["url"])])
        model = str(self.valves.DEFAULT_MODEL or "").strip()
        if model:
            command_args.extend(["--model", model])

        try:
            raw = await self._run_au_vendor_json(command_args=command_args)
        except Exception as exc:
            message = str(exc)
            return self._error(
                status_code=502,
                error_code="CommandExecutionFailed",
                error_message=message,
                request_id=self._extract_request_id(message),
            )

        content, response = self._extract_content(raw)
        if not content:
            return self._error(
                status_code=502,
                error_code="InvalidProviderResponse",
                error_message="Volcengine response did not contain choices[0].message.content",
                request_id=str(response.get("request_id") or "").strip() or None,
            )
        result = {
            "ok": True,
            "status_code": 200,
            "media_type": media_type,
            "method": method,
            "method_label": DESCRIPTION_METHODS[method]["label"],
            "content": content,
            "response_id": str(response.get("id") or "").strip() or None,
            "model": str(response.get("model") or model or "").strip() or None,
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else None,
        }
        return json.dumps(result, ensure_ascii=False)

    async def describe_image(
        self,
        image: str,
        description_method: str = "detailed_visual",
        custom_instruction: str = "",
        output_language: str = "zh-CN",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """Describe one image URL or one OpenWebUI %media reference using a preset method."""
        return await self._describe(
            reference=image,
            media_type="image",
            description_method=description_method,
            custom_instruction=custom_instruction,
            output_language=output_language,
            __request__=__request__,
        )

    async def describe_video(
        self,
        video: str,
        description_method: str = "detailed_visual",
        custom_instruction: str = "",
        output_language: str = "zh-CN",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        """Describe one video URL or one OpenWebUI %media reference using a preset method."""
        return await self._describe(
            reference=video,
            media_type="video",
            description_method=description_method,
            custom_instruction=custom_instruction,
            output_language=output_language,
            __request__=__request__,
        )

"""
title: BTN Image2 Tool
author: local-dev
version: 0.2.2
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

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
    request_openwebui_json,
)

try:
    from shared.toolkit import AUMediaReferenceBridge, extract_media_asset_references
except Exception:
    _ensure_shared_toolkit_loaded(force_reload=True)
    from shared.toolkit import AUMediaReferenceBridge, extract_media_asset_references


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
            default="BTN_IMAGE_2_API_KEY",
            description="Default API key env name passed to au vendor commands.",
        )
        KEY_ROUTING_PROVIDER: str = Field(
            default="btn_image2",
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
        DEFAULT_SIZE: str = Field(default="1024x1792")
        DEFAULT_QUALITY: str = Field(default="auto")
        DEFAULT_MODEL: str = Field(default="gpt-image-2")
        TASK_ARTIFACT_ROOT: str = Field(
            default="",
            description="Optional root directory for btn-image2 task artifacts (json/images).",
        )
        SUMMARY_IMAGE_FILE_LIMIT: int = Field(default=3, ge=1, le=5)

    _PROVIDER = "tapque_image2"
    _SKILL_NAME = "btn-image2"
    _TOOL_NAME_GEN = "btn_image2_tool.generate_image_with_btn_image2_gen"
    _TOOL_NAME_EDIT = "btn_image2_tool.edit_image_with_btn_image2"

    def __init__(self) -> None:
        self.valves = self.Valves()
        self._active_jobs: dict[str, asyncio.Task[None]] = {}

    def _base_url(self, __request__: Optional[Request]) -> str:
        return build_base_url(__request__, self.valves.OPENWEBUI_BASE_URL)

    def _headers(self, __request__: Optional[Request]) -> dict[str, str]:
        return build_auth_headers(
            __request__,
            self.valves.OPENWEBUI_API_KEY,
            include_content_type=True,
        )

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
        provider = str(self.valves.KEY_ROUTING_PROVIDER or "btn_image2").strip().lower() or "btn_image2"
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
        tool_name: str,
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
            "tool_name": tool_name,
            "skill_name": self._SKILL_NAME,
            "status": (status or "").strip() or "RUNNING",
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
            payload["prompt_text"] = prompt_text
        if generation_params is not None:
            payload["generation_params"] = generation_params
        if prompt_resources is not None:
            payload["prompt_resources"] = prompt_resources
        if credential_alias is not None:
            payload["credential_alias"] = str(credential_alias).strip() or None
        if routing_group_id is not None:
            payload["routing_group_id"] = str(routing_group_id).strip() or None

        return await bridge_upsert(
            requester=self._request,
            payload=payload,
            __request__=__request__,
        )

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

    def _resolve_task_artifact_paths(self, *, task_id: str, user_id: str) -> dict[str, str]:
        uid = str(user_id or "").strip() or "anonymous"
        configured_root = str(self.valves.TASK_ARTIFACT_ROOT or "").strip()
        if configured_root:
            root = Path(configured_root).expanduser().resolve() / uid / "btn_image2"
        else:
            root: Optional[Path] = None

            data_dir = str(os.getenv("DATA_DIR") or "").strip()
            if data_dir:
                root = (
                    Path(data_dir).expanduser().resolve()
                    / "cache"
                    / "material_packages"
                    / uid
                    / "task_vendor_artifacts"
                    / "btn_image2"
                )

            if root is None:
                try:
                    from open_webui.config import CACHE_DIR as OPENWEBUI_CACHE_DIR

                    cache_dir = Path(OPENWEBUI_CACHE_DIR).expanduser().resolve()
                    root = cache_dir / "material_packages" / uid / "task_vendor_artifacts" / "btn_image2"
                except Exception:
                    root = None

            if root is None:
                cwd = Path.cwd().resolve()
                candidates = [
                    cwd / ".data-prod" / "cache" / "material_packages" / uid / "task_vendor_artifacts" / "btn_image2",
                    cwd / ".data-dev" / "cache" / "material_packages" / uid / "task_vendor_artifacts" / "btn_image2",
                    cwd / ".data" / "cache" / "material_packages" / uid / "task_vendor_artifacts" / "btn_image2",
                    cwd / "backend" / "open_webui" / "data" / "cache" / "material_packages" / uid / "task_vendor_artifacts" / "btn_image2",
                ]
                root = candidates[0]
                for candidate in candidates:
                    if candidate.parent.exists():
                        root = candidate
                        break

        task_dir = root / task_id
        image_dir = task_dir / "images"
        json_file = task_dir / "result.json"
        task_dir.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)
        return {
            "task_dir": str(task_dir),
            "json_file": str(json_file),
            "image_output_dir": str(image_dir),
        }

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    def _summarize_btn_payload(self, payload: dict[str, Any], *, json_file: str) -> dict[str, Any]:
        response_node = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        data_rows = response_node.get("data") if isinstance(response_node.get("data"), list) else []
        output_images = len(data_rows)

        output_urls = self._extract_http_urls(response_node, max_items=12)
        primary_image_url = output_urls[0] if output_urls else None

        saved_images = payload.get("saved_images") if isinstance(payload.get("saved_images"), dict) else {}
        saved_image_count = self._to_int(saved_images.get("saved_count"), default=0)
        saved_image_dir = str(saved_images.get("output_dir") or "").strip() or None
        saved_files_raw = saved_images.get("saved_files") if isinstance(saved_images.get("saved_files"), list) else []
        saved_files: list[str] = []
        for item in saved_files_raw:
            text = str(item or "").strip()
            if text:
                saved_files.append(text)
        image_files = saved_files[: max(1, int(self.valves.SUMMARY_IMAGE_FILE_LIMIT))]

        error_code, error_message, failed_reason = self._extract_failure(payload)
        merged_error = error_message or failed_reason
        has_error = bool((error_code or merged_error) and not output_urls and saved_image_count <= 0)

        if output_images <= 0 and output_urls:
            output_images = len(output_urls)
        if output_images <= 0 and saved_image_count > 0:
            output_images = saved_image_count

        return {
            "output_images": output_images,
            "output_urls": output_urls,
            "primary_image_url": primary_image_url,
            "saved_image_count": saved_image_count,
            "saved_image_dir": saved_image_dir,
            "image_files": image_files,
            "json_file": str(json_file),
            "error_code": error_code,
            "error_message": merged_error,
            "request_id": None,
            "has_error": has_error,
        }

    def _build_submit_response(
        self,
        *,
        task_id: str,
        status: str,
        model: str,
        size: str,
        quality: str,
        summary: dict[str, Any],
        credential_alias: Optional[str],
        routing_group_id: Optional[str],
    ) -> str:
        return json.dumps(
            {
                "ok": str(status).upper() not in {"FAILED"},
                "task_id": task_id,
                "response_id": task_id,
                "status": status,
                "model": model,
                "size": size,
                "quality": quality,
                "output_images": self._to_int(summary.get("output_images"), default=0),
                "saved_image_count": self._to_int(summary.get("saved_image_count"), default=0),
                "saved_image_dir": summary.get("saved_image_dir"),
                "json_file": summary.get("json_file"),
                "image_files": list(summary.get("image_files") or []),
                "image_url": summary.get("primary_image_url"),
                "image_url_markdown": (
                    f"[查看图片]({summary.get('primary_image_url')})"
                    if str(summary.get("primary_image_url") or "").startswith(("http://", "https://"))
                    else "暂无"
                ),
                "image_urls": list(summary.get("output_urls") or []),
                "error_code": summary.get("error_code"),
                "error_message": summary.get("error_message"),
                "request_id": summary.get("request_id"),
                "credential_alias": credential_alias,
                "routing_group_id": routing_group_id,
            },
            ensure_ascii=False,
        )

    def _extract_http_urls(self, payload: Any, *, max_items: int = 12) -> list[str]:
        results: list[str] = []

        def walk(node: Any) -> None:
            if len(results) >= max_items:
                return
            if isinstance(node, str):
                text = node.strip()
                if text.startswith("http://") or text.startswith("https://"):
                    if text not in results:
                        results.append(text)
                return
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
                return
            if isinstance(node, list):
                for value in node:
                    walk(value)

        walk(payload)
        return results

    def _extract_failure(self, payload: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        candidate_nodes: list[dict[str, Any]] = []
        if isinstance(payload.get("response"), dict):
            candidate_nodes.append(payload.get("response"))
        candidate_nodes.append(payload)

        for node in candidate_nodes:
            code = str(node.get("error_code") or "").strip() or None
            message = str(node.get("error_message") or "").strip() or None
            failed_reason = str(node.get("failed_reason") or "").strip() or None
            if code or message or failed_reason:
                return code, message, failed_reason
        return None, None, None

    async def _run_au_vendor_json(
        self,
        *,
        command_args: list[str],
        output_json_path: str = "",
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
        argv.extend(["--api-key-env", resolved_env, "--quiet"])

        resolved_output_json: Optional[Path] = None
        output_text = str(output_json_path or "").strip()
        if output_text:
            resolved_output_json = Path(output_text).expanduser().resolve()
            resolved_output_json.parent.mkdir(parents=True, exist_ok=True)
            argv.extend(["--output", str(resolved_output_json)])

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

        if resolved_output_json is not None and resolved_output_json.exists():
            content = resolved_output_json.read_text(encoding="utf-8").strip()
            if content:
                try:
                    payload = json.loads(content)
                    if isinstance(payload, dict):
                        return payload
                except Exception as exc:
                    raise RuntimeError(f"failed to parse JSON from output file {resolved_output_json}: {exc}") from exc

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

        if resolved_output_json is not None:
            raise RuntimeError(
                f"failed to parse JSON from output file and stdout: {resolved_output_json}"
            )
        raise RuntimeError("failed to parse JSON from au command output")

    def _build_prompt_resources(self, image_sources: list[str]) -> list[dict[str, Any]]:
        return [{"name": f"image_{idx}", "source": value} for idx, value in enumerate(image_sources, start=1)]

    async def _resolve_au_image_inputs(
        self,
        *,
        prompt_text: str,
        images: list[str],
        image_refs: list[str],
        chat_id: str,
        __request__: Optional[Request],
    ) -> dict[str, Any]:
        input_images: list[str] = []
        for value in images or []:
            text = str(value or "").strip()
            if text:
                input_images.append(text)

        prompt_image_refs = [f"%{ref}" for ref in extract_media_asset_references(prompt_text or "")]
        provided_image_refs: list[str] = []
        inferred_image_ref_inputs: list[str] = []
        for value in image_refs or []:
            text = str(value or "").strip()
            if not text:
                continue
            if text.startswith("%"):
                inferred_image_ref_inputs.append(text)
                continue
            provided_image_refs.append(text)

        seen_ref_keys: set[str] = set()
        for value in input_images:
            ref_key = self._normalize_media_ref_key(value)
            if ref_key:
                seen_ref_keys.add(ref_key)

        inferred_prompt_inputs: list[str] = []
        merged_candidate_refs: list[tuple[str, str]] = []
        for value in inferred_image_ref_inputs:
            ref_key = self._normalize_media_ref_key(value)
            if not ref_key:
                continue
            if ref_key in seen_ref_keys:
                continue
            normalized_value = f"%{ref_key}"
            merged_candidate_refs.append((normalized_value, "image_ref"))
            seen_ref_keys.add(ref_key)

        for value in prompt_image_refs:
            ref_key = self._normalize_media_ref_key(value)
            if not ref_key:
                continue
            if ref_key in seen_ref_keys:
                continue
            normalized_value = f"%{ref_key}"
            merged_candidate_refs.append((normalized_value, "prompt"))
            seen_ref_keys.add(ref_key)

        inferred_image_ref_inputs = []
        for value, source in merged_candidate_refs:
            if source == "prompt":
                inferred_prompt_inputs.append(value)
            else:
                inferred_image_ref_inputs.append(value)
            input_images.append(value)

        bridge = AUMediaReferenceBridge(
            __request__=__request__,
            request_timeout_seconds=self.valves.REQUEST_TIMEOUT_SECONDS,
            openwebui_base_url=self.valves.OPENWEBUI_BASE_URL,
            openwebui_api_key=self.valves.OPENWEBUI_API_KEY,
            chat_id=str(chat_id or "").strip(),
            status="active",
            url_expires_in=self.valves.MEDIA_URL_EXPIRES_IN_SECONDS,
        )

        image_result = await bridge.resolve_media_inputs(
            values=input_images,
            media_type="image",
            workdir=self.valves.AU_WORKDIR,
        )
        if not image_result.get("ok"):
            return image_result

        prompt_resources: list[dict[str, Any]] = []
        for item in image_result.get("prompt_resources") or []:
            if isinstance(item, dict) and str(item.get("url") or "").startswith(("http://", "https://")):
                prompt_resources.append(item)

        if not prompt_resources:
            prompt_resources = self._build_prompt_resources(list(image_result.get("resolved_values") or []))

        return {
            "ok": True,
            "images": list(image_result.get("resolved_values") or []),
            "prompt_resources": prompt_resources,
            "input_images": input_images,
            "image_refs": provided_image_refs,
            "inferred_prompt_inputs": inferred_prompt_inputs,
            "inferred_image_ref_inputs": inferred_image_ref_inputs,
        }

    def _schedule_btn_job(
        self,
        *,
        task_id: str,
        tool_name: str,
        model: str,
        chat_id: str,
        prompt_text: str,
        references: Optional[list[str]],
        prompt_resources: Optional[list[dict[str, Any]]],
        generation_params: dict[str, Any],
        command_args: list[str],
        output_json_path: str,
        api_key_env: str,
        api_key: str,
        credential_alias: Optional[str],
        routing_group_id: Optional[str],
        __request__: Optional[Request],
    ) -> bool:
        existing = self._active_jobs.get(task_id)
        if existing and not existing.done():
            return True

        async def runner() -> None:
            try:
                payload = await self._run_au_vendor_json(
                    command_args=command_args,
                    output_json_path=output_json_path,
                    timeout_seconds=self.valves.SUBPROCESS_TIMEOUT_SECONDS,
                    api_key_env=api_key_env,
                    api_key=api_key,
                )
                summary = self._summarize_btn_payload(payload, json_file=output_json_path)
                final_status = "FAILED" if bool(summary.get("has_error")) else "SUCCEEDED"

                update_params = dict(generation_params or {})
                update_params.update(
                    {
                        "json_file": output_json_path,
                        "output_images": summary.get("output_images"),
                        "saved_image_count": summary.get("saved_image_count"),
                        "saved_image_dir": summary.get("saved_image_dir"),
                        "image_files": summary.get("image_files"),
                    }
                )

                await self._bridge_upsert_task(
                    task_id=task_id,
                    tool_name=tool_name,
                    status=final_status,
                    model=model,
                    chat_id=chat_id,
                    references=references,
                    raw_submit_response={"json_file": output_json_path},
                    raw_last_response={
                        "json_file": output_json_path,
                        "output_images": summary.get("output_images"),
                        "saved_image_count": summary.get("saved_image_count"),
                        "saved_image_dir": summary.get("saved_image_dir"),
                    },
                    image_urls=list(summary.get("output_urls") or []),
                    primary_image_url=summary.get("primary_image_url"),
                    error_code=summary.get("error_code"),
                    error_message=summary.get("error_message"),
                    prompt_text=prompt_text,
                    generation_params=update_params,
                    prompt_resources=prompt_resources,
                    credential_alias=credential_alias,
                    routing_group_id=routing_group_id,
                    __request__=__request__,
                )
            except Exception as exc:
                fail_params = dict(generation_params or {})
                fail_params["json_file"] = output_json_path
                await self._bridge_upsert_task(
                    task_id=task_id,
                    tool_name=tool_name,
                    status="FAILED",
                    model=model,
                    chat_id=chat_id,
                    references=references,
                    error_code="CommandExecutionFailed",
                    error_message=str(exc),
                    prompt_text=prompt_text,
                    generation_params=fail_params,
                    prompt_resources=prompt_resources,
                    credential_alias=credential_alias,
                    routing_group_id=routing_group_id,
                    __request__=__request__,
                )
            finally:
                self._active_jobs.pop(task_id, None)

        try:
            task = asyncio.create_task(runner(), name=f"btn-image2:{task_id}")
        except RuntimeError:
            return False

        self._active_jobs[task_id] = task
        return True

    async def generate_image_with_btn_image2_gen(
        self,
        prompt: str,
        model: str = "",
        n: int = 1,
        size: str = "",
        quality: str = "",
        background: str = "",
        output_format: str = "",
        output_compression: Optional[int] = None,
        moderation: str = "",
        response_format: str = "",
        style: str = "",
        stream: Optional[bool] = None,
        partial_images: Optional[int] = None,
        user: str = "",
        chat_id: str = "",
        api_key_env: str = "",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        prompt_text = str(prompt or "").strip()
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

        credential = await self._resolve_vendor_credential(__user__=__user__)
        if not credential.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": int(credential.get("status_code") or 500),
                    "error_code": credential.get("error_code") or "KEY_ROUTING_RESOLVE_FAILED",
                    "error_message": credential.get("error_message") or credential.get("error") or "Failed to resolve key routing credential",
                    "request_id": credential.get("request_id"),
                },
                ensure_ascii=False,
            )

        user_id = str((__user__ or {}).get("id") or "").strip()
        resolved_api_key = str(credential.get("api_key") or "").strip()
        resolved_credential_alias = str(credential.get("credential_alias") or "").strip() or None
        resolved_routing_group_id = str(credential.get("routing_group_id") or "").strip() or None

        resolved_model = str(model or self.valves.DEFAULT_MODEL).strip() or self.valves.DEFAULT_MODEL
        resolved_size = str(size or self.valves.DEFAULT_SIZE).strip() or self.valves.DEFAULT_SIZE
        resolved_quality = str(quality or self.valves.DEFAULT_QUALITY).strip() or self.valves.DEFAULT_QUALITY

        task_id = f"btnimg2_{uuid.uuid4().hex[:16]}"
        artifacts = self._resolve_task_artifact_paths(task_id=task_id, user_id=user_id)
        generation_params = {
            "mode": "gen",
            "n": int(n or 1),
            "size": resolved_size,
            "quality": resolved_quality,
            "json_file": artifacts["json_file"],
            "image_output_dir": artifacts["image_output_dir"],
        }

        await self._bridge_upsert_task(
            task_id=task_id,
            tool_name=self._TOOL_NAME_GEN,
            status="QUEUED",
            model=resolved_model,
            chat_id=str(chat_id or "").strip(),
            prompt_text=prompt_text,
            raw_submit_response={"json_file": artifacts["json_file"]},
            generation_params=generation_params,
            credential_alias=resolved_credential_alias,
            routing_group_id=resolved_routing_group_id,
            __request__=__request__,
        )

        args: list[str] = [
            "btn-image2-gen",
            "--prompt",
            prompt_text,
            "--model",
            resolved_model,
            "--n",
            str(max(1, int(n or 1))),
            "--size",
            resolved_size,
            "--quality",
            resolved_quality,
        ]

        if background:
            args.extend(["--background", str(background).strip()])
        if output_format:
            args.extend(["--output-format", str(output_format).strip()])
        if output_compression is not None:
            args.extend(["--output-compression", str(int(output_compression))])
        if moderation:
            args.extend(["--moderation", str(moderation).strip()])
        if style:
            args.extend(["--style", str(style).strip()])
        if stream is not None:
            args.append("--stream" if bool(stream) else "--no-stream")
        if partial_images is not None:
            args.extend(["--partial-images", str(int(partial_images))])
        if user:
            args.extend(["--user", str(user).strip()])
        args.extend(["--save-images", "--image-output-dir", artifacts["image_output_dir"]])

        scheduled = self._schedule_btn_job(
            task_id=task_id,
            tool_name=self._TOOL_NAME_GEN,
            model=resolved_model,
            chat_id=str(chat_id or "").strip(),
            prompt_text=prompt_text,
            references=None,
            prompt_resources=None,
            generation_params=generation_params,
            command_args=args,
            output_json_path=artifacts["json_file"],
            api_key_env=api_key_env,
            api_key=resolved_api_key,
            credential_alias=resolved_credential_alias,
            routing_group_id=resolved_routing_group_id,
            __request__=__request__,
        )
        if not scheduled:
            error_summary = {
                "output_images": 0,
                "saved_image_count": 0,
                "saved_image_dir": artifacts["image_output_dir"],
                "json_file": artifacts["json_file"],
                "image_files": [],
                "output_urls": [],
                "primary_image_url": None,
                "error_code": "BackgroundTaskScheduleFailed",
                "error_message": "failed to schedule background job",
                "request_id": None,
            }
            await self._bridge_upsert_task(
                task_id=task_id,
                tool_name=self._TOOL_NAME_GEN,
                status="FAILED",
                model=resolved_model,
                chat_id=str(chat_id or "").strip(),
                error_code="BackgroundTaskScheduleFailed",
                error_message="failed to schedule background job",
                prompt_text=prompt_text,
                generation_params=generation_params,
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
                __request__=__request__,
            )
            return self._build_submit_response(
                task_id=task_id,
                status="FAILED",
                model=resolved_model,
                size=resolved_size,
                quality=resolved_quality,
                summary=error_summary,
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
            )

        return self._build_submit_response(
            task_id=task_id,
            status="QUEUED",
            model=resolved_model,
            size=resolved_size,
            quality=resolved_quality,
            summary={
                "output_images": 0,
                "saved_image_count": 0,
                "saved_image_dir": artifacts["image_output_dir"],
                "json_file": artifacts["json_file"],
                "image_files": [],
                "output_urls": [],
                "primary_image_url": None,
                "error_code": None,
                "error_message": None,
                "request_id": None,
            },
            credential_alias=resolved_credential_alias,
            routing_group_id=resolved_routing_group_id,
        )

    async def edit_image_with_btn_image2(
        self,
        prompt: str,
        images: Optional[list[str]] = None,
        image_refs: Optional[list[str]] = None,
        include_image_order_hint: Optional[bool] = None,
        mask: str = "",
        model: str = "",
        n: int = 1,
        size: str = "",
        quality: str = "",
        background: str = "",
        output_format: str = "",
        output_compression: Optional[int] = None,
        moderation: str = "",
        input_fidelity: str = "",
        stream: Optional[bool] = None,
        partial_images: Optional[int] = None,
        user: str = "",
        chat_id: str = "",
        api_key_env: str = "",
        __request__: Request = None,
        __user__: dict = None,
    ) -> str:
        prompt_text = str(prompt or "").strip()
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

        input_images = self._sanitize_list(images)
        input_image_refs = self._sanitize_list(image_refs)

        credential = await self._resolve_vendor_credential(__user__=__user__)
        if not credential.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": int(credential.get("status_code") or 500),
                    "error_code": credential.get("error_code") or "KEY_ROUTING_RESOLVE_FAILED",
                    "error_message": credential.get("error_message") or credential.get("error") or "Failed to resolve key routing credential",
                    "request_id": credential.get("request_id"),
                },
                ensure_ascii=False,
            )

        user_id = str((__user__ or {}).get("id") or "").strip()
        resolved_api_key = str(credential.get("api_key") or "").strip()
        resolved_credential_alias = str(credential.get("credential_alias") or "").strip() or None
        resolved_routing_group_id = str(credential.get("routing_group_id") or "").strip() or None

        resolved_model = str(model or self.valves.DEFAULT_MODEL).strip() or self.valves.DEFAULT_MODEL
        resolved_size = str(size or self.valves.DEFAULT_SIZE).strip() or self.valves.DEFAULT_SIZE
        resolved_quality = str(quality or self.valves.DEFAULT_QUALITY).strip() or self.valves.DEFAULT_QUALITY
        resolved_include_hint = True if include_image_order_hint is None else bool(include_image_order_hint)

        media_bridge_result = await self._resolve_au_image_inputs(
            prompt_text=prompt_text,
            images=input_images,
            image_refs=input_image_refs,
            chat_id=str(chat_id or "").strip(),
            __request__=__request__,
        )
        if not media_bridge_result.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "status_code": int(media_bridge_result.get("status_code") or 400),
                    "error_code": media_bridge_result.get("error_code") or "MissingMediaAssetReferences",
                    "error_message": media_bridge_result.get("error_message") or "Failed to resolve image references for au vendor command",
                    "missing_references": media_bridge_result.get("missing_references") or [],
                    "ambiguous_references": media_bridge_result.get("ambiguous_references") or [],
                    "available_references": media_bridge_result.get("available_references") or [],
                    "unresolved_inputs": media_bridge_result.get("unresolved_inputs") or [],
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        resolved_images = list(media_bridge_result.get("images") or [])
        resolved_prompt_resources = list(media_bridge_result.get("prompt_resources") or [])
        resolved_refs = list(media_bridge_result.get("image_refs") or [])
        submitted_input_images = list(media_bridge_result.get("input_images") or [])
        inferred_prompt_inputs = list(media_bridge_result.get("inferred_prompt_inputs") or [])
        inferred_image_ref_inputs = list(media_bridge_result.get("inferred_image_ref_inputs") or [])

        if not resolved_images:
            return json.dumps(
                {
                    "ok": False,
                    "status_code": 400,
                    "error_code": "InvalidParameter",
                    "error_message": "images is required for edit mode",
                    "request_id": None,
                },
                ensure_ascii=False,
            )

        task_id = f"btnimg2_{uuid.uuid4().hex[:16]}"
        artifacts = self._resolve_task_artifact_paths(task_id=task_id, user_id=user_id)
        prompt_resources = resolved_prompt_resources or self._build_prompt_resources(resolved_images)
        generation_params = {
            "mode": "edit",
            "n": int(n or 1),
            "size": resolved_size,
            "quality": resolved_quality,
            "include_image_order_hint": resolved_include_hint,
            "json_file": artifacts["json_file"],
            "image_output_dir": artifacts["image_output_dir"],
            "input_images": submitted_input_images,
            "input_image_refs": resolved_refs,
            "inferred_prompt_image_refs": inferred_prompt_inputs,
            "inferred_image_ref_inputs": inferred_image_ref_inputs,
        }
        await self._bridge_upsert_task(
            task_id=task_id,
            tool_name=self._TOOL_NAME_EDIT,
            status="QUEUED",
            model=resolved_model,
            chat_id=str(chat_id or "").strip(),
            references=resolved_images,
            prompt_text=prompt_text,
            raw_submit_response={"json_file": artifacts["json_file"]},
            generation_params=generation_params,
            prompt_resources=prompt_resources,
            credential_alias=resolved_credential_alias,
            routing_group_id=resolved_routing_group_id,
            __request__=__request__,
        )

        args: list[str] = [
            "btn-image2-edit",
            "--prompt",
            prompt_text,
            "--model",
            resolved_model,
            "--n",
            str(max(1, int(n or 1))),
            "--size",
            resolved_size,
            "--quality",
            resolved_quality,
            "--include-image-order-hint" if resolved_include_hint else "--no-include-image-order-hint",
        ]

        for item in resolved_images:
            args.extend(["--image", item])
        for item in resolved_refs:
            args.extend(["--image-ref", item])
        if mask:
            args.extend(["--mask", str(mask).strip()])
        if background:
            args.extend(["--background", str(background).strip()])
        if output_format:
            args.extend(["--output-format", str(output_format).strip()])
        if output_compression is not None:
            args.extend(["--output-compression", str(int(output_compression))])
        if moderation:
            args.extend(["--moderation", str(moderation).strip()])
        if input_fidelity:
            args.extend(["--input-fidelity", str(input_fidelity).strip()])
        if stream is not None:
            args.append("--stream" if bool(stream) else "--no-stream")
        if partial_images is not None:
            args.extend(["--partial-images", str(int(partial_images))])
        if user:
            args.extend(["--user", str(user).strip()])
        args.extend(["--save-images", "--image-output-dir", artifacts["image_output_dir"]])

        scheduled = self._schedule_btn_job(
            task_id=task_id,
            tool_name=self._TOOL_NAME_EDIT,
            model=resolved_model,
            chat_id=str(chat_id or "").strip(),
            prompt_text=prompt_text,
            references=resolved_images,
            prompt_resources=prompt_resources,
            generation_params=generation_params,
            command_args=args,
            output_json_path=artifacts["json_file"],
            api_key_env=api_key_env,
            api_key=resolved_api_key,
            credential_alias=resolved_credential_alias,
            routing_group_id=resolved_routing_group_id,
            __request__=__request__,
        )
        if not scheduled:
            error_summary = {
                "output_images": 0,
                "saved_image_count": 0,
                "saved_image_dir": artifacts["image_output_dir"],
                "json_file": artifacts["json_file"],
                "image_files": [],
                "output_urls": [],
                "primary_image_url": None,
                "error_code": "BackgroundTaskScheduleFailed",
                "error_message": "failed to schedule background job",
                "request_id": None,
            }
            await self._bridge_upsert_task(
                task_id=task_id,
                tool_name=self._TOOL_NAME_EDIT,
                status="FAILED",
                model=resolved_model,
                chat_id=str(chat_id or "").strip(),
                references=resolved_images,
                error_code="BackgroundTaskScheduleFailed",
                error_message="failed to schedule background job",
                prompt_text=prompt_text,
                generation_params=generation_params,
                prompt_resources=prompt_resources,
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
                __request__=__request__,
            )
            return self._build_submit_response(
                task_id=task_id,
                status="FAILED",
                model=resolved_model,
                size=resolved_size,
                quality=resolved_quality,
                summary=error_summary,
                credential_alias=resolved_credential_alias,
                routing_group_id=resolved_routing_group_id,
            )

        return self._build_submit_response(
            task_id=task_id,
            status="QUEUED",
            model=resolved_model,
            size=resolved_size,
            quality=resolved_quality,
            summary={
                "output_images": 0,
                "saved_image_count": 0,
                "saved_image_dir": artifacts["image_output_dir"],
                "json_file": artifacts["json_file"],
                "image_files": [],
                "output_urls": [],
                "primary_image_url": None,
                "error_code": None,
                "error_message": None,
                "request_id": None,
            },
            credential_alias=resolved_credential_alias,
            routing_group_id=resolved_routing_group_id,
        )

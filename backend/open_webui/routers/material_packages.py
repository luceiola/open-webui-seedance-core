from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import inspect
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from open_webui.config import CACHE_DIR
from open_webui.models.files import Files
from open_webui.models.groups import Groups
from open_webui.models.users import UserModel, Users
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()


MATERIAL_PACKAGES_DIR = CACHE_DIR / 'material_packages'
MATERIAL_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)

ARK_ENV_FILE_CANDIDATES: list[Path] = [
    Path(os.getenv('ARK_ENV_FILE', '')).expanduser().resolve() if os.getenv('ARK_ENV_FILE') else None,
    Path.cwd() / 'config' / 'happyhorse.env',
    Path.cwd() / 'config' / 'ark.dev.env',
    Path.cwd() / 'config' / 'ark.env',
    Path.cwd() / '.env',
]

KEY_ROUTING_CONFIG_PATH = (
    Path(os.getenv('KEY_ROUTING_CONFIG_FILE', '')).expanduser().resolve()
    if os.getenv('KEY_ROUTING_CONFIG_FILE')
    else (Path.cwd() / 'config' / 'key_routing.json').resolve()
)

KEY_ROUTING_ERROR_NO_GROUP = 'KEY_ROUTING_NO_GROUP'
KEY_ROUTING_ERROR_MULTI_GROUP = 'KEY_ROUTING_MULTI_GROUP'
KEY_ROUTING_ERROR_ALIAS_NOT_FOUND = 'KEY_ROUTING_ALIAS_NOT_FOUND'
KEY_ROUTING_ERROR_ENV_MISSING = 'KEY_ROUTING_ENV_MISSING'
KEY_ROUTING_ERROR_PROVIDER_NOT_CONFIGURED = 'KEY_ROUTING_PROVIDER_NOT_CONFIGURED'
KEY_ROUTING_ERROR_RESOLVE_FAILED = 'KEY_ROUTING_RESOLVE_FAILED'

_KEY_ROUTING_CACHE_PATH: Optional[Path] = None
_KEY_ROUTING_CACHE_MTIME_NS: Optional[int] = None
_KEY_ROUTING_CACHE_DATA: dict[str, Any] = {}


SUPPORTED_EXTENSIONS: dict[str, str] = {
    # image
    '.jpg': 'image',
    '.jpeg': 'image',
    '.png': 'image',
    '.webp': 'image',
    '.bmp': 'image',
    '.gif': 'image',
    # video
    '.mp4': 'video',
    '.mov': 'video',
    '.mkv': 'video',
    '.avi': 'video',
    '.webm': 'video',
    '.mpeg': 'video',
    '.mpg': 'video',
    '.m4v': 'video',
    # audio
    '.mp3': 'audio',
    '.wav': 'audio',
    '.m4a': 'audio',
    '.aac': 'audio',
    '.flac': 'audio',
    '.ogg': 'audio',
}

SKIP_DIR_NAMES: set[str] = {
    '__MACOSX',
}

SKIP_FILE_NAMES_LOWER: set[str] = {
    '.ds_store',
    'thumbs.db',
    'desktop.ini',
}


class MaterialAsset(BaseModel):
    reference_name: str
    filename: str
    relative_path: str
    media_type: str
    size_bytes: int
    mime_type: Optional[str] = None
    ark_file_id: Optional[str] = None
    ark_status: Optional[str] = None
    error: Optional[str] = None
    tos_key: Optional[str] = None
    tos_status: Optional[str] = None
    tos_error: Optional[str] = None


class MaterialPackageResponse(BaseModel):
    id: str
    asset_package_id: str
    user_id: str
    chat_id: Optional[str] = None
    zip_filename: str
    package_display_name: Optional[str] = None
    source_filename: Optional[str] = None
    source_kind: Optional[str] = None
    merged_asset_count: Optional[int] = None
    status: str
    created_at: int
    updated_at: int
    assets: list[MaterialAsset]
    unsupported_files: list[str] = []
    skipped_files: list[str] = []


class MaterialPackageAssetAddress(BaseModel):
    reference_name: str
    filename: str
    relative_path: str
    media_type: str
    tos_key: Optional[str] = None
    tos_status: Optional[str] = None
    temp_url: Optional[str] = None
    temp_url_expires_at: Optional[int] = None


class MaterialPackageAssetsResponse(BaseModel):
    asset_package_id: str
    package_display_name: Optional[str] = None
    assets: list[MaterialPackageAssetAddress]


class UploadSourceItem(BaseModel):
    upload_id: Optional[str] = None
    file_path: Optional[str] = None
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None


class CreateMaterialPackageFromUploadRequest(BaseModel):
    chat_id: Optional[str] = None
    upload_ids: list[str] = Field(default_factory=list)
    uploads: list[UploadSourceItem] = Field(default_factory=list)
    package_display_name: Optional[str] = None


class ResolveReferencesRequest(BaseModel):
    prompt: str = Field(min_length=1)


class ResolveReferencesResponse(BaseModel):
    package_id: str
    references: list[str]
    missing_references: list[str]
    available_references: list[str]
    cleaned_prompt: str
    assets: list[MaterialAsset]


class GenerateWithPackageRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str = Field(default='doubao-seed-2-0-lite-260215')
    instructions: Optional[str] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    duration: Optional[int] = Field(default=None, ge=1, le=60)
    ratio: Optional[str] = None
    watermark: Optional[bool] = None
    generate_audio: Optional[bool] = None


class GenerateWithPackageResponse(BaseModel):
    package_id: str
    references: list[str]
    response_id: Optional[str] = None
    status: Optional[str] = None
    output_text: Optional[str] = None
    raw_response: dict[str, Any]


class ArkGenerationTaskSubmitRequest(BaseModel):
    model: str = Field(min_length=1)
    content: list[dict[str, Any]] = Field(default_factory=list)
    instructions: Optional[str] = None
    duration: Optional[int] = Field(default=None, ge=1, le=60)
    ratio: Optional[str] = None
    watermark: Optional[bool] = None
    generate_audio: Optional[bool] = None


class ArkGenerationTaskProxyResponse(BaseModel):
    ok: bool = True
    provider: str = 'ark'
    credential_alias: Optional[str] = None
    routing_group_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class GenerationTaskStatusResponse(BaseModel):
    task_id: str
    status: Optional[str] = None
    raw_response: dict[str, Any]


class GenerationTaskListItem(BaseModel):
    task_id: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    package_id: Optional[str] = None
    chat_id: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    archive_status: Optional[str] = None
    archive_error: Optional[str] = None
    archive_retry_count: Optional[int] = None
    archive_updated_at: Optional[int] = None
    download_ready: bool = False
    can_delete: bool = False
    deleted_at: Optional[int] = None
    created_at: int
    updated_at: int
    references: list[str] = []
    duration: Optional[int] = None
    ratio: Optional[str] = None
    watermark: Optional[bool] = None
    generate_audio: Optional[bool] = None
    thumbnail_url: Optional[str] = None
    video_preview_url: Optional[str] = None
    video_download_url: Optional[str] = None
    video_url: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    request_id: Optional[str] = None
    credential_alias: Optional[str] = None
    routing_group_id: Optional[str] = None


class GenerationTaskUserItem(BaseModel):
    user_id: str
    user_name: str


class GenerationTaskPreviewResponse(BaseModel):
    ok: bool = True
    task_id: str
    status: Optional[str] = None
    archive_status: Optional[str] = None
    download_ready: bool = False
    can_delete: bool = False
    thumbnail_url: Optional[str] = None
    video_preview_url: Optional[str] = None


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _read_env_value_from_file(key: str) -> Optional[str]:
    for path in ARK_ENV_FILE_CANDIDATES:
        if path is None or not path.exists() or not path.is_file():
            continue

        try:
            for raw_line in path.read_text(encoding='utf-8').splitlines():
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue

                k, v = line.split('=', 1)
                if k.strip() != key:
                    continue

                value = v.strip().strip('"').strip("'")
                if value:
                    return value
        except Exception:
            continue

    return None


def _get_ark_base_url() -> str:
    base = (os.getenv('ARK_BASE_URL') or _read_env_value_from_file('ARK_BASE_URL') or 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/')
    if '/api/' not in base:
        base = f'{base}/api/v3'
    return base


def _to_int_value(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def _key_routing_error(
    *,
    code: str,
    message: str,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            'code': code,
            'message': message,
            'request_id': None,
        },
    )


def _load_key_routing_config() -> dict[str, Any]:
    global _KEY_ROUTING_CACHE_PATH, _KEY_ROUTING_CACHE_MTIME_NS, _KEY_ROUTING_CACHE_DATA

    path = KEY_ROUTING_CONFIG_PATH
    if not path.exists() or not path.is_file():
        _KEY_ROUTING_CACHE_PATH = path
        _KEY_ROUTING_CACHE_MTIME_NS = None
        _KEY_ROUTING_CACHE_DATA = {}
        return {}

    mtime_ns = _to_int_value(path.stat().st_mtime_ns, 0)
    if _KEY_ROUTING_CACHE_PATH == path and _KEY_ROUTING_CACHE_MTIME_NS == mtime_ns:
        return dict(_KEY_ROUTING_CACHE_DATA)

    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise _key_routing_error(
            code=KEY_ROUTING_ERROR_RESOLVE_FAILED,
            message=f'Failed to load key routing config: {exc}',
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not isinstance(payload, dict):
        raise _key_routing_error(
            code=KEY_ROUTING_ERROR_RESOLVE_FAILED,
            message='Invalid key routing config: root must be an object',
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    _KEY_ROUTING_CACHE_PATH = path
    _KEY_ROUTING_CACHE_MTIME_NS = mtime_ns
    _KEY_ROUTING_CACHE_DATA = payload
    return dict(payload)


def _get_provider_key_routing_config(provider: str) -> Optional[dict[str, Any]]:
    normalized = str(provider or '').strip().lower()
    if not normalized:
        return None
    config = _load_key_routing_config()
    providers = config.get('providers') if isinstance(config.get('providers'), dict) else {}
    provider_config = providers.get(normalized)
    if isinstance(provider_config, dict):
        return provider_config
    return None


def _resolve_provider_legacy_api_key(provider: str) -> Optional[str]:
    normalized = str(provider or '').strip().lower()
    if normalized in {'seedance', 'ark'}:
        return (os.getenv('ARK_API_KEY') or _read_env_value_from_file('ARK_API_KEY') or '').strip() or None
    if normalized in {'happyhorse', 'dashscope'}:
        return (os.getenv('DASHSCOPE_API_KEY') or _read_env_value_from_file('DASHSCOPE_API_KEY') or '').strip() or None
    return None


def _resolve_key_routing_alias(
    *,
    provider: str,
    provider_config: dict[str, Any],
    user_group_ids: set[str],
    user_group_names: set[str],
) -> tuple[str, Optional[str]]:
    bindings = provider_config.get('bindings') if isinstance(provider_config.get('bindings'), list) else []
    matches: list[tuple[int, str, Optional[str]]] = []
    for entry in bindings:
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get('alias') or '').strip()
        if not alias:
            continue

        group_id = str(entry.get('group_id') or '').strip()
        group_name = str(entry.get('group_name') or '').strip()
        matched = False
        if group_id and group_id in user_group_ids:
            matched = True
        if group_name and group_name in user_group_names:
            matched = True
        if not matched:
            continue

        priority = _to_int_value(entry.get('priority'), 0)
        matches.append((priority, alias, group_id or None))

    if not matches:
        default_alias = str(provider_config.get('default_alias') or '').strip()
        if default_alias:
            return default_alias, None
        raise _key_routing_error(
            code=KEY_ROUTING_ERROR_NO_GROUP,
            message=f'No key routing group matched for provider={provider}',
        )

    strict_single_group = bool(provider_config.get('strict_single_group', True))
    aliases = sorted({alias for _, alias, _ in matches})
    if strict_single_group and len(aliases) > 1:
        raise _key_routing_error(
            code=KEY_ROUTING_ERROR_MULTI_GROUP,
            message=f'Multiple key routing groups matched for provider={provider}: {",".join(aliases)}',
        )

    matches.sort(key=lambda item: (-item[0], item[1]))
    _, selected_alias, selected_group_id = matches[0]
    return selected_alias, selected_group_id


async def _resolve_provider_credential(
    *,
    provider: str,
    user_id: str,
    preferred_alias: Optional[str] = None,
) -> dict[str, Optional[str]]:
    normalized_provider = str(provider or '').strip().lower()
    if not normalized_provider:
        raise _key_routing_error(code=KEY_ROUTING_ERROR_RESOLVE_FAILED, message='Provider is required')

    preferred_alias_value = str(preferred_alias or '').strip()
    provider_config = _get_provider_key_routing_config(normalized_provider)

    if provider_config is None:
        # Backward compatibility: when routing config is absent, keep legacy env behavior.
        legacy_api_key = _resolve_provider_legacy_api_key(normalized_provider)
        if not legacy_api_key:
            raise _key_routing_error(
                code=KEY_ROUTING_ERROR_PROVIDER_NOT_CONFIGURED,
                message=f'Provider key routing is not configured for provider={normalized_provider}',
            )
        return {
            'provider': normalized_provider,
            'credential_alias': 'legacy_env',
            'routing_group_id': None,
            'api_key': legacy_api_key,
        }

    credentials = provider_config.get('credentials') if isinstance(provider_config.get('credentials'), dict) else {}
    if not credentials:
        raise _key_routing_error(
            code=KEY_ROUTING_ERROR_PROVIDER_NOT_CONFIGURED,
            message=f'Provider credentials are not configured for provider={normalized_provider}',
        )

    routing_group_id: Optional[str] = None
    if preferred_alias_value:
        selected_alias = preferred_alias_value
    else:
        groups = await Groups.get_groups_by_member_id(str(user_id))
        user_group_ids = {str(group.id) for group in groups if getattr(group, 'id', None)}
        user_group_names = {str(group.name) for group in groups if getattr(group, 'name', None)}
        selected_alias, routing_group_id = _resolve_key_routing_alias(
            provider=normalized_provider,
            provider_config=provider_config,
            user_group_ids=user_group_ids,
            user_group_names=user_group_names,
        )

    credential_conf = credentials.get(selected_alias)
    env_name = ''
    if isinstance(credential_conf, str):
        env_name = credential_conf.strip()
    elif isinstance(credential_conf, dict):
        env_name = str(credential_conf.get('env') or '').strip()

    if not env_name:
        raise _key_routing_error(
            code=KEY_ROUTING_ERROR_ALIAS_NOT_FOUND,
            message=f'Credential alias is not configured for provider={normalized_provider}: {selected_alias}',
        )

    api_key = (os.getenv(env_name) or _read_env_value_from_file(env_name) or '').strip()
    if not api_key:
        raise _key_routing_error(
            code=KEY_ROUTING_ERROR_ENV_MISSING,
            message=f'Credential env is empty for provider={normalized_provider}, alias={selected_alias}, env={env_name}',
        )

    return {
        'provider': normalized_provider,
        'credential_alias': selected_alias,
        'routing_group_id': routing_group_id,
        'api_key': api_key,
    }


def _get_ark_headers(*, api_key: Optional[str] = None) -> dict[str, str]:
    api_key = (api_key or os.getenv('ARK_API_KEY') or _read_env_value_from_file('ARK_API_KEY') or '').strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='ARK_API_KEY is not configured',
        )
    return {
        'Authorization': f'Bearer {api_key}',
    }


def _get_dashscope_base_url() -> str:
    base = (os.getenv('DASHSCOPE_BASE_URL') or _read_env_value_from_file('DASHSCOPE_BASE_URL') or 'https://dashscope.aliyuncs.com/api/v1').rstrip('/')
    if '/api/' not in base:
        base = f'{base}/api/v1'
    return base


def _get_dashscope_headers() -> dict[str, str]:
    api_key = (os.getenv('DASHSCOPE_API_KEY') or _read_env_value_from_file('DASHSCOPE_API_KEY') or '').strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='DASHSCOPE_API_KEY is not configured',
        )
    return {
        'Authorization': f'Bearer {api_key}',
    }


def _manifest_path(user_id: str, package_id: str) -> Path:
    user_dir = MATERIAL_PACKAGES_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / f'{package_id}.json'


def _tasks_dir(user_id: str) -> Path:
    tasks_dir = MATERIAL_PACKAGES_DIR / user_id / 'tasks'
    tasks_dir.mkdir(parents=True, exist_ok=True)
    return tasks_dir


def _sanitize_task_id(task_id: str) -> str:
    value = (task_id or '').strip()
    if not value or not re.fullmatch(r'[A-Za-z0-9._-]{3,128}', value):
        raise HTTPException(status_code=400, detail='Invalid task_id')
    return value


def _task_record_path(user_id: str, task_id: str) -> Path:
    safe_task_id = _sanitize_task_id(task_id)
    return _tasks_dir(user_id) / f'{safe_task_id}.json'


def _load_task_record(user_id: str, task_id: str) -> Optional[dict[str, Any]]:
    path = _task_record_path(user_id, task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _find_user_task_record_by_provider_task_id(
    *,
    user_id: str,
    provider: str,
    provider_task_id: str,
) -> Optional[tuple[str, dict[str, Any]]]:
    provider_value = str(provider or '').strip().lower()
    provider_task_value = str(provider_task_id or '').strip()
    if not provider_value or not provider_task_value:
        return None

    tasks_dir = _tasks_dir(user_id)
    if not tasks_dir.exists():
        return None

    for path in tasks_dir.glob('*.json'):
        data = _load_task_record_from_path(path)
        if data is None:
            continue

        current_provider = str(data.get('provider') or '').strip().lower()
        current_provider_task_id = str(data.get('provider_task_id') or '').strip()
        if current_provider == provider_value and current_provider_task_id == provider_task_value:
            return path.stem, data
    return None


def _save_task_record(user_id: str, task_id: str, data: dict[str, Any]) -> None:
    path = _task_record_path(user_id, task_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _extract_task_status(data: dict[str, Any]) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    nested_data = data.get('data') if isinstance(data.get('data'), dict) else {}
    nested_output = data.get('output') if isinstance(data.get('output'), dict) else {}
    nested_task = data.get('task') if isinstance(data.get('task'), dict) else {}
    return (
        data.get('status')
        or data.get('task_status')
        or nested_data.get('status')
        or nested_data.get('task_status')
        or nested_output.get('status')
        or nested_output.get('task_status')
        or nested_task.get('status')
        or nested_task.get('task_status')
    )


TERMINAL_TASK_STATUSES: set[str] = {
    'succeeded',
    'completed',
    'failed',
    'error',
    'cancelled',
    'canceled',
}

GENERATION_SKILL_SEEDANCE = 'seedance'
GENERATION_SKILL_HAPPYHORSE = 'happyhorse'
GENERATION_SKILL_UNKNOWN = 'unknown'

TASK_ARTIFACT_KIND_VIDEO = 'video'
TASK_ARTIFACT_KIND_IMAGE = 'image'
TASK_ARTIFACT_KINDS: set[str] = {
    TASK_ARTIFACT_KIND_VIDEO,
    TASK_ARTIFACT_KIND_IMAGE,
}

ARCHIVE_STATUS_NOT_REQUIRED = 'NOT_REQUIRED'
ARCHIVE_STATUS_PENDING = 'PENDING'
ARCHIVE_STATUS_RUNNING = 'RUNNING'
ARCHIVE_STATUS_SUCCEEDED = 'SUCCEEDED'
ARCHIVE_STATUS_FAILED = 'FAILED'

TASK_ARCHIVE_FINAL_STATUSES: set[str] = {
    ARCHIVE_STATUS_SUCCEEDED,
    ARCHIVE_STATUS_FAILED,
}

TASK_SOFT_DELETE_RETENTION_DAYS = max(1, _get_int_env('TASK_SOFT_DELETE_RETENTION_DAYS', 7))
TASK_ARCHIVE_MAX_RETRIES = max(0, _get_int_env('TASK_ARCHIVE_MAX_RETRIES', 3))
TASK_ARCHIVE_POLL_INTERVAL_SECONDS = max(2, _get_int_env('TASK_ARCHIVE_POLL_INTERVAL_SECONDS', 8))
TASK_ARCHIVE_POLL_MAX_SECONDS = max(30, _get_int_env('TASK_ARCHIVE_POLL_MAX_SECONDS', 1800))

_LAST_SOFT_DELETE_CLEANUP_AT = 0
_ACTIVE_ARCHIVE_POLLERS: dict[str, asyncio.Task] = {}


def _normalize_task_status(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    value = str(status).strip()
    if not value:
        return None
    return value.upper()


def _is_succeeded_task_status(status: Optional[str]) -> bool:
    return str(status or '').strip().lower() in {'succeeded', 'completed', 'success'}


def _user_root_dir(user_id: str) -> Path:
    path = MATERIAL_PACKAGES_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _task_archives_dir(user_id: str) -> Path:
    path = _user_root_dir(user_id) / 'task_archives'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _task_thumbnails_dir(user_id: str) -> Path:
    path = _user_root_dir(user_id) / 'task_thumbnails'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_resolve_under(base_dir: Path, relative_path: str) -> Optional[Path]:
    rel = Path(str(relative_path or '')).as_posix().lstrip('/')
    if not rel or _is_unsafe_zip_path(rel):
        return None
    target = (base_dir / rel).resolve()
    base_resolved = base_dir.resolve()
    base_with_sep = f'{base_resolved}{os.sep}'
    if str(target) != str(base_resolved) and not str(target).startswith(base_with_sep):
        return None
    return target


def _task_file_from_relative(user_id: str, relative_path: Optional[str]) -> Optional[Path]:
    if not relative_path:
        return None
    base_dir = _user_root_dir(user_id)
    target = _safe_resolve_under(base_dir, str(relative_path))
    if not target or not target.is_file():
        return None
    return target


def _build_task_download_url(task_id: str) -> str:
    return f'/api/v1/material-packages/tasks/{task_id}/download'


def _build_task_preview_url(task_id: str) -> str:
    return f'/api/v1/material-packages/tasks/{task_id}/video'


def _build_task_thumbnail_url(task_id: str) -> str:
    return f'/api/v1/material-packages/tasks/{task_id}/thumbnail'


def _archive_video_relpath(task_id: str, ext: str) -> str:
    safe_ext = ext if ext.startswith('.') else f'.{ext}'
    return f'task_archives/{task_id}{safe_ext.lower()}'


def _archive_thumb_relpath(task_id: str) -> str:
    return f'task_thumbnails/{task_id}.jpg'


def _guess_video_extension(video_url: Optional[str]) -> str:
    if not video_url:
        return '.mp4'
    try:
        suffix = Path(urlparse(video_url).path).suffix.lower()
    except Exception:
        suffix = ''
    if suffix in {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}:
        return suffix
    return '.mp4'


def _load_task_record_from_path(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _iter_task_record_paths() -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    if not MATERIAL_PACKAGES_DIR.exists():
        return rows

    for user_dir in MATERIAL_PACKAGES_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        tasks_dir = user_dir / 'tasks'
        if not tasks_dir.exists() or not tasks_dir.is_dir():
            continue
        for path in tasks_dir.glob('*.json'):
            rows.append((user_dir.name, path))

    rows.sort(key=lambda row: row[1].stat().st_mtime, reverse=True)
    return rows


def _find_task_record_owner(task_id: str) -> Optional[tuple[str, dict[str, Any], Path]]:
    safe_task_id = _sanitize_task_id(task_id)
    for owner_user_id, path in _iter_task_record_paths():
        if path.stem != safe_task_id:
            continue
        data = _load_task_record_from_path(path)
        if data is None:
            continue
        return owner_user_id, data, path
    return None


def _task_delete_allowed(current_user: UserModel, owner_user_id: str) -> bool:
    return current_user.role == 'admin' or str(current_user.id) == str(owner_user_id)


def _sync_task_serving_fields(user_id: str, task_record: dict[str, Any]) -> bool:
    changed = False
    task_id = str(task_record.get('task_id') or '').strip()
    if not task_id:
        return changed

    artifact_kind = str(task_record.get('artifact_kind') or '').strip().lower()
    if artifact_kind == TASK_ARTIFACT_KIND_IMAGE:
        image_urls = task_record.get('image_urls')
        image_candidates = image_urls if isinstance(image_urls, list) else []
        image_candidates = [str(v or '').strip() for v in image_candidates if str(v or '').strip()]
        primary_image_url = str(task_record.get('primary_image_url') or '').strip()
        if not primary_image_url and image_candidates:
            primary_image_url = image_candidates[0]
            task_record['primary_image_url'] = primary_image_url
            changed = True

        if task_record.get('download_ready') != False:
            task_record['download_ready'] = False
            changed = True

        if task_record.get('video_download_url') is not None:
            task_record['video_download_url'] = None
            changed = True

        if task_record.get('video_preview_url') is not None:
            task_record['video_preview_url'] = None
            changed = True

        next_thumb_url = primary_image_url or None
        if task_record.get('thumbnail_url') != next_thumb_url:
            task_record['thumbnail_url'] = next_thumb_url
            changed = True

        return changed

    video_path = _task_file_from_relative(user_id, task_record.get('archived_video_path'))
    thumb_path = _task_file_from_relative(user_id, task_record.get('thumbnail_path'))

    archive_status = str(task_record.get('archive_status') or '').strip().upper() or ARCHIVE_STATUS_NOT_REQUIRED
    if video_path is not None and archive_status != ARCHIVE_STATUS_SUCCEEDED:
        # Self-heal legacy/inconsistent records: if archived file exists, treat archive as succeeded.
        task_record['archive_status'] = ARCHIVE_STATUS_SUCCEEDED
        task_record['archive_error'] = None
        archive_status = ARCHIVE_STATUS_SUCCEEDED
        changed = True

    download_ready = archive_status == ARCHIVE_STATUS_SUCCEEDED and video_path is not None
    if task_record.get('download_ready') != download_ready:
        task_record['download_ready'] = download_ready
        changed = True

    next_download_url = _build_task_download_url(task_id) if download_ready else None
    if task_record.get('video_download_url') != next_download_url:
        task_record['video_download_url'] = next_download_url
        changed = True

    next_preview_url = _build_task_preview_url(task_id) if download_ready else None
    if task_record.get('video_preview_url') != next_preview_url:
        task_record['video_preview_url'] = next_preview_url
        changed = True

    next_thumb_url = _build_task_thumbnail_url(task_id) if thumb_path else None
    if task_record.get('thumbnail_url') != next_thumb_url:
        task_record['thumbnail_url'] = next_thumb_url
        changed = True

    return changed


def _normalize_task_defaults(task_record: dict[str, Any], *, owner_user_id: str) -> bool:
    changed = False

    task_id = str(task_record.get('task_id') or '').strip()
    if not task_id:
        return changed

    if str(task_record.get('user_id') or '') != str(owner_user_id):
        task_record['user_id'] = str(owner_user_id)
        changed = True

    status_value = _normalize_task_status(task_record.get('status'))
    if task_record.get('status') != status_value:
        task_record['status'] = status_value
        changed = True

    model_value = str(task_record.get('model') or '').strip()
    skill_value = str(task_record.get('skill_name') or '').strip().lower()
    inferred_skill = _generation_skill_from_model(model_value)
    if not skill_value or skill_value == GENERATION_SKILL_UNKNOWN:
        task_record['skill_name'] = inferred_skill
        changed = True

    provider_value = str(task_record.get('provider') or '').strip().lower()
    if not provider_value:
        task_record['provider'] = 'ark'
        changed = True

    provider_task_id = str(task_record.get('provider_task_id') or '').strip()
    if not provider_task_id:
        task_record['provider_task_id'] = task_id
        changed = True

    tool_name = str(task_record.get('tool_name') or '').strip()
    if not tool_name:
        task_record['tool_name'] = 'material_packages.generate'
        changed = True

    if 'credential_alias' not in task_record:
        task_record['credential_alias'] = None
        changed = True
    if 'routing_group_id' not in task_record:
        task_record['routing_group_id'] = None
        changed = True

    artifact_kind = str(task_record.get('artifact_kind') or '').strip().lower()
    if artifact_kind not in TASK_ARTIFACT_KINDS:
        inferred_artifact_kind = TASK_ARTIFACT_KIND_VIDEO
        if task_record.get('primary_image_url') or task_record.get('image_urls'):
            inferred_artifact_kind = TASK_ARTIFACT_KIND_IMAGE
        task_record['artifact_kind'] = inferred_artifact_kind
        artifact_kind = inferred_artifact_kind
        changed = True

    if task_record.get('progress') is None:
        task_record['progress'] = None
        changed = True

    image_urls = task_record.get('image_urls')
    if image_urls is None:
        task_record['image_urls'] = []
        image_urls = task_record['image_urls']
        changed = True
    elif not isinstance(image_urls, list):
        task_record['image_urls'] = [str(image_urls)]
        image_urls = task_record['image_urls']
        changed = True

    normalized_image_urls: list[str] = []
    for value in image_urls:
        candidate = str(value or '').strip()
        if candidate.startswith(('http://', 'https://')):
            normalized_image_urls.append(candidate)
    deduped_image_urls = list(dict.fromkeys(normalized_image_urls))
    if deduped_image_urls != image_urls:
        task_record['image_urls'] = deduped_image_urls
        changed = True

    primary_image_url = str(task_record.get('primary_image_url') or '').strip()
    if not primary_image_url and deduped_image_urls:
        task_record['primary_image_url'] = deduped_image_urls[0]
        changed = True
    elif primary_image_url and not primary_image_url.startswith(('http://', 'https://')):
        task_record['primary_image_url'] = deduped_image_urls[0] if deduped_image_urls else None
        changed = True

    if _is_succeeded_task_status(status_value):
        if artifact_kind == TASK_ARTIFACT_KIND_IMAGE:
            if task_record.get('archive_status') != ARCHIVE_STATUS_NOT_REQUIRED:
                task_record['archive_status'] = ARCHIVE_STATUS_NOT_REQUIRED
                changed = True
        else:
            desired_archive = str(task_record.get('archive_status') or '').strip().upper()
            if not desired_archive:
                task_record['archive_status'] = ARCHIVE_STATUS_PENDING
                changed = True
    else:
        if str(task_record.get('archive_status') or '').strip().upper() not in TASK_ARCHIVE_FINAL_STATUSES:
            if task_record.get('archive_status') != ARCHIVE_STATUS_NOT_REQUIRED:
                task_record['archive_status'] = ARCHIVE_STATUS_NOT_REQUIRED
                changed = True

    if 'archive_retry_count' not in task_record:
        task_record['archive_retry_count'] = 0
        changed = True
    if 'archive_error' not in task_record:
        task_record['archive_error'] = None
        changed = True
    if 'archive_updated_at' not in task_record:
        task_record['archive_updated_at'] = int(task_record.get('updated_at') or int(time.time()))
        changed = True
    if 'deleted_at' not in task_record:
        task_record['deleted_at'] = None
        changed = True
    if 'deleted_by' not in task_record:
        task_record['deleted_by'] = None
        changed = True
    if 'delete_reason' not in task_record:
        task_record['delete_reason'] = None
        changed = True

    finished_at = int(task_record.get('finished_at') or 0)
    if _is_terminal_task_status(status_value):
        if finished_at <= 0:
            task_record['finished_at'] = int(task_record.get('updated_at') or int(time.time()))
            changed = True
    elif finished_at > 0:
        task_record['finished_at'] = None
        changed = True

    if _sync_task_serving_fields(owner_user_id, task_record):
        changed = True

    return changed


def _is_soft_deleted(task_record: dict[str, Any]) -> bool:
    try:
        return int(task_record.get('deleted_at') or 0) > 0
    except Exception:
        return False


def _cleanup_expired_soft_deleted_records() -> None:
    global _LAST_SOFT_DELETE_CLEANUP_AT
    now = int(time.time())
    if now - _LAST_SOFT_DELETE_CLEANUP_AT < 300:
        return
    _LAST_SOFT_DELETE_CLEANUP_AT = now

    cutoff = now - TASK_SOFT_DELETE_RETENTION_DAYS * 86400
    for owner_user_id, path in _iter_task_record_paths():
        data = _load_task_record_from_path(path)
        if data is None:
            continue
        deleted_at = int(data.get('deleted_at') or 0)
        if deleted_at <= 0 or deleted_at > cutoff:
            continue

        for key in ('archived_video_path', 'thumbnail_path'):
            file_path = _task_file_from_relative(owner_user_id, data.get(key))
            if file_path and file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass

        try:
            path.unlink()
        except Exception:
            pass


def _is_terminal_task_status(status: Optional[str]) -> bool:
    return str(status or '').strip().lower() in TERMINAL_TASK_STATUSES


def _should_refresh_task_status(task_record: dict[str, Any], min_interval_seconds: int) -> bool:
    status_value = str(task_record.get('status') or '').strip().lower()
    if _is_terminal_task_status(status_value):
        return False

    now = int(time.time())
    updated_at = int(task_record.get('updated_at') or 0)
    return (now - updated_at) >= max(0, int(min_interval_seconds))


async def _submit_generation_task_to_ark(
    payload: dict[str, Any],
    *,
    timeout_seconds: int = 180,
    api_key: Optional[str] = None,
) -> dict[str, Any]:
    base_url = _get_ark_base_url()
    headers = _get_ark_headers(api_key=api_key)

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(f'{base_url}/contents/generations/tasks', headers=headers, json=payload)
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f'Ark tasks.create failed: {resp.text}',
        )
    return resp.json()


async def _query_generation_task_from_ark(
    task_id: str,
    *,
    timeout_seconds: int = 120,
    api_key: Optional[str] = None,
) -> dict[str, Any]:
    base_url = _get_ark_base_url()
    headers = _get_ark_headers(api_key=api_key)

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.get(f'{base_url}/contents/generations/tasks/{task_id}', headers=headers)

        if resp.status_code >= 400:
            fallback_resp = await client.post(
                f'{base_url}/contents/generations/tasks/query',
                headers=headers,
                json={'task_id': task_id},
            )
            if fallback_resp.status_code >= 400:
                raise HTTPException(
                    status_code=fallback_resp.status_code,
                    detail=f'Ark task query failed: {fallback_resp.text}',
                )
            return fallback_resp.json()

        return resp.json()


async def _query_generation_task_from_happyhorse(task_id: str, timeout_seconds: int = 120) -> dict[str, Any]:
    base_url = _get_dashscope_base_url()
    headers = _get_dashscope_headers()

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.get(f'{base_url}/tasks/{task_id}', headers=headers)
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f'HappyHorse task query failed: {resp.text}',
            )
        return resp.json()


def _query_generation_task_from_gpt_image2_local(user_id: str, task_id: str) -> dict[str, Any]:
    raw_store_dir = os.getenv('GPT_IMAGE2_TASK_STORE_DIR') or '.data-dev/gpt_image2_tasks'
    store_dir = Path(raw_store_dir).expanduser()
    safe_task_id = _sanitize_task_id(task_id)
    task_file = store_dir / str(user_id) / f'{safe_task_id}.json'
    if not task_file.exists() or not task_file.is_file():
        raise HTTPException(status_code=404, detail='GPT-Image-2 local task not found')
    try:
        return json.loads(task_file.read_text(encoding='utf-8'))
    except Exception:
        raise HTTPException(status_code=500, detail='Failed to read GPT-Image-2 local task record')


async def _refresh_task_record_from_ark(
    user_id: str,
    task_record: dict[str, Any],
    *,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    task_id = str(task_record.get('task_id') or '').strip()
    if not task_id:
        return task_record

    provider = str(task_record.get('provider') or 'ark').strip().lower() or 'ark'
    provider_task_id = str(task_record.get('provider_task_id') or task_id).strip() or task_id
    credential_alias = str(task_record.get('credential_alias') or '').strip() or None
    routing_group_id = str(task_record.get('routing_group_id') or '').strip() or None

    try:
        if provider == 'happyhorse':
            raw = await _query_generation_task_from_happyhorse(provider_task_id, timeout_seconds=timeout_seconds)
            artifact_kind = TASK_ARTIFACT_KIND_VIDEO
            image_urls = None
            primary_image_url = None
        elif provider in {'ark', ''}:
            resolved = await _resolve_provider_credential(
                provider='seedance',
                user_id=str(user_id),
                preferred_alias=credential_alias,
            )
            raw = await _query_generation_task_from_ark(
                provider_task_id,
                timeout_seconds=timeout_seconds,
                api_key=str(resolved.get('api_key') or ''),
            )
            credential_alias = str(resolved.get('credential_alias') or '').strip() or credential_alias
            routing_group_id = str(resolved.get('routing_group_id') or '').strip() or routing_group_id
            artifact_kind = TASK_ARTIFACT_KIND_VIDEO
            image_urls = None
            primary_image_url = None
        elif provider in {'openai_image2', 'gpt_image2'}:
            raw = _query_generation_task_from_gpt_image2_local(user_id, provider_task_id)
            extracted = _extract_image_urls(raw)
            artifact_kind = TASK_ARTIFACT_KIND_IMAGE
            image_urls = extracted
            primary_image_url = extracted[0] if extracted else None
        else:
            return task_record
    except HTTPException as exc:
        log.warning('Refresh task status failed for %s(%s): %s', task_id, provider, exc.detail)
        return task_record
    except Exception:
        log.exception('Refresh task status crashed for %s(%s)', task_id, provider)
        return task_record

    refreshed = _touch_task_record(
        user_id,
        task_id,
        status=_extract_task_status(raw),
        artifact_kind=artifact_kind,
        image_urls=image_urls,
        primary_image_url=primary_image_url,
        credential_alias=credential_alias,
        routing_group_id=routing_group_id,
        raw_last_response=raw,
    )
    return refreshed


def _extract_request_id_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'Request id:\s*([A-Za-z0-9_-]+)', text, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _find_first_video_url(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        for key in ('video_url', 'output_video_url', 'result_url'):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(('http://', 'https://')):
                return value

        for key in ('url', 'download_url'):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(('http://', 'https://')) and '.mp4' in value.lower():
                return value

        for value in payload.values():
            found = _find_first_video_url(value)
            if found:
                return found
        return None

    if isinstance(payload, list):
        for item in payload:
            found = _find_first_video_url(item)
            if found:
                return found
    return None


def _extract_image_urls(payload: Any) -> list[str]:
    collected: list[str] = []

    def _collect(node: Any) -> None:
        if isinstance(node, dict):
            for key in ('image_url', 'url'):
                value = str(node.get(key) or '').strip()
                if value.startswith(('http://', 'https://')):
                    collected.append(value)

            for key in ('image_urls', 'urls'):
                values = node.get(key)
                if isinstance(values, list):
                    for value in values:
                        candidate = str(value or '').strip()
                        if candidate.startswith(('http://', 'https://')):
                            collected.append(candidate)

            for value in node.values():
                _collect(value)
            return

        if isinstance(node, list):
            for item in node:
                _collect(item)

    _collect(payload)
    return list(dict.fromkeys(collected))


def _extract_error_info(payload: Any) -> dict[str, Optional[str]]:
    if not isinstance(payload, dict):
        return {
            'error_code': None,
            'error_message': None,
            'request_id': None,
        }

    request_id: Optional[str] = payload.get('request_id')
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    for candidate in (
        payload.get('error'),
        (payload.get('data') or {}).get('error') if isinstance(payload.get('data'), dict) else None,
        payload.get('output') if isinstance(payload.get('output'), dict) else None,
    ):
        if isinstance(candidate, dict):
            error_code = error_code or candidate.get('code')
            error_message = error_message or candidate.get('message')
            request_id = request_id or candidate.get('request_id')
        elif isinstance(candidate, str):
            error_message = error_message or candidate

    if not error_message and isinstance(payload.get('message'), str):
        error_message = payload.get('message')

    request_id = request_id or _extract_request_id_from_text(error_message or '')
    return {
        'error_code': error_code,
        'error_message': error_message,
        'request_id': request_id,
    }


async def _download_video_file(video_url: str, dest_path: Path, *, timeout_seconds: int = 300) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + '.part')

    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        async with client.stream('GET', video_url) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f'Video download failed: HTTP {resp.status_code}')
            with temp_path.open('wb') as f:
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

    if not temp_path.exists() or temp_path.stat().st_size <= 0:
        raise RuntimeError('Downloaded video file is empty')
    temp_path.replace(dest_path)


def _generate_video_thumbnail(video_path: Path, thumbnail_path: Path) -> bool:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return False

    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                ffmpeg,
                '-y',
                '-ss',
                '00:00:01.000',
                '-i',
                str(video_path),
                '-vframes',
                '1',
                '-vf',
                'scale=540:-1:force_original_aspect_ratio=decrease',
                str(thumbnail_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return False
    return thumbnail_path.exists() and thumbnail_path.stat().st_size > 0


async def _archive_task_record_if_needed(
    owner_user_id: str,
    task_record: dict[str, Any],
    *,
    force_retry: bool = False,
) -> dict[str, Any]:
    task_id = str(task_record.get('task_id') or '').strip()
    if not task_id:
        return task_record

    now = int(time.time())
    changed = False
    status_value = _normalize_task_status(task_record.get('status'))
    if task_record.get('status') != status_value:
        task_record['status'] = status_value
        changed = True

    if str(task_record.get('artifact_kind') or '').strip().lower() == TASK_ARTIFACT_KIND_IMAGE:
        if str(task_record.get('archive_status') or '').strip().upper() != ARCHIVE_STATUS_NOT_REQUIRED:
            task_record['archive_status'] = ARCHIVE_STATUS_NOT_REQUIRED
            changed = True
        if _sync_task_serving_fields(owner_user_id, task_record):
            changed = True
        if changed:
            task_record['archive_updated_at'] = now
            _save_task_record(owner_user_id, task_id, task_record)
        return task_record

    if not _is_succeeded_task_status(status_value):
        if str(task_record.get('archive_status') or '').strip().upper() not in TASK_ARCHIVE_FINAL_STATUSES:
            if task_record.get('archive_status') != ARCHIVE_STATUS_NOT_REQUIRED:
                task_record['archive_status'] = ARCHIVE_STATUS_NOT_REQUIRED
                changed = True
        if _sync_task_serving_fields(owner_user_id, task_record):
            changed = True
        if changed:
            task_record['archive_updated_at'] = now
            _save_task_record(owner_user_id, task_id, task_record)
        return task_record

    archive_status = str(task_record.get('archive_status') or '').strip().upper() or ARCHIVE_STATUS_PENDING
    if archive_status == ARCHIVE_STATUS_SUCCEEDED:
        if _sync_task_serving_fields(owner_user_id, task_record):
            _save_task_record(owner_user_id, task_id, task_record)
        return task_record

    retry_count = int(task_record.get('archive_retry_count') or 0)
    if archive_status == ARCHIVE_STATUS_FAILED and retry_count >= TASK_ARCHIVE_MAX_RETRIES and not force_retry:
        return task_record

    video_url = str(task_record.get('video_url') or '').strip()
    if not video_url:
        video_url = _find_first_video_url(task_record.get('raw_last_response') or {}) or ''
        if video_url:
            task_record['video_url'] = video_url
            changed = True

    if not video_url:
        task_record['archive_status'] = ARCHIVE_STATUS_FAILED
        task_record['archive_error'] = 'No video_url found in task response'
        task_record['archive_retry_count'] = retry_count + 1
        task_record['archive_updated_at'] = now
        _sync_task_serving_fields(owner_user_id, task_record)
        _save_task_record(owner_user_id, task_id, task_record)
        return task_record

    task_record['archive_status'] = ARCHIVE_STATUS_RUNNING
    task_record['archive_error'] = None
    task_record['archive_updated_at'] = now
    task_record['archive_retry_count'] = retry_count + 1
    _save_task_record(owner_user_id, task_id, task_record)

    ext = _guess_video_extension(video_url)
    video_relpath = _archive_video_relpath(task_id, ext)
    video_path = _safe_resolve_under(_user_root_dir(owner_user_id), video_relpath)
    if not video_path:
        task_record['archive_status'] = ARCHIVE_STATUS_FAILED
        task_record['archive_error'] = 'Invalid archive path'
        task_record['archive_updated_at'] = int(time.time())
        _save_task_record(owner_user_id, task_id, task_record)
        return task_record

    try:
        await _download_video_file(video_url, video_path)
    except Exception as e:
        task_record['archive_status'] = ARCHIVE_STATUS_FAILED
        task_record['archive_error'] = str(e)
        task_record['archive_updated_at'] = int(time.time())
        _sync_task_serving_fields(owner_user_id, task_record)
        _save_task_record(owner_user_id, task_id, task_record)
        return task_record

    task_record['archived_video_path'] = video_relpath
    task_record['archive_status'] = ARCHIVE_STATUS_SUCCEEDED
    task_record['archive_error'] = None
    task_record['archive_updated_at'] = int(time.time())

    thumb_relpath = _archive_thumb_relpath(task_id)
    thumb_path = _safe_resolve_under(_user_root_dir(owner_user_id), thumb_relpath)
    if thumb_path and _generate_video_thumbnail(video_path, thumb_path):
        task_record['thumbnail_path'] = thumb_relpath

    _sync_task_serving_fields(owner_user_id, task_record)
    _save_task_record(owner_user_id, task_id, task_record)
    return task_record


def _archive_poller_key(owner_user_id: str, task_id: str) -> str:
    return f'{owner_user_id}:{task_id}'


def _spawn_task_archive_poller(owner_user_id: str, task_id: str) -> None:
    key = _archive_poller_key(owner_user_id, task_id)
    existing = _ACTIVE_ARCHIVE_POLLERS.get(key)
    if existing and not existing.done():
        return

    async def _runner() -> None:
        started_at = int(time.time())
        while int(time.time()) - started_at <= TASK_ARCHIVE_POLL_MAX_SECONDS:
            record = _load_task_record(owner_user_id, task_id)
            if not record:
                return

            if _is_soft_deleted(record):
                return

            if _should_refresh_task_status(record, TASK_ARCHIVE_POLL_INTERVAL_SECONDS):
                record = await _refresh_task_record_from_ark(owner_user_id, record, timeout_seconds=120)

            record = await _archive_task_record_if_needed(owner_user_id, record)

            status_value = _normalize_task_status(record.get('status'))
            archive_status = str(record.get('archive_status') or '').strip().upper()
            if _is_terminal_task_status(status_value) and (
                not _is_succeeded_task_status(status_value) or archive_status in TASK_ARCHIVE_FINAL_STATUSES
            ):
                return

            await asyncio.sleep(TASK_ARCHIVE_POLL_INTERVAL_SECONDS)

    task = asyncio.create_task(_runner(), name=f'task-archive-poller:{key}')
    _ACTIVE_ARCHIVE_POLLERS[key] = task

    def _cleanup(_task: asyncio.Task) -> None:
        _ACTIVE_ARCHIVE_POLLERS.pop(key, None)

    task.add_done_callback(_cleanup)


def _touch_task_record(
    user_id: str,
    task_id: str,
    *,
    user_name: Optional[str] = None,
    provider: Optional[str] = None,
    provider_task_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    skill_name: Optional[str] = None,
    package_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    model: Optional[str] = None,
    references: Optional[list[str]] = None,
    progress: Optional[float] = None,
    finished_at: Optional[int] = None,
    duration: Optional[int] = None,
    ratio: Optional[str] = None,
    watermark: Optional[bool] = None,
    generate_audio: Optional[bool] = None,
    status: Optional[str] = None,
    artifact_kind: Optional[str] = None,
    image_urls: Optional[list[str]] = None,
    primary_image_url: Optional[str] = None,
    video_url: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    request_id: Optional[str] = None,
    credential_alias: Optional[str] = None,
    routing_group_id: Optional[str] = None,
    raw_submit_response: Optional[dict[str, Any]] = None,
    raw_last_response: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    now = int(time.time())
    current = _load_task_record(user_id, task_id) or {
        'task_id': task_id,
        'user_id': user_id,
        'created_at': now,
        'updated_at': now,
        'references': [],
    }

    if package_id is not None:
        current['package_id'] = package_id
    if provider is not None:
        current['provider'] = str(provider).strip().lower() or 'ark'
    if provider_task_id is not None:
        current['provider_task_id'] = str(provider_task_id).strip() or task_id
    if tool_name is not None:
        current['tool_name'] = str(tool_name).strip() or 'material_packages.generate'
    if skill_name is not None:
        current['skill_name'] = str(skill_name).strip().lower() or GENERATION_SKILL_UNKNOWN
    if chat_id is not None:
        current['chat_id'] = chat_id
    if user_name is not None:
        current['user_name'] = user_name
    if model is not None:
        current['model'] = model
    if references is not None:
        current['references'] = list(references)
    if progress is not None:
        try:
            current['progress'] = float(progress)
        except Exception:
            pass
    if finished_at is not None:
        try:
            current['finished_at'] = int(finished_at)
        except Exception:
            pass
    if duration is not None:
        current['duration'] = duration
    if ratio is not None:
        current['ratio'] = ratio
    if watermark is not None:
        current['watermark'] = watermark
    if generate_audio is not None:
        current['generate_audio'] = generate_audio
    if status is not None:
        current['status'] = _normalize_task_status(status)
    if artifact_kind is not None:
        current['artifact_kind'] = str(artifact_kind).strip().lower()
    if image_urls is not None:
        current['image_urls'] = list(image_urls)
    if primary_image_url is not None:
        current['primary_image_url'] = str(primary_image_url).strip() or None
    if video_url is not None:
        current['video_url'] = str(video_url).strip() or None
    if error_code is not None:
        current['error_code'] = str(error_code).strip() or None
    if error_message is not None:
        current['error_message'] = str(error_message).strip() or None
    if request_id is not None:
        current['request_id'] = str(request_id).strip() or None
    if credential_alias is not None:
        current['credential_alias'] = str(credential_alias).strip() or None
    if routing_group_id is not None:
        current['routing_group_id'] = str(routing_group_id).strip() or None
    if raw_submit_response is not None:
        current['raw_submit_response'] = raw_submit_response
    if raw_last_response is not None:
        current['raw_last_response'] = raw_last_response
        parsed_status = _extract_task_status(raw_last_response)
        if parsed_status:
            current['status'] = _normalize_task_status(parsed_status)

        parsed_progress = _extract_progress_from_payload(raw_last_response)
        if parsed_progress is not None:
            current['progress'] = parsed_progress

        video_url = _find_first_video_url(raw_last_response)
        if video_url:
            current['video_url'] = video_url

        image_urls_from_payload = _extract_image_urls(raw_last_response)
        if image_urls_from_payload:
            current['image_urls'] = image_urls_from_payload
            current['primary_image_url'] = image_urls_from_payload[0]
            if str(current.get('artifact_kind') or '').strip().lower() not in TASK_ARTIFACT_KINDS:
                current['artifact_kind'] = TASK_ARTIFACT_KIND_IMAGE

        err = _extract_error_info(raw_last_response)
        if err.get('error_code'):
            current['error_code'] = err.get('error_code')
        if err.get('error_message'):
            current['error_message'] = err.get('error_message')
        if err.get('request_id'):
            current['request_id'] = err.get('request_id')

    if _normalize_task_defaults(current, owner_user_id=str(user_id)):
        current['updated_at'] = now
    _sync_task_serving_fields(str(user_id), current)
    current['updated_at'] = now
    _save_task_record(user_id, task_id, current)
    return current


def _package_asset_dir(user_id: str, package_id: str) -> Path:
    return MATERIAL_PACKAGES_DIR / user_id / package_id / 'assets'


def _asset_storage_path(user_id: str, package_id: str, relative_path: str, ensure_parent: bool) -> Path:
    rel_path = Path(relative_path)
    if _is_unsafe_zip_path(rel_path.as_posix()):
        raise HTTPException(status_code=400, detail=f'Unsafe asset path: {relative_path}')

    base_dir = _package_asset_dir(user_id, package_id)
    target_path = (base_dir / rel_path).resolve()
    base_resolved = base_dir.resolve()
    base_with_sep = f'{base_resolved}{os.sep}'
    if str(target_path) != str(base_resolved) and not str(target_path).startswith(base_with_sep):
        raise HTTPException(status_code=400, detail=f'Unsafe asset path: {relative_path}')

    if ensure_parent:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    return target_path


def _asset_file_path_from_manifest_entry(user_id: str, package_id: str, asset: dict[str, Any]) -> Optional[Path]:
    rel = asset.get('stored_relative_path') or asset.get('relative_path')
    if not rel:
        return None
    try:
        path = _asset_storage_path(user_id, package_id, str(rel), ensure_parent=False)
    except HTTPException:
        return None
    return path if path.is_file() else None


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def _get_tos_config() -> Optional[dict[str, Any]]:
    enabled_raw = (os.getenv('MATERIAL_PACK_TOS_ENABLED') or _read_env_value_from_file('MATERIAL_PACK_TOS_ENABLED') or '').strip()
    enabled = _is_truthy(enabled_raw) if enabled_raw else False

    access_key = (os.getenv('TOS_ACCESS_KEY') or _read_env_value_from_file('TOS_ACCESS_KEY') or '').strip()
    secret_key = (os.getenv('TOS_SECRET_KEY') or _read_env_value_from_file('TOS_SECRET_KEY') or '').strip()
    endpoint = (os.getenv('TOS_ENDPOINT') or _read_env_value_from_file('TOS_ENDPOINT') or '').strip()
    region = (os.getenv('TOS_REGION') or _read_env_value_from_file('TOS_REGION') or '').strip()
    bucket = (os.getenv('TOS_BUCKET') or _read_env_value_from_file('TOS_BUCKET') or '').strip()
    prefix = (os.getenv('TOS_PREFIX') or _read_env_value_from_file('TOS_PREFIX') or 'material-packages').strip().strip('/')
    if not prefix:
        prefix = 'material-packages'

    expires = _get_int_env('TOS_PRESIGN_EXPIRES_SECONDS', 3600)
    expires = max(60, min(expires, 7 * 24 * 3600))

    verify_ssl_raw = (os.getenv('TOS_VERIFY_SSL') or _read_env_value_from_file('TOS_VERIFY_SSL') or 'true').strip()
    verify_ssl = _is_truthy(verify_ssl_raw) if verify_ssl_raw else True

    provided = [access_key, secret_key, endpoint, region, bucket]
    if not any(provided) and not enabled:
        return None

    missing = []
    if not access_key:
        missing.append('TOS_ACCESS_KEY')
    if not secret_key:
        missing.append('TOS_SECRET_KEY')
    if not endpoint:
        missing.append('TOS_ENDPOINT')
    if not region:
        missing.append('TOS_REGION')
    if not bucket:
        missing.append('TOS_BUCKET')

    if missing:
        if enabled:
            raise HTTPException(
                status_code=400,
                detail=f'TOS is enabled but missing config: {", ".join(missing)}',
            )
        return None

    return {
        'access_key': access_key,
        'secret_key': secret_key,
        'endpoint': endpoint,
        'region': region,
        'bucket': bucket,
        'prefix': prefix,
        'presign_expires': expires,
        'verify_ssl': verify_ssl,
    }


def _get_tos_context() -> Optional[dict[str, Any]]:
    cfg = _get_tos_config()
    if not cfg:
        return None

    try:
        import tos
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'TOS SDK is required but not installed: {e}',
        )

    client = tos.TosClientV2(
        ak=cfg['access_key'],
        sk=cfg['secret_key'],
        endpoint=cfg['endpoint'],
        region=cfg['region'],
        enable_verify_ssl=cfg['verify_ssl'],
    )
    return {
        'client': client,
        'tos': tos,
        'bucket': cfg['bucket'],
        'prefix': cfg['prefix'],
        'presign_expires': cfg['presign_expires'],
    }


def _build_tos_object_key(prefix: str, user_id: str, package_id: str, relative_path: str) -> str:
    rel = relative_path.replace('\\', '/').lstrip('/')
    if _is_unsafe_zip_path(rel):
        raise HTTPException(status_code=400, detail=f'Unsafe object key path: {relative_path}')
    return '/'.join([prefix.strip('/'), user_id, package_id, rel]).strip('/')


def _upload_file_to_tos(
    tos_ctx: dict[str, Any],
    *,
    local_path: Path,
    object_key: str,
    mime_type: Optional[str] = None,
) -> dict[str, Any]:
    client = tos_ctx['client']
    bucket = tos_ctx['bucket']
    kwargs: dict[str, Any] = {}
    if mime_type and mime_type != 'application/octet-stream':
        kwargs['content_type'] = mime_type

    output = client.put_object_from_file(
        bucket=bucket,
        key=object_key,
        file_path=str(local_path),
        **kwargs,
    )
    return {
        'tos_key': object_key,
        'etag': getattr(output, 'etag', None),
    }


def _build_tos_presigned_url(
    tos_ctx: dict[str, Any],
    *,
    object_key: str,
) -> Optional[str]:
    output = tos_ctx['client'].pre_signed_url(
        tos_ctx['tos'].HttpMethodType.Http_Method_Get,
        bucket=tos_ctx['bucket'],
        key=object_key,
        expires=tos_ctx['presign_expires'],
    )
    signed_url = getattr(output, 'signed_url', None)
    if isinstance(signed_url, str) and (signed_url.startswith('http://') or signed_url.startswith('https://')):
        return signed_url
    return None


def _ensure_tos_url_for_asset(
    tos_ctx: Optional[dict[str, Any]],
    *,
    user_id: str,
    package_id: str,
    asset: dict[str, Any],
) -> tuple[Optional[str], bool]:
    if not tos_ctx:
        return None, False

    changed = False
    object_key = asset.get('tos_key')
    local_path = _asset_file_path_from_manifest_entry(user_id, package_id, asset)

    # If key already exists and file is marked active, try to presign directly.
    if object_key and asset.get('tos_status') == 'active':
        url = _build_tos_presigned_url(tos_ctx, object_key=object_key)
        if url:
            return url, changed
        asset['tos_status'] = 'failed'
        asset['tos_error'] = 'Failed to build presigned url for existing tos_key'
        changed = True

    if not local_path:
        return None, changed

    if not object_key:
        rel = asset.get('stored_relative_path') or asset.get('relative_path') or asset.get('filename') or asset.get('reference_name')
        object_key = _build_tos_object_key(tos_ctx['prefix'], user_id, package_id, str(rel))

    try:
        upload_result = _upload_file_to_tos(
            tos_ctx,
            local_path=local_path,
            object_key=object_key,
            mime_type=asset.get('mime_type'),
        )
        asset['tos_key'] = upload_result['tos_key']
        asset['tos_status'] = 'active'
        asset['tos_error'] = None
        changed = True
    except Exception as e:
        asset['tos_status'] = 'failed'
        asset['tos_error'] = str(e)
        changed = True
        return None, changed

    url = _build_tos_presigned_url(tos_ctx, object_key=asset['tos_key'])
    if not url:
        asset['tos_status'] = 'failed'
        asset['tos_error'] = 'Failed to build presigned url after upload'
        changed = True
        return None, changed

    return url, changed


def _save_manifest(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail='Material package not found')
    return json.loads(path.read_text(encoding='utf-8'))


def _normalize_reference_name(relative_path: str, used_names: set[str]) -> str:
    base_name = relative_path.replace(' ', '_')
    candidate = base_name
    idx = 2
    while candidate in used_names:
        candidate = f'{base_name}~{idx}'
        idx += 1
    used_names.add(candidate)
    return candidate


def _is_unsafe_zip_path(path: str) -> bool:
    p = Path(path)
    return p.is_absolute() or '..' in p.parts


def _should_skip_zip_entry(rel_path: Path) -> bool:
    # 1) Skip macOS metadata folders and dot-directories.
    for segment in rel_path.parts[:-1]:
        if segment in SKIP_DIR_NAMES or segment.startswith('.'):
            return True

    filename = rel_path.name
    filename_lower = filename.lower()

    # 2) Skip common hidden/system artifacts.
    if filename.startswith('._'):
        return True
    if filename_lower in SKIP_FILE_NAMES_LOWER:
        return True

    # 3) Skip hidden dot-files by default in material packages.
    if filename.startswith('.'):
        return True

    return False


def _media_type_for_file(path: Path) -> Optional[str]:
    return SUPPORTED_EXTENSIONS.get(path.suffix.lower())


def _is_zip_source(filename: str, mime_type: Optional[str] = None) -> bool:
    name = (filename or '').strip().lower()
    if name.endswith('.zip'):
        return True
    mt = (mime_type or '').strip().lower()
    return mt in {
        'application/zip',
        'application/x-zip-compressed',
        'application/x-zip',
        'multipart/x-zip',
    }


def _build_merged_package_name(asset_count: int) -> str:
    try:
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
    except Exception:
        now = datetime.now()
    return f'合并上传-{asset_count}个素材-{now.strftime("%y%m%d-%H%M")}'


def _dedupe_filename(filename: str, used: set[str]) -> str:
    candidate = (filename or '').strip() or f'asset_{len(used) + 1}'
    base = Path(candidate).stem
    suffix = Path(candidate).suffix
    idx = 2
    while candidate in used:
        candidate = f'{base}~{idx}{suffix}'
        idx += 1
    used.add(candidate)
    return candidate


def _create_manifest(
    *,
    user_id: str,
    chat_id: Optional[str],
    source_filename: str,
    source_kind: str,
    package_display_name: Optional[str] = None,
    merged_asset_count: Optional[int] = None,
) -> tuple[str, Path, dict[str, Any]]:
    package_id = f'pkg_{uuid.uuid4().hex[:16]}'
    now = int(time.time())
    source_filename = (source_filename or '').strip() or package_id
    package_display_name = (package_display_name or source_filename).strip() or package_id

    manifest: dict[str, Any] = {
        'id': package_id,
        'asset_package_id': package_id,
        'user_id': user_id,
        'chat_id': chat_id,
        'zip_filename': source_filename,
        'package_display_name': package_display_name,
        'source_filename': source_filename,
        'source_kind': source_kind,
        'merged_asset_count': merged_asset_count,
        'status': 'processing',
        'created_at': now,
        'updated_at': now,
        'assets': [],
        'unsupported_files': [],
        'skipped_files': [],
    }
    manifest_path = _manifest_path(user_id, package_id)
    _save_manifest(manifest_path, manifest)
    return package_id, manifest_path, manifest


def _mark_manifest_failed(manifest_path: Path, manifest: dict[str, Any]) -> None:
    manifest['status'] = 'failed'
    manifest['updated_at'] = int(time.time())
    _save_manifest(manifest_path, manifest)


def _append_asset_from_local_file(
    *,
    user_id: str,
    package_id: str,
    manifest: dict[str, Any],
    local_path: Path,
    relative_path: str,
    used_reference_names: set[str],
) -> None:
    media_type = _media_type_for_file(local_path)
    if not media_type:
        manifest['unsupported_files'].append(relative_path)
        return

    reference_name = _normalize_reference_name(relative_path, used_reference_names)
    mime_type = mimetypes.guess_type(local_path.name)[0] or 'application/octet-stream'
    storage_path = _asset_storage_path(user_id, package_id, relative_path, ensure_parent=True)
    shutil.copy2(local_path, storage_path)

    asset = {
        'reference_name': reference_name,
        'filename': local_path.name,
        'relative_path': relative_path,
        'stored_relative_path': relative_path,
        'media_type': media_type,
        'size_bytes': storage_path.stat().st_size,
        'mime_type': mime_type,
        'ark_file_id': None,
        'ark_status': None,
        'error': None,
        'tos_key': None,
        'tos_status': None,
        'tos_error': None,
    }
    manifest['assets'].append(asset)


def _upload_assets_to_tos(
    *,
    user_id: str,
    package_id: str,
    manifest: dict[str, Any],
) -> None:
    tos_ctx = _get_tos_context()
    if not tos_ctx:
        raise HTTPException(
            status_code=400,
            detail='TOS is required for material package upload. Set MATERIAL_PACK_TOS_ENABLED=true and configure TOS_ACCESS_KEY/TOS_SECRET_KEY/TOS_ENDPOINT/TOS_REGION/TOS_BUCKET.',
        )

    for asset in manifest['assets']:
        try:
            object_key = _build_tos_object_key(
                tos_ctx['prefix'],
                str(user_id),
                package_id,
                str(asset.get('stored_relative_path') or asset.get('relative_path') or asset.get('filename') or asset.get('reference_name')),
            )
            local_path = _asset_file_path_from_manifest_entry(user_id, package_id, asset)
            if not local_path:
                raise RuntimeError('Stored local file not found')

            upload_result = _upload_file_to_tos(
                tos_ctx,
                local_path=local_path,
                object_key=object_key,
                mime_type=asset.get('mime_type'),
            )
            asset['tos_key'] = upload_result.get('tos_key')
            asset['tos_status'] = 'active'
            asset['tos_error'] = None
        except Exception as e:
            log.exception(f'Material package TOS upload failed for asset: {asset.get("reference_name")}')
            asset['tos_status'] = 'failed'
            asset['tos_error'] = str(e)


def _finalize_manifest(
    *,
    user_id: str,
    package_id: str,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> MaterialPackageResponse:
    if not manifest.get('assets'):
        _mark_manifest_failed(manifest_path, manifest)
        raise HTTPException(status_code=400, detail='No supported media files found in upload')

    _upload_assets_to_tos(user_id=user_id, package_id=package_id, manifest=manifest)
    if any(item.get('tos_status') == 'failed' for item in manifest['assets']):
        manifest['status'] = 'partial_failed'
    else:
        manifest['status'] = 'ready'
    manifest['updated_at'] = int(time.time())
    _save_manifest(manifest_path, manifest)
    return _to_response_model(manifest)


def _ingest_zip_file_to_manifest(
    *,
    user_id: str,
    package_id: str,
    manifest: dict[str, Any],
    zip_path: Path,
) -> None:
    max_zip_mb = _get_int_env('MATERIAL_PACK_MAX_ZIP_MB', 256)
    max_extract_mb = _get_int_env('MATERIAL_PACK_MAX_EXTRACT_MB', 1024)
    max_files = _get_int_env('MATERIAL_PACK_MAX_FILES', 200)

    if not zip_path.is_file():
        raise HTTPException(status_code=400, detail='Zip file path does not exist')

    size_bytes = zip_path.stat().st_size
    if size_bytes <= 0:
        raise HTTPException(status_code=400, detail='Uploaded zip file is empty')
    if size_bytes > max_zip_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f'Zip file exceeds max size: {max_zip_mb} MB')

    temp_dir = Path(tempfile.mkdtemp(prefix=f'material_pack_{package_id}_'))
    try:
        total_uncompressed = 0
        used_reference_names: set[str] = set()

        with zipfile.ZipFile(zip_path, 'r') as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > max_files:
                raise HTTPException(status_code=400, detail=f'Zip contains too many files (> {max_files})')

            for info in infos:
                if _is_unsafe_zip_path(info.filename):
                    raise HTTPException(status_code=400, detail=f'Unsafe path found in zip: {info.filename}')

                rel_path = Path(info.filename)
                total_uncompressed += int(info.file_size)
                if total_uncompressed > max_extract_mb * 1024 * 1024:
                    raise HTTPException(status_code=400, detail=f'Uncompressed size exceeds max limit: {max_extract_mb} MB')

                if _should_skip_zip_entry(rel_path):
                    manifest['skipped_files'].append(rel_path.as_posix())
                    continue

                out_path = temp_dir / 'extracted' / rel_path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, 'r') as src, out_path.open('wb') as dst:
                    shutil.copyfileobj(src, dst)

                _append_asset_from_local_file(
                    user_id=user_id,
                    package_id=package_id,
                    manifest=manifest,
                    local_path=out_path,
                    relative_path=rel_path.as_posix(),
                    used_reference_names=used_reference_names,
                )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _ingest_single_files_to_manifest(
    *,
    user_id: str,
    package_id: str,
    manifest: dict[str, Any],
    file_items: list[dict[str, Any]],
) -> None:
    used_reference_names: set[str] = set()
    used_relative_names: set[str] = set()
    for item in file_items:
        local_path = Path(str(item.get('local_path') or '')).expanduser().resolve()
        if not local_path.is_file():
            manifest['unsupported_files'].append(str(item.get('original_filename') or local_path.name or 'unknown'))
            continue

        base_name = os.path.basename(str(item.get('original_filename') or local_path.name or 'asset'))
        rel_name = _dedupe_filename(base_name, used_relative_names)
        _append_asset_from_local_file(
            user_id=user_id,
            package_id=package_id,
            manifest=manifest,
            local_path=local_path,
            relative_path=rel_name,
            used_reference_names=used_reference_names,
        )


async def _resolve_upload_source_for_user(item: UploadSourceItem, user_id: str) -> dict[str, Any]:
    upload_id = (item.upload_id or '').strip()
    if upload_id:
        file_item = Files.get_file_by_id_and_user_id(upload_id, user_id)
        if inspect.isawaitable(file_item):
            file_item = await file_item
        if file_item is None:
            raise HTTPException(status_code=404, detail=f'Upload not found: {upload_id}')
        if not file_item.path:
            raise HTTPException(status_code=400, detail=f'Upload has no storage path: {upload_id}')

        local_storage_path = Storage.get_file(file_item.path)
        if inspect.isawaitable(local_storage_path):
            local_storage_path = await local_storage_path
        local_path = Path(str(local_storage_path)).expanduser().resolve()
        meta = file_item.meta or {}
        if not isinstance(meta, dict):
            meta = {}
        filename = (
            (item.original_filename or '').strip()
            or str(meta.get('name') or '').strip()
            or (file_item.filename or '').strip()
            or local_path.name
        )
        mime_type = (item.mime_type or '').strip() or str(meta.get('content_type') or '').strip() or None
        return {
            'upload_id': upload_id,
            'local_path': local_path,
            'original_filename': filename,
            'mime_type': mime_type,
        }

    file_path = (item.file_path or '').strip()
    if file_path:
        local_path = Path(file_path).expanduser().resolve()
        filename = (item.original_filename or '').strip() or local_path.name
        mime_type = (item.mime_type or '').strip() or (mimetypes.guess_type(filename)[0] or None)
        return {
            'upload_id': None,
            'local_path': local_path,
            'original_filename': filename,
            'mime_type': mime_type,
        }

    raise HTTPException(status_code=400, detail='Each upload item must provide upload_id or file_path')


def _extract_references(prompt: str) -> list[str]:
    # Stop reference capture at common punctuation so patterns like:
    #   @01_FR1.mp4，参考图
    # are parsed as "01_FR1.mp4" instead of "01_FR1.mp4，参考图".
    refs = re.findall(r'@([^\s@,，。；;:：!！?？)）\]】}》>"“”\'`]+)', prompt)
    cleaned = []
    for ref in refs:
        ref = ref.strip().rstrip('.,;:!?)]}>"\'')
        if ref:
            cleaned.append(ref)
    # dedupe while preserving order
    return list(dict.fromkeys(cleaned))


def _clean_prompt(prompt: str, references: list[str]) -> str:
    cleaned = prompt
    for ref in references:
        cleaned = re.sub(rf'@{re.escape(ref)}', ref, cleaned)
    return cleaned


def _is_happyhorse_model(model: str) -> bool:
    m = (model or '').lower().replace('_', '-').strip()
    return 'happyhorse' in m


def _is_seedance_model(model: str) -> bool:
    m = (model or '').lower()
    return 'seedance' in m or 'seedance' in m.replace('-', '')


def _generation_skill_from_model(model: Optional[str]) -> str:
    raw = str(model or '').strip()
    if _is_seedance_model(raw):
        return GENERATION_SKILL_SEEDANCE
    if _is_happyhorse_model(raw):
        return GENERATION_SKILL_HAPPYHORSE
    return GENERATION_SKILL_UNKNOWN


def _extract_progress_from_payload(payload: Any) -> Optional[float]:
    def _normalize_number(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            number = float(value)
        except Exception:
            return None
        if number < 0:
            number = 0.0
        # Some providers return 0~1 progress ratio.
        if number <= 1:
            number = number * 100
        if number > 100:
            number = 100.0
        return round(number, 2)

    if isinstance(payload, dict):
        for key in ('progress', 'percent', 'percentage'):
            value = _normalize_number(payload.get(key))
            if value is not None:
                return value
        for key in ('data', 'result', 'output'):
            value = _extract_progress_from_payload(payload.get(key))
            if value is not None:
                return value
        for value in payload.values():
            nested = _extract_progress_from_payload(value)
            if nested is not None:
                return nested
        return None

    if isinstance(payload, list):
        for item in payload:
            nested = _extract_progress_from_payload(item)
            if nested is not None:
                return nested
    return None


def _build_generation_reference_block(
    *,
    model_skill: str,
    media_type: str,
    url: str,
) -> dict[str, Any]:
    if media_type == 'image':
        return {
            'type': 'image_url',
            'image_url': {'url': url},
            'role': 'reference_image',
        }
    if model_skill == GENERATION_SKILL_HAPPYHORSE:
        raise ValueError('HappyHorse supports image references only')
    if media_type == 'video':
        return {
            'type': 'video_url',
            'video_url': {'url': url},
            'role': 'reference_video',
        }
    if media_type == 'audio':
        return {
            'type': 'audio_url',
            'audio_url': {'url': url},
            'role': 'reference_audio',
        }
    raise ValueError(f'Unsupported media type for {model_skill} tasks: {media_type}')


def _build_seedance_reference_block(media_type: str, url: str) -> dict[str, Any]:
    # Backward-compatible wrapper for older call sites.
    return _build_generation_reference_block(
        model_skill=GENERATION_SKILL_SEEDANCE,
        media_type=media_type,
        url=url,
    )


def _to_response_model(manifest: dict[str, Any]) -> MaterialPackageResponse:
    package_id = str(manifest.get('id') or manifest.get('asset_package_id') or '')
    source_filename = manifest.get('source_filename') or manifest.get('zip_filename') or package_id
    package_display_name = manifest.get('package_display_name') or source_filename
    assets = [MaterialAsset(**item) for item in manifest.get('assets', [])]
    return MaterialPackageResponse(
        id=package_id,
        asset_package_id=package_id,
        user_id=manifest['user_id'],
        chat_id=manifest.get('chat_id'),
        zip_filename=manifest.get('zip_filename') or source_filename,
        package_display_name=package_display_name,
        source_filename=source_filename,
        source_kind=manifest.get('source_kind') or 'zip',
        merged_asset_count=manifest.get('merged_asset_count'),
        status=manifest['status'],
        created_at=manifest['created_at'],
        updated_at=manifest['updated_at'],
        assets=assets,
        unsupported_files=manifest.get('unsupported_files', []),
        skipped_files=manifest.get('skipped_files', []),
    )


async def _resolve_user_name(user_id: str, cache: dict[str, str]) -> str:
    if user_id in cache:
        return cache[user_id]
    user = await Users.get_user_by_id(user_id)
    if user:
        value = str(user.name or user.username or user.id)
    else:
        value = str(user_id)
    cache[user_id] = value
    return value


def _to_generation_task_list_item(
    *,
    owner_user_id: str,
    owner_user_name: str,
    item: dict[str, Any],
    requester: UserModel,
) -> GenerationTaskListItem:
    task_id = str(item.get('task_id') or '')
    can_delete = _task_delete_allowed(requester, owner_user_id)
    archive_status = str(item.get('archive_status') or ARCHIVE_STATUS_NOT_REQUIRED).upper()
    download_ready = bool(item.get('download_ready'))

    return GenerationTaskListItem(
        task_id=task_id,
        user_id=owner_user_id,
        user_name=owner_user_name,
        package_id=item.get('package_id'),
        chat_id=item.get('chat_id'),
        model=item.get('model'),
        status=item.get('status'),
        archive_status=archive_status,
        archive_error=item.get('archive_error'),
        archive_retry_count=int(item.get('archive_retry_count') or 0),
        archive_updated_at=int(item.get('archive_updated_at') or 0),
        download_ready=download_ready,
        can_delete=can_delete,
        deleted_at=int(item.get('deleted_at') or 0) or None,
        created_at=int(item.get('created_at') or 0),
        updated_at=int(item.get('updated_at') or 0),
        references=item.get('references') or [],
        duration=item.get('duration'),
        ratio=item.get('ratio'),
        watermark=item.get('watermark'),
        generate_audio=item.get('generate_audio'),
        thumbnail_url=item.get('thumbnail_url'),
        video_preview_url=item.get('video_preview_url'),
        video_download_url=item.get('video_download_url'),
        video_url=item.get('video_url'),
        error_code=item.get('error_code'),
        error_message=item.get('error_message'),
        request_id=item.get('request_id'),
        credential_alias=item.get('credential_alias'),
        routing_group_id=item.get('routing_group_id'),
    )


async def _load_task_for_read(
    task_id: str,
    *,
    include_deleted: bool = False,
    refresh_status: bool = False,
    refresh_min_interval_seconds: int = 5,
) -> tuple[str, dict[str, Any]]:
    found = _find_task_record_owner(task_id)
    if not found:
        raise HTTPException(status_code=404, detail='Task not found')

    owner_user_id, item, _path = found
    changed = _normalize_task_defaults(item, owner_user_id=owner_user_id)

    if refresh_status and _should_refresh_task_status(item, refresh_min_interval_seconds):
        item = await _refresh_task_record_from_ark(owner_user_id, item, timeout_seconds=120)
        changed = True

    item = await _archive_task_record_if_needed(owner_user_id, item)
    if _normalize_task_defaults(item, owner_user_id=owner_user_id):
        changed = True

    if _is_soft_deleted(item) and not include_deleted:
        raise HTTPException(status_code=404, detail='Task not found')

    if changed:
        _save_task_record(owner_user_id, str(item.get('task_id') or task_id), item)

    return owner_user_id, item


@router.post('/', response_model=MaterialPackageResponse)
async def upload_material_package(
    zip_file: UploadFile = File(...),
    chat_id: Optional[str] = Form(None),
    wait_for_processing: bool = Query(True),
    user: UserModel = Depends(get_verified_user),
):
    filename = os.path.basename(zip_file.filename or '')
    if not filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail='Only .zip files are supported')

    zip_bytes = await zip_file.read()
    if not zip_bytes:
        raise HTTPException(status_code=400, detail='Uploaded zip file is empty')
    _ = wait_for_processing
    package_id, manifest_path, manifest = _create_manifest(
        user_id=str(user.id),
        chat_id=chat_id,
        source_filename=filename,
        source_kind='zip',
        package_display_name=filename,
    )
    temp_dir = Path(tempfile.mkdtemp(prefix=f'material_pack_upload_{package_id}_'))
    try:
        zip_path = temp_dir / 'input.zip'
        zip_path.write_bytes(zip_bytes)
        _ingest_zip_file_to_manifest(
            user_id=str(user.id),
            package_id=package_id,
            manifest=manifest,
            zip_path=zip_path,
        )
        return _finalize_manifest(
            user_id=str(user.id),
            package_id=package_id,
            manifest_path=manifest_path,
            manifest=manifest,
        )
    except Exception:
        _mark_manifest_failed(manifest_path, manifest)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@router.post('/from-upload', response_model=MaterialPackageResponse)
async def create_material_package_from_chat_upload(
    form_data: CreateMaterialPackageFromUploadRequest,
    user: UserModel = Depends(get_verified_user),
):
    upload_items: list[UploadSourceItem] = []
    upload_items.extend(form_data.uploads or [])
    dedup_upload_ids: list[str] = []
    seen_upload_ids: set[str] = set()
    for raw in (form_data.upload_ids or []):
        item = str(raw).strip()
        if not item or item in seen_upload_ids:
            continue
        seen_upload_ids.add(item)
        dedup_upload_ids.append(item)
    upload_items.extend([UploadSourceItem(upload_id=item) for item in dedup_upload_ids])

    if not upload_items:
        raise HTTPException(status_code=400, detail='No uploads provided. Pass upload_ids or uploads[].')

    resolved_uploads: list[dict[str, Any]] = []
    for item in upload_items:
        resolved_uploads.append(await _resolve_upload_source_for_user(item, str(user.id)))
    zip_uploads = [item for item in resolved_uploads if _is_zip_source(str(item.get('original_filename') or ''), item.get('mime_type'))]

    if zip_uploads:
        if len(resolved_uploads) != 1:
            raise HTTPException(status_code=400, detail='ZIP upload must be sent alone in one request.')

        source = zip_uploads[0]
        source_filename = os.path.basename(str(source.get('original_filename') or 'upload.zip')).strip() or 'upload.zip'
        package_display_name = (form_data.package_display_name or '').strip() or source_filename
        package_id, manifest_path, manifest = _create_manifest(
            user_id=str(user.id),
            chat_id=form_data.chat_id,
            source_filename=source_filename,
            source_kind='zip',
            package_display_name=package_display_name,
        )
        try:
            _ingest_zip_file_to_manifest(
                user_id=str(user.id),
                package_id=package_id,
                manifest=manifest,
                zip_path=Path(str(source.get('local_path'))).expanduser().resolve(),
            )
            return _finalize_manifest(
                user_id=str(user.id),
                package_id=package_id,
                manifest_path=manifest_path,
                manifest=manifest,
            )
        except Exception:
            _mark_manifest_failed(manifest_path, manifest)
            raise

    unsupported: list[str] = []
    for item in resolved_uploads:
        local_path = Path(str(item.get('local_path') or '')).expanduser().resolve()
        original_name = os.path.basename(str(item.get('original_filename') or local_path.name or 'unknown'))
        media_type = _media_type_for_file(Path(original_name)) or _media_type_for_file(local_path)
        if media_type is None:
            unsupported.append(original_name)

    if unsupported:
        raise HTTPException(
            status_code=400,
            detail={
                'error': 'Unsupported media files',
                'unsupported_files': unsupported,
                'guidance': 'Only image/video/audio files are supported in single-file upload mode.',
            },
        )

    asset_count = len(resolved_uploads)
    if asset_count == 1:
        single_name = os.path.basename(str(resolved_uploads[0].get('original_filename') or 'upload'))
        package_display_name = (form_data.package_display_name or '').strip() or single_name
        source_filename = single_name
        merged_asset_count = None
    else:
        package_display_name = (form_data.package_display_name or '').strip() or _build_merged_package_name(asset_count)
        source_filename = package_display_name
        merged_asset_count = asset_count

    package_id, manifest_path, manifest = _create_manifest(
        user_id=str(user.id),
        chat_id=form_data.chat_id,
        source_filename=source_filename,
        source_kind='single_file',
        package_display_name=package_display_name,
        merged_asset_count=merged_asset_count,
    )

    try:
        _ingest_single_files_to_manifest(
            user_id=str(user.id),
            package_id=package_id,
            manifest=manifest,
            file_items=resolved_uploads,
        )
        return _finalize_manifest(
            user_id=str(user.id),
            package_id=package_id,
            manifest_path=manifest_path,
            manifest=manifest,
        )
    except Exception:
        _mark_manifest_failed(manifest_path, manifest)
        raise


@router.get('/', response_model=list[MaterialPackageResponse])
async def list_material_packages(user: UserModel = Depends(get_verified_user)):
    user_dir = MATERIAL_PACKAGES_DIR / user.id
    if not user_dir.exists():
        return []

    packages: list[MaterialPackageResponse] = []
    for path in sorted(user_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        manifest = _load_manifest(path)
        packages.append(_to_response_model(manifest))
    return packages


@router.get('/{package_id}/assets', response_model=MaterialPackageAssetsResponse)
async def get_material_package_assets(
    package_id: str,
    include_temp_urls: bool = Query(False),
    user: UserModel = Depends(get_verified_user),
):
    manifest = _load_manifest(_manifest_path(user.id, package_id))

    tos_ctx: Optional[dict[str, Any]] = None
    temp_url_expires_at: Optional[int] = None
    if include_temp_urls:
        tos_ctx = _get_tos_context()
        if tos_ctx:
            temp_url_expires_at = int(time.time()) + int(tos_ctx.get('presign_expires') or 3600)

    rows: list[MaterialPackageAssetAddress] = []
    for item in manifest.get('assets', []):
        temp_url = None
        if include_temp_urls and tos_ctx and item.get('tos_key') and item.get('tos_status') == 'active':
            temp_url = _build_tos_presigned_url(tos_ctx, object_key=str(item.get('tos_key')))

        rows.append(
            MaterialPackageAssetAddress(
                reference_name=str(item.get('reference_name') or ''),
                filename=str(item.get('filename') or ''),
                relative_path=str(item.get('relative_path') or ''),
                media_type=str(item.get('media_type') or ''),
                tos_key=item.get('tos_key'),
                tos_status=item.get('tos_status'),
                temp_url=temp_url,
                temp_url_expires_at=temp_url_expires_at if temp_url else None,
            )
        )

    return MaterialPackageAssetsResponse(
        asset_package_id=str(manifest.get('id') or manifest.get('asset_package_id') or package_id),
        package_display_name=manifest.get('package_display_name') or manifest.get('source_filename') or manifest.get('zip_filename'),
        assets=rows,
    )


@router.post('/providers/ark/generations/tasks', response_model=ArkGenerationTaskProxyResponse)
async def submit_ark_generation_task(
    form_data: ArkGenerationTaskSubmitRequest,
    user: UserModel = Depends(get_verified_user),
):
    if not form_data.content:
        raise HTTPException(status_code=400, detail='content is required')

    resolved = await _resolve_provider_credential(provider='seedance', user_id=str(user.id))
    response_json = await _submit_generation_task_to_ark(
        form_data.model_dump(exclude_none=True),
        timeout_seconds=180,
        api_key=str(resolved.get('api_key') or ''),
    )
    return ArkGenerationTaskProxyResponse(
        provider='ark',
        credential_alias=str(resolved.get('credential_alias') or '').strip() or None,
        routing_group_id=str(resolved.get('routing_group_id') or '').strip() or None,
        data=response_json,
    )


@router.get('/providers/ark/generations/tasks/{task_id}', response_model=ArkGenerationTaskProxyResponse)
async def query_ark_generation_task(
    task_id: str,
    credential_alias: Optional[str] = Query(default=None),
    timeout_seconds: int = Query(default=120, ge=5, le=300),
    user: UserModel = Depends(get_verified_user),
):
    resolved = await _resolve_provider_credential(
        provider='seedance',
        user_id=str(user.id),
        preferred_alias=credential_alias,
    )
    response_json = await _query_generation_task_from_ark(
        _sanitize_task_id(task_id),
        timeout_seconds=timeout_seconds,
        api_key=str(resolved.get('api_key') or ''),
    )
    return ArkGenerationTaskProxyResponse(
        provider='ark',
        credential_alias=str(resolved.get('credential_alias') or '').strip() or None,
        routing_group_id=str(resolved.get('routing_group_id') or '').strip() or None,
        data=response_json,
    )


@router.get('/tasks', response_model=list[GenerationTaskListItem])
async def list_generation_tasks(
    user_id: Optional[str] = Query(default=None),
    package_id: Optional[str] = Query(default=None),
    task_status: Optional[str] = Query(default=None, alias='status'),
    model: Optional[str] = Query(default=None),
    chat_id: Optional[str] = Query(default=None),
    include_deleted: bool = Query(default=False),
    refresh_status: bool = Query(default=True),
    refresh_min_interval_seconds: int = Query(default=5, ge=0, le=600),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user: UserModel = Depends(get_verified_user),
):
    _cleanup_expired_soft_deleted_records()

    desired_user = (user_id or '').strip() if user_id else None
    desired_status = (task_status or '').strip().lower() if task_status else None
    desired_package = (package_id or '').strip() if package_id else None
    desired_model = (model or '').strip().lower() if model else None
    desired_chat = (chat_id or '').strip() if chat_id else None

    skipped = 0
    user_name_cache: dict[str, str] = {}
    rows: list[GenerationTaskListItem] = []
    for owner_user_id, path in _iter_task_record_paths():
        if desired_user and str(owner_user_id) != desired_user:
            continue
        item = _load_task_record_from_path(path)
        if item is None:
            continue

        changed = _normalize_task_defaults(item, owner_user_id=owner_user_id)

        if refresh_status and _should_refresh_task_status(item, refresh_min_interval_seconds):
            item = await _refresh_task_record_from_ark(owner_user_id, item, timeout_seconds=120)
            changed = True

        item = await _archive_task_record_if_needed(owner_user_id, item)
        if _normalize_task_defaults(item, owner_user_id=owner_user_id):
            changed = True

        if changed:
            _save_task_record(owner_user_id, str(item.get('task_id') or path.stem), item)

        if not include_deleted and _is_soft_deleted(item):
            continue
        if desired_package and str(item.get('package_id') or '') != desired_package:
            continue
        if desired_model and str(item.get('model') or '').strip().lower() != desired_model:
            continue
        if desired_chat and item.get('chat_id') != desired_chat:
            continue
        if desired_status and str(item.get('status') or '').strip().lower() != desired_status:
            continue

        if skipped < offset:
            skipped += 1
            continue

        owner_user_name = str(item.get('user_name') or '').strip()
        if not owner_user_name:
            owner_user_name = await _resolve_user_name(owner_user_id, user_name_cache)
            item['user_name'] = owner_user_name
            _save_task_record(owner_user_id, str(item.get('task_id') or path.stem), item)

        rows.append(
            _to_generation_task_list_item(
                owner_user_id=owner_user_id,
                owner_user_name=owner_user_name,
                item=item,
                requester=user,
            )
        )

        if len(rows) >= limit:
            break

    return rows


@router.get('/tasks/users', response_model=list[GenerationTaskUserItem])
async def list_generation_task_users(
    include_deleted: bool = Query(default=False),
    user: UserModel = Depends(get_verified_user),
):
    _ = user
    _cleanup_expired_soft_deleted_records()

    user_name_cache: dict[str, str] = {}
    seen: set[str] = set()
    rows: list[GenerationTaskUserItem] = []
    for owner_user_id, path in _iter_task_record_paths():
        if owner_user_id in seen:
            continue
        item = _load_task_record_from_path(path)
        if item is None:
            continue
        if not include_deleted and _is_soft_deleted(item):
            continue

        user_name = str(item.get('user_name') or '').strip()
        if not user_name:
            user_name = await _resolve_user_name(owner_user_id, user_name_cache)

        rows.append(GenerationTaskUserItem(user_id=owner_user_id, user_name=user_name))
        seen.add(owner_user_id)

    rows.sort(key=lambda row: row.user_name.lower())
    return rows


@router.get('/tasks/{task_id}/preview', response_model=GenerationTaskPreviewResponse)
async def get_generation_task_preview(
    task_id: str,
    refresh_status: bool = Query(default=True),
    user: UserModel = Depends(get_verified_user),
):
    owner_user_id, item = await _load_task_for_read(
        task_id,
        refresh_status=refresh_status,
        refresh_min_interval_seconds=5,
    )
    return GenerationTaskPreviewResponse(
        task_id=str(item.get('task_id') or task_id),
        status=item.get('status'),
        archive_status=item.get('archive_status'),
        download_ready=bool(item.get('download_ready')),
        can_delete=_task_delete_allowed(user, owner_user_id),
        thumbnail_url=item.get('thumbnail_url'),
        video_preview_url=item.get('video_preview_url'),
    )


@router.get('/tasks/{task_id}/video')
async def stream_generation_task_video(task_id: str, user: UserModel = Depends(get_verified_user)):
    _ = user
    owner_user_id, item = await _load_task_for_read(task_id, refresh_status=False)
    if not item.get('download_ready'):
        raise HTTPException(status_code=409, detail='ArchiveNotReady')

    video_path = _task_file_from_relative(owner_user_id, item.get('archived_video_path'))
    if not video_path:
        raise HTTPException(status_code=404, detail='Archived video not found')

    media_type = mimetypes.guess_type(video_path.name)[0] or 'video/mp4'
    return FileResponse(path=video_path, media_type=media_type, filename=video_path.name)


@router.get('/tasks/{task_id}/thumbnail')
async def get_generation_task_thumbnail(task_id: str, user: UserModel = Depends(get_verified_user)):
    _ = user
    owner_user_id, item = await _load_task_for_read(task_id, refresh_status=False)
    thumbnail_path = _task_file_from_relative(owner_user_id, item.get('thumbnail_path'))
    if not thumbnail_path:
        raise HTTPException(status_code=404, detail='Thumbnail not found')
    media_type = mimetypes.guess_type(thumbnail_path.name)[0] or 'image/jpeg'
    return FileResponse(path=thumbnail_path, media_type=media_type, filename=thumbnail_path.name)


@router.get('/tasks/{task_id}/download')
async def download_generation_task(task_id: str, user: UserModel = Depends(get_verified_user)):
    _ = user
    owner_user_id, item = await _load_task_for_read(task_id, refresh_status=False)
    if not item.get('download_ready'):
        raise HTTPException(status_code=409, detail='ArchiveNotReady')

    video_path = _task_file_from_relative(owner_user_id, item.get('archived_video_path'))
    if not video_path:
        raise HTTPException(status_code=404, detail='Archived video not found')

    media_type = mimetypes.guess_type(video_path.name)[0] or 'video/mp4'
    return FileResponse(path=video_path, media_type=media_type, filename=video_path.name)


@router.post('/tasks/{task_id}/archive/retry')
async def retry_generation_task_archive(task_id: str, user: UserModel = Depends(get_verified_user)):
    owner_user_id, item = await _load_task_for_read(task_id, refresh_status=True)
    if not _task_delete_allowed(user, owner_user_id):
        raise HTTPException(status_code=403, detail='No permission to retry archive')

    item['archive_status'] = ARCHIVE_STATUS_PENDING
    item['archive_error'] = None
    item['archive_updated_at'] = int(time.time())
    _save_task_record(owner_user_id, str(item.get('task_id') or task_id), item)
    item = await _archive_task_record_if_needed(owner_user_id, item, force_retry=True)
    return {
        'ok': True,
        'task_id': str(item.get('task_id') or task_id),
        'archive_status': item.get('archive_status'),
    }


@router.delete('/tasks/{task_id}')
async def soft_delete_generation_task(
    task_id: str,
    delete_reason: Optional[str] = Query(default=None),
    user: UserModel = Depends(get_verified_user),
):
    owner_user_id, item = await _load_task_for_read(
        task_id,
        include_deleted=True,
        refresh_status=False,
    )
    if not _task_delete_allowed(user, owner_user_id):
        raise HTTPException(status_code=403, detail='No permission to delete this task')

    now = int(time.time())
    item['deleted_at'] = now
    item['deleted_by'] = str(user.id)
    item['delete_reason'] = (delete_reason or '').strip() or None
    item['updated_at'] = now
    _save_task_record(owner_user_id, str(item.get('task_id') or task_id), item)

    return {
        'ok': True,
        'task_id': str(item.get('task_id') or task_id),
        'deleted_at': now,
    }


@router.get('/{package_id}', response_model=MaterialPackageResponse)
async def get_material_package(package_id: str, user: UserModel = Depends(get_verified_user)):
    manifest = _load_manifest(_manifest_path(user.id, package_id))
    return _to_response_model(manifest)


@router.post('/{package_id}/resolve', response_model=ResolveReferencesResponse)
async def resolve_material_references(
    package_id: str,
    form_data: ResolveReferencesRequest,
    user: UserModel = Depends(get_verified_user),
):
    manifest = _load_manifest(_manifest_path(user.id, package_id))

    available_assets = [MaterialAsset(**item) for item in manifest.get('assets', [])]
    available_refs = {item.reference_name: item for item in available_assets}

    refs = _extract_references(form_data.prompt)
    missing = [ref for ref in refs if ref not in available_refs]

    cleaned_prompt = _clean_prompt(form_data.prompt, refs)
    resolved_assets = [available_refs[ref] for ref in refs if ref in available_refs]

    return ResolveReferencesResponse(
        package_id=package_id,
        references=refs,
        missing_references=missing,
        available_references=sorted(list(available_refs.keys())),
        cleaned_prompt=cleaned_prompt,
        assets=resolved_assets,
    )


@router.post('/{package_id}/generate', response_model=GenerateWithPackageResponse)
async def generate_with_material_package(
    package_id: str,
    form_data: GenerateWithPackageRequest,
    user: UserModel = Depends(get_verified_user),
):
    manifest = _load_manifest(_manifest_path(user.id, package_id))
    assets_raw = [item for item in manifest.get('assets', [])]
    assets = [MaterialAsset(**item) for item in assets_raw]
    assets_map = {item.reference_name: item for item in assets}
    assets_raw_map = {str(item.get('reference_name')): item for item in assets_raw}

    references = _extract_references(form_data.prompt)
    missing = [ref for ref in references if ref not in assets_map]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                'error': 'Missing referenced files',
                'missing_references': missing,
                'available_references': sorted(list(assets_map.keys())),
            },
        )

    cleaned_prompt = _clean_prompt(form_data.prompt, references)

    generation_skill = _generation_skill_from_model(form_data.model)
    if generation_skill in {GENERATION_SKILL_SEEDANCE, GENERATION_SKILL_HAPPYHORSE}:
        # Seedance/HappyHorse models use content generations tasks API and TOS URLs only.
        generation_content: list[dict[str, Any]] = [
            {'type': 'text', 'text': cleaned_prompt},
        ]
        tos_ctx = _get_tos_context()
        if not tos_ctx:
            raise HTTPException(
                status_code=400,
                detail='TOS is required for generation tasks. Set MATERIAL_PACK_TOS_ENABLED=true and configure TOS_ACCESS_KEY/TOS_SECRET_KEY/TOS_ENDPOINT/TOS_REGION/TOS_BUCKET.',
            )

        unresolved_references: list[dict[str, Any]] = []
        manifest_changed = False

        for idx, ref in enumerate(references):
            item = assets_map[ref]
            if generation_skill == GENERATION_SKILL_HAPPYHORSE and item.media_type != 'image':
                raise HTTPException(
                    status_code=400,
                    detail={
                        'ok': False,
                        'error_code': 'ModelConstraintViolation',
                        'error_message': f'{form_data.model} only supports image references',
                        'details': {
                            'field': f'references[{idx}].media_type',
                            'allowed': ['image'],
                            'actual': item.media_type,
                        },
                    },
                )

            file_url: Optional[str] = None
            asset_raw = assets_raw_map.get(ref, {})
            tos_url, changed = _ensure_tos_url_for_asset(
                tos_ctx,
                user_id=str(user.id),
                package_id=package_id,
                asset=asset_raw,
            )
            if changed:
                manifest_changed = True
            if tos_url:
                file_url = tos_url

            if not file_url:
                unresolved_references.append(
                    {
                        'reference': ref,
                        'has_tos_config': bool(tos_ctx),
                        'tos_key': assets_raw_map.get(ref, {}).get('tos_key'),
                        'tos_status': assets_raw_map.get(ref, {}).get('tos_status'),
                        'has_stored_file': bool(_asset_file_path_from_manifest_entry(user.id, package_id, assets_raw_map.get(ref, {}))),
                    }
                )
                continue

            generation_content.append(
                _build_generation_reference_block(
                    model_skill=generation_skill,
                    media_type=item.media_type,
                    url=file_url,
                )
            )

        if manifest_changed:
            manifest['updated_at'] = int(time.time())
            _save_manifest(_manifest_path(user.id, package_id), manifest)

        if unresolved_references:
            raise HTTPException(
                status_code=400,
                detail={
                    'error': 'Unable to resolve TOS URL for some references',
                    'unresolved_references': unresolved_references,
                    'guidance': (
                        'Generation now uses TOS only. '
                        'Please check TOS credentials, bucket permission, and whether local stored files still exist for this package.'
                    ),
                },
            )

        payload: dict[str, Any] = {
            'model': form_data.model,
            'content': generation_content,
        }
        if form_data.duration is not None:
            payload['duration'] = form_data.duration
        if form_data.ratio:
            payload['ratio'] = form_data.ratio
        if form_data.watermark is not None:
            payload['watermark'] = form_data.watermark
        if generation_skill == GENERATION_SKILL_SEEDANCE and form_data.generate_audio is not None:
            payload['generate_audio'] = form_data.generate_audio

        resolved = await _resolve_provider_credential(provider='seedance', user_id=str(user.id))
        response_json = await _submit_generation_task_to_ark(
            payload,
            timeout_seconds=180,
            api_key=str(resolved.get('api_key') or ''),
        )

        task_id = (
            response_json.get('task_id')
            or response_json.get('id')
            or (response_json.get('data') or {}).get('task_id')
            or (response_json.get('data') or {}).get('id')
        )
        task_status = response_json.get('status') or (response_json.get('data') or {}).get('status') or 'submitted'

        if task_id:
            _touch_task_record(
                str(user.id),
                str(task_id),
                user_name=str(user.name or user.username or user.id),
                provider='ark',
                provider_task_id=str(task_id),
                tool_name='material_packages.generate',
                skill_name=generation_skill,
                package_id=package_id,
                chat_id=manifest.get('chat_id'),
                model=form_data.model,
                references=references,
                duration=form_data.duration,
                ratio=form_data.ratio,
                watermark=form_data.watermark,
                generate_audio=form_data.generate_audio,
                status=task_status,
                credential_alias=str(resolved.get('credential_alias') or '').strip() or None,
                routing_group_id=str(resolved.get('routing_group_id') or '').strip() or None,
                raw_submit_response=response_json,
                raw_last_response=response_json,
            )
            _spawn_task_archive_poller(str(user.id), str(task_id))

        return GenerateWithPackageResponse(
            package_id=package_id,
            references=references,
            response_id=task_id,
            status=task_status,
            output_text=None,
            raw_response=response_json,
        )

    raise HTTPException(
        status_code=400,
        detail='Only seedance/happyhorse models are supported in TOS-only mode.',
    )


@router.get('/tasks/{task_id}', response_model=GenerationTaskStatusResponse)
async def get_generation_task_status(
    task_id: str,
    refresh_status: bool = Query(default=True),
    refresh_min_interval_seconds: int = Query(default=5, ge=0, le=600),
    user: UserModel = Depends(get_verified_user),
):
    _ = user
    owner_user_id, item = await _load_task_for_read(
        task_id,
        refresh_status=refresh_status,
        refresh_min_interval_seconds=refresh_min_interval_seconds,
    )

    safe_task_id = _sanitize_task_id(str(item.get('task_id') or task_id))
    _spawn_task_archive_poller(owner_user_id, safe_task_id)

    return GenerationTaskStatusResponse(
        task_id=safe_task_id,
        status=item.get('status'),
        raw_response=item.get('raw_last_response') or {},
    )

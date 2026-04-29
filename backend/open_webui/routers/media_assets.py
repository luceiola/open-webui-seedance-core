from __future__ import annotations

import json
import mimetypes
import os
import re
import inspect
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from open_webui.config import CACHE_DIR
from open_webui.models.files import Files
from open_webui.models.users import UserModel
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_verified_user

router = APIRouter()

MEDIA_ASSETS_DIR = CACHE_DIR / 'media_assets'
MEDIA_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

ARK_ENV_FILE_CANDIDATES: list[Path] = [
    Path(os.getenv('ARK_ENV_FILE', '')).expanduser().resolve() if os.getenv('ARK_ENV_FILE') else None,
    Path.cwd() / 'config' / 'ark.env',
    Path.cwd() / 'config' / 'ark.dev.env',
    Path.cwd() / '.env',
]

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


class MediaAssetItem(BaseModel):
    asset_id: str
    user_id: str
    chat_id: Optional[str] = None
    display_name: str
    relative_path: Optional[str] = None
    original_filename: str
    media_type: str
    mime_type: Optional[str] = None
    size_bytes: int
    status: str
    tos_key: Optional[str] = None
    tos_status: Optional[str] = None
    tos_error: Optional[str] = None
    created_at: int
    updated_at: int


class MediaAssetUrlResponse(BaseModel):
    asset_id: str
    url: str
    expires_in: int


class UploadSourceItem(BaseModel):
    upload_id: Optional[str] = None
    file_path: Optional[str] = None
    relative_path: Optional[str] = None
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None


class CreateMediaAssetsFromUploadRequest(BaseModel):
    chat_id: Optional[str] = None
    upload_ids: list[str] = Field(default_factory=list)
    uploads: list[UploadSourceItem] = Field(default_factory=list)


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


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name) or _read_env_value_from_file(name)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _normalize_filename(name: str, fallback: str) -> str:
    candidate = Path((name or '').strip()).name
    if not candidate:
        candidate = fallback
    candidate = candidate.replace('\\', '_').replace('/', '_')
    candidate = re.sub(r'\s+', '_', candidate)
    candidate = candidate.strip('._')
    return candidate or fallback


def _normalize_relative_path(path_value: str, fallback: str) -> str:
    raw = (path_value or '').strip().replace('\\', '/')
    if not raw:
        raw = fallback

    raw = re.sub(r'/+', '/', raw).lstrip('/')
    if raw.startswith('./'):
        raw = raw[2:]

    parts: list[str] = []
    for seg in raw.split('/'):
        part = seg.strip()
        if not part or part == '.':
            continue
        if part == '..':
            raise HTTPException(status_code=400, detail=f'Unsafe relative path: {path_value}')
        part = re.sub(r'\s+', '_', part)
        part = part.replace('\\', '_').replace('/', '_')
        if not part:
            continue
        parts.append(part)

    if not parts:
        return _normalize_filename(fallback, fallback)
    return '/'.join(parts)


def _media_type_for_filename(filename: str) -> Optional[str]:
    return SUPPORTED_EXTENSIONS.get(Path(filename).suffix.lower())


def _media_type_for_mime(mime_type: Optional[str]) -> Optional[str]:
    mt = str(mime_type or '').strip().lower()
    if not mt:
        return None
    if mt.startswith('image/'):
        return 'image'
    if mt.startswith('video/'):
        return 'video'
    if mt.startswith('audio/'):
        return 'audio'
    return None


def _user_dir(user_id: str) -> Path:
    path = MEDIA_ASSETS_DIR / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _asset_path(user_id: str, asset_id: str) -> Path:
    return _user_dir(user_id) / f'{asset_id}.json'


def _save_asset(user_id: str, asset_id: str, data: dict[str, Any]) -> None:
    _asset_path(user_id, asset_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _load_asset(user_id: str, asset_id: str) -> dict[str, Any]:
    path = _asset_path(user_id, asset_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail='Media asset not found')
    return json.loads(path.read_text(encoding='utf-8'))


def _list_assets(user_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(_user_dir(user_id).glob('asset_*.json'), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            rows.append(json.loads(p.read_text(encoding='utf-8')))
        except Exception:
            continue
    return rows


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
        relative_path = (
            (item.relative_path or '').strip()
            or str(meta.get('relative_path') or '').strip()
            or (item.original_filename or '').strip()
        )
        filename = (
            relative_path
            or str(meta.get('name') or '').strip()
            or (file_item.filename or '').strip()
            or local_path.name
        )
        mime_type = (item.mime_type or '').strip() or str(meta.get('content_type') or '').strip() or None
        return {
            'upload_id': upload_id,
            'local_path': local_path,
            'original_filename': filename,
            'relative_path': relative_path or filename,
            'mime_type': mime_type,
        }

    file_path = (item.file_path or '').strip()
    if file_path:
        local_path = Path(file_path).expanduser().resolve()
        relative_path = (item.relative_path or '').strip() or (item.original_filename or '').strip()
        filename = relative_path or local_path.name
        mime_type = (item.mime_type or '').strip() or (mimetypes.guess_type(filename)[0] or None)
        return {
            'upload_id': None,
            'local_path': local_path,
            'original_filename': filename,
            'relative_path': relative_path or filename,
            'mime_type': mime_type,
        }

    raise HTTPException(status_code=400, detail='Each upload item must provide upload_id or file_path')


def _get_media_tos_config(required: bool) -> Optional[dict[str, Any]]:
    # MEDIA_ASSET_* has higher priority; fallback to material pack TOS configs.
    enabled_raw = (
        os.getenv('MEDIA_ASSET_TOS_ENABLED')
        or _read_env_value_from_file('MEDIA_ASSET_TOS_ENABLED')
        or os.getenv('MATERIAL_PACK_TOS_ENABLED')
        or _read_env_value_from_file('MATERIAL_PACK_TOS_ENABLED')
        or ''
    ).strip()
    enabled = _is_truthy(enabled_raw) if enabled_raw else False

    access_key = (os.getenv('TOS_ACCESS_KEY') or _read_env_value_from_file('TOS_ACCESS_KEY') or '').strip()
    secret_key = (os.getenv('TOS_SECRET_KEY') or _read_env_value_from_file('TOS_SECRET_KEY') or '').strip()
    endpoint = (os.getenv('TOS_ENDPOINT') or _read_env_value_from_file('TOS_ENDPOINT') or '').strip()
    region = (os.getenv('TOS_REGION') or _read_env_value_from_file('TOS_REGION') or '').strip()
    bucket = (
        os.getenv('MEDIA_ASSET_TOS_BUCKET')
        or _read_env_value_from_file('MEDIA_ASSET_TOS_BUCKET')
        or os.getenv('TOS_BUCKET')
        or _read_env_value_from_file('TOS_BUCKET')
        or ''
    ).strip()
    prefix = (
        os.getenv('MEDIA_ASSET_TOS_PREFIX')
        or _read_env_value_from_file('MEDIA_ASSET_TOS_PREFIX')
        or 'media-assets'
    ).strip().strip('/')
    if not prefix:
        prefix = 'media-assets'

    expires = _get_int_env('MEDIA_ASSET_TOS_PRESIGN_EXPIRES_SECONDS', _get_int_env('TOS_PRESIGN_EXPIRES_SECONDS', 3600))
    expires = max(60, min(expires, 7 * 24 * 3600))

    verify_ssl_raw = (os.getenv('TOS_VERIFY_SSL') or _read_env_value_from_file('TOS_VERIFY_SSL') or 'true').strip()
    verify_ssl = _is_truthy(verify_ssl_raw) if verify_ssl_raw else True

    if not enabled:
        if required:
            raise HTTPException(
                status_code=400,
                detail=(
                    'TOS is required for media asset upload. '
                    'Set MEDIA_ASSET_TOS_ENABLED=true (or MATERIAL_PACK_TOS_ENABLED=true) and configure '
                    'TOS_ACCESS_KEY/TOS_SECRET_KEY/TOS_ENDPOINT/TOS_REGION/TOS_BUCKET.'
                ),
            )
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
        raise HTTPException(status_code=400, detail=f'TOS is enabled but missing config: {", ".join(missing)}')

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


def _get_tos_context(required: bool = True) -> Optional[dict[str, Any]]:
    cfg = _get_media_tos_config(required=required)
    if not cfg:
        return None
    try:
        import tos
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'TOS SDK is required but not installed: {e}')

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


def _build_tos_object_key(prefix: str, user_id: str, asset_id: str, relative_path: str) -> str:
    rel = _normalize_relative_path(relative_path, fallback='asset.bin').replace('\\', '/').lstrip('/')
    if not rel or '..' in Path(rel).parts:
        raise HTTPException(status_code=400, detail=f'Unsafe object key path: {relative_path}')
    return '/'.join([prefix.strip('/'), user_id, asset_id, rel]).strip('/')


def _upload_file_to_tos(
    tos_ctx: dict[str, Any],
    *,
    local_path: Path,
    object_key: str,
    mime_type: Optional[str],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if mime_type and mime_type != 'application/octet-stream':
        kwargs['content_type'] = mime_type
    output = tos_ctx['client'].put_object_from_file(
        bucket=tos_ctx['bucket'],
        key=object_key,
        file_path=str(local_path),
        **kwargs,
    )
    return {
        'tos_key': object_key,
        'etag': getattr(output, 'etag', None),
    }


def _build_tos_presigned_url(tos_ctx: dict[str, Any], *, object_key: str, expires_in: Optional[int] = None) -> str:
    expires = expires_in if expires_in is not None else int(tos_ctx['presign_expires'])
    expires = max(60, min(int(expires), 7 * 24 * 3600))
    output = tos_ctx['client'].pre_signed_url(
        tos_ctx['tos'].HttpMethodType.Http_Method_Get,
        bucket=tos_ctx['bucket'],
        key=object_key,
        expires=expires,
    )
    signed_url = getattr(output, 'signed_url', None)
    if not isinstance(signed_url, str) or not signed_url.startswith(('http://', 'https://')):
        raise HTTPException(status_code=500, detail='Failed to build TOS presigned URL')
    return signed_url


def _create_media_asset_record_from_local_file(
    *,
    user_id: str,
    chat_id: Optional[str],
    local_path: Path,
    original_filename: str,
    mime_type: Optional[str],
    tos_ctx: dict[str, Any],
    max_file_bytes: int,
    max_file_mb: int,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if not local_path.is_file():
        return None, 'File not found'

    fallback_name = local_path.name or f'asset_{uuid.uuid4().hex[:8]}'
    relative_path = _normalize_relative_path(original_filename, fallback=fallback_name)
    media_type = _media_type_for_filename(relative_path) or _media_type_for_mime(mime_type)
    if not media_type:
        return None, 'Unsupported media type'

    size_bytes = int(local_path.stat().st_size)
    if size_bytes <= 0:
        return None, 'Empty file'
    if size_bytes > max_file_bytes:
        return None, f'File exceeds max size ({max_file_mb}MB)'

    final_mime_type = mime_type or mimetypes.guess_type(relative_path)[0] or 'application/octet-stream'
    asset_id = f'asset_{uuid.uuid4().hex[:16]}'
    object_key = _build_tos_object_key(tos_ctx['prefix'], user_id, asset_id, relative_path)
    try:
        _upload_file_to_tos(
            tos_ctx,
            local_path=local_path,
            object_key=object_key,
            mime_type=final_mime_type,
        )
    except Exception as e:
        return None, str(e)

    now = int(time.time())
    row = {
        'asset_id': asset_id,
        'user_id': user_id,
        'chat_id': chat_id,
        'display_name': relative_path,
        'relative_path': relative_path,
        'original_filename': original_filename or relative_path,
        'media_type': media_type,
        'mime_type': final_mime_type,
        'size_bytes': size_bytes,
        'status': 'active',
        'tos_key': object_key,
        'tos_status': 'active',
        'tos_error': None,
        'created_at': now,
        'updated_at': now,
    }
    _save_asset(user_id, asset_id, row)
    return row, None


@router.post('/upload')
async def upload_media_assets(
    files: list[UploadFile] = File(...),
    chat_id: Optional[str] = Form(default=None),
    user: UserModel = Depends(get_verified_user),
):
    if not files:
        raise HTTPException(status_code=400, detail='No files provided')

    tos_ctx = _get_tos_context(required=True)
    max_file_mb = _get_int_env('MEDIA_ASSET_MAX_FILE_MB', 1024)
    max_file_bytes = max(1, max_file_mb) * 1024 * 1024

    uploaded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for file in files:
        original_name = file.filename or ''
        content = await file.read()
        if len(content) <= 0:
            failed.append({'filename': original_name or 'unknown', 'error': 'Empty file'})
            continue

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix='media_asset_',
                suffix=Path(original_name or '').suffix,
                delete=False,
            ) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            row, error = _create_media_asset_record_from_local_file(
                user_id=str(user.id),
                chat_id=chat_id,
                local_path=tmp_path,
                original_filename=original_name or tmp_path.name,
                mime_type=file.content_type,
                tos_ctx=tos_ctx,
                max_file_bytes=max_file_bytes,
                max_file_mb=max_file_mb,
            )
            if error or row is None:
                failed.append({'filename': original_name or tmp_path.name, 'error': error or 'Upload failed'})
                continue
            uploaded.append(row)
        except Exception as e:
            failed.append({'filename': original_name or 'unknown', 'error': str(e)})
            continue
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    return {
        'ok': True,
        'uploaded': uploaded,
        'failed': failed,
        'count': len(uploaded),
    }


@router.get('/', response_model=list[MediaAssetItem])
async def list_media_assets(
    media_type: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias='status'),
    chat_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: UserModel = Depends(get_verified_user),
):
    rows = _list_assets(str(user.id))
    filtered: list[dict[str, Any]] = []

    for row in rows:
        if media_type and str(row.get('media_type') or '').strip().lower() != media_type.strip().lower():
            continue
        if status_filter and str(row.get('status') or '').strip().lower() != status_filter.strip().lower():
            continue
        if chat_id and str(row.get('chat_id') or '') != chat_id:
            continue
        filtered.append(row)

    return [MediaAssetItem(**item) for item in filtered[offset : offset + limit]]


@router.post('/from-upload')
async def create_media_assets_from_chat_upload(
    form_data: CreateMediaAssetsFromUploadRequest,
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

    tos_ctx = _get_tos_context(required=True)
    max_file_mb = _get_int_env('MEDIA_ASSET_MAX_FILE_MB', 1024)
    max_file_bytes = max(1, max_file_mb) * 1024 * 1024

    uploaded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    resolved_uploads: list[dict[str, Any]] = []
    for item in upload_items:
        resolved_uploads.append(await _resolve_upload_source_for_user(item, str(user.id)))

    for source in resolved_uploads:
        local_path = Path(str(source.get('local_path') or '')).expanduser().resolve()
        original_name = str(
            source.get('relative_path')
            or source.get('original_filename')
            or local_path.name
            or 'unknown'
        )
        mime_type = source.get('mime_type')

        row, error = _create_media_asset_record_from_local_file(
            user_id=str(user.id),
            chat_id=form_data.chat_id,
            local_path=local_path,
            original_filename=original_name,
            mime_type=mime_type,
            tos_ctx=tos_ctx,
            max_file_bytes=max_file_bytes,
            max_file_mb=max_file_mb,
        )
        if error or row is None:
            failed.append({'filename': original_name, 'error': error or 'Upload failed'})
            continue
        uploaded.append(row)

    return {
        'ok': True,
        'uploaded': uploaded,
        'failed': failed,
        'count': len(uploaded),
    }


@router.get('/{asset_id}/url', response_model=MediaAssetUrlResponse)
async def get_media_asset_url(
    asset_id: str,
    expires_in: Optional[int] = Query(default=None, ge=60, le=604800),
    user: UserModel = Depends(get_verified_user),
):
    row = _load_asset(str(user.id), asset_id)
    if row.get('status') != 'active' or row.get('tos_status') != 'active':
        raise HTTPException(status_code=400, detail='Media asset is not active')

    tos_key = row.get('tos_key')
    if not tos_key:
        raise HTTPException(status_code=400, detail='Media asset has no TOS key')

    tos_ctx = _get_tos_context(required=True)
    url = _build_tos_presigned_url(tos_ctx, object_key=str(tos_key), expires_in=expires_in)
    actual_expires = int(expires_in or tos_ctx['presign_expires'])
    return MediaAssetUrlResponse(asset_id=asset_id, url=url, expires_in=actual_expires)


@router.get('/{asset_id}', response_model=MediaAssetItem)
async def get_media_asset_detail(asset_id: str, user: UserModel = Depends(get_verified_user)):
    row = _load_asset(str(user.id), asset_id)
    return MediaAssetItem(**row)

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from pydantic import BaseModel
from typing import Any, Optional
import mimetypes
import logging
import re
import time

from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.task import (
    title_generation_template,
    follow_up_generation_template,
    query_generation_template,
    image_prompt_generation_template,
    autocomplete_generation_template,
    tags_generation_template,
    emoji_generation_template,
    moa_response_generation_template,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.constants import ERROR_MESSAGES, TASKS
from open_webui.models.users import UserModel
from open_webui.routers import material_packages as material_packages_router

from open_webui.routers.pipelines import process_pipeline_inlet_filter

from open_webui.utils.task import get_task_model_id

from open_webui.config import (
    DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_FOLLOW_UP_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_TAGS_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_EMOJI_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_MOA_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_VOICE_MODE_PROMPT_TEMPLATE,
)

log = logging.getLogger(__name__)

router = APIRouter()


##################################
#
# Task Endpoints
#
##################################


class ActiveChatsForm(BaseModel):
    chat_ids: list[str]


UNIFIED_TASK_TERMINAL_STATUSES: set[str] = {'SUCCEEDED', 'FAILED', 'CANCELED'}
UNIFIED_TASK_STATUS_ALIASES: dict[str, str] = {
    'SUCCESS': 'SUCCEEDED',
    'COMPLETED': 'SUCCEEDED',
    'ERROR': 'FAILED',
    'CANCELLED': 'CANCELED',
    'SUBMITTED': 'PENDING',
    'QUEUED': 'PENDING',
    'IN_PROGRESS': 'RUNNING',
}
UNIFIED_TASK_ARCHIVE_STATUSES: set[str] = {
    'NOT_REQUIRED',
    'PENDING',
    'RUNNING',
    'SUCCEEDED',
    'FAILED',
}


class UnifiedTaskItem(BaseModel):
    id: str
    provider: str
    provider_task_id: str
    tool_name: str
    skill_name: Optional[str] = None
    user_id: str
    user_name: str
    chat_id: Optional[str] = None
    model: Optional[str] = None
    status: str
    archive_status: str
    progress: Optional[float] = None
    download_ready: bool = False
    can_delete: bool = False
    can_cancel: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    request_id: Optional[str] = None
    deleted_at: Optional[int] = None
    created_at: int
    updated_at: int
    finished_at: Optional[int] = None
    thumbnail_url: Optional[str] = None
    video_preview_url: Optional[str] = None
    video_download_url: Optional[str] = None


class UnifiedTaskArtifactItem(BaseModel):
    id: str
    task_id: str
    artifact_type: str
    storage_backend: str
    storage_path: str
    mime_type: Optional[str] = None
    bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_ms: Optional[int] = None
    preview_ready: bool
    download_ready: bool
    created_at: int


class UnifiedTaskListResponse(BaseModel):
    items: list[UnifiedTaskItem]
    total: int
    offset: int
    limit: int


class UnifiedTaskUserItem(BaseModel):
    user_id: str
    user_name: str


class UnifiedTaskUsersResponse(BaseModel):
    users: list[UnifiedTaskUserItem]


class UnifiedTaskProvidersResponse(BaseModel):
    providers: list[str]


class UnifiedTaskDetailResponse(BaseModel):
    task: UnifiedTaskItem
    artifacts: list[UnifiedTaskArtifactItem]


class UnifiedTaskPreviewResponse(BaseModel):
    ok: bool = True
    task_id: str
    status: str
    archive_status: str
    download_ready: bool
    can_delete: bool
    thumbnail_url: Optional[str] = None
    video_preview_url: Optional[str] = None


def _normalize_unified_status(raw_status: Optional[str]) -> str:
    value = str(raw_status or '').strip().upper()
    if not value:
        return 'PENDING'
    value = UNIFIED_TASK_STATUS_ALIASES.get(value, value)
    if value in {'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED'}:
        return value
    return 'RUNNING'


def _parse_status_filter(raw_status: Optional[str]) -> Optional[str]:
    if raw_status is None:
        return None
    value = str(raw_status).strip().upper()
    if not value:
        return None
    normalized = UNIFIED_TASK_STATUS_ALIASES.get(value, value)
    if normalized == 'UNKNOWN':
        return normalized
    if normalized not in {'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED'}:
        raise HTTPException(status_code=400, detail='Invalid status filter')
    return normalized


def _normalize_archive_status(raw_status: Optional[str]) -> str:
    value = str(raw_status or '').strip().upper()
    if value in UNIFIED_TASK_ARCHIVE_STATUSES:
        return value
    return 'NOT_REQUIRED'


def _provider_cancel_supported(provider: str) -> bool:
    # v1.1.3 phase-1: cancellation endpoint is reserved but provider-side cancel is not wired yet.
    _ = provider
    return False


def _can_cancel_task(
    *,
    provider: str,
    status_value: str,
    can_delete: bool,
    is_deleted: bool,
) -> bool:
    return (
        can_delete
        and not is_deleted
        and status_value in {'PENDING', 'RUNNING'}
        and _provider_cancel_supported(provider)
    )


def _to_unified_task_item(
    *,
    owner_user_id: str,
    owner_user_name: str,
    item: dict[str, Any],
    requester: UserModel,
) -> UnifiedTaskItem:
    task_id = str(item.get('task_id') or '').strip()
    provider = str(item.get('provider') or 'ark').strip().lower() or 'ark'
    status_value = _normalize_unified_status(item.get('status'))
    archive_status = _normalize_archive_status(item.get('archive_status'))

    can_delete = material_packages_router._task_delete_allowed(requester, owner_user_id)
    is_deleted = material_packages_router._is_soft_deleted(item)
    can_cancel = _can_cancel_task(
        provider=provider,
        status_value=status_value,
        can_delete=can_delete,
        is_deleted=is_deleted,
    )
    created_at = int(item.get('created_at') or 0)
    updated_at = int(item.get('updated_at') or 0)
    finished_at = int(item.get('finished_at') or 0) or None
    if finished_at is None and status_value in UNIFIED_TASK_TERMINAL_STATUSES:
        finished_at = updated_at or None

    progress_value: Optional[float] = None
    try:
        raw_progress = item.get('progress')
        progress_value = float(raw_progress) if raw_progress is not None else None
    except Exception:
        progress_value = None

    inferred_skill = material_packages_router._generation_skill_from_model(item.get('model'))
    task_skill_name = str(item.get('skill_name') or inferred_skill or 'unknown').strip().lower() or 'unknown'
    task_tool_name = str(item.get('tool_name') or 'material_packages.generate').strip() or 'material_packages.generate'

    return UnifiedTaskItem(
        id=task_id,
        provider=provider,
        provider_task_id=str(item.get('provider_task_id') or task_id),
        tool_name=task_tool_name,
        skill_name=task_skill_name,
        user_id=str(owner_user_id),
        user_name=str(owner_user_name),
        chat_id=item.get('chat_id'),
        model=item.get('model'),
        status=status_value,
        archive_status=archive_status,
        progress=progress_value,
        download_ready=bool(item.get('download_ready')),
        can_delete=can_delete,
        can_cancel=can_cancel,
        error_code=item.get('error_code'),
        error_message=item.get('error_message'),
        request_id=item.get('request_id'),
        deleted_at=int(item.get('deleted_at') or 0) or None,
        created_at=created_at,
        updated_at=updated_at,
        finished_at=finished_at,
        thumbnail_url=item.get('thumbnail_url'),
        video_preview_url=item.get('video_preview_url'),
        video_download_url=item.get('video_download_url'),
    )


def _build_unified_artifacts(owner_user_id: str, item: dict[str, Any]) -> list[UnifiedTaskArtifactItem]:
    task_id = str(item.get('task_id') or '').strip()
    if not task_id:
        return []

    created_at = int(item.get('archive_updated_at') or item.get('updated_at') or int(time.time()))
    rows: list[UnifiedTaskArtifactItem] = []

    video_relpath = str(item.get('archived_video_path') or '').strip()
    video_path = material_packages_router._task_file_from_relative(owner_user_id, video_relpath)
    if video_relpath and video_path:
        rows.append(
            UnifiedTaskArtifactItem(
                id=f'{task_id}:video',
                task_id=task_id,
                artifact_type='video',
                storage_backend='local',
                storage_path=video_relpath,
                mime_type=mimetypes.guess_type(video_path.name)[0] or 'video/mp4',
                bytes=int(video_path.stat().st_size),
                width=None,
                height=None,
                duration_ms=None,
                preview_ready=True,
                download_ready=bool(item.get('download_ready')),
                created_at=created_at,
            )
        )

    thumb_relpath = str(item.get('thumbnail_path') or '').strip()
    thumb_path = material_packages_router._task_file_from_relative(owner_user_id, thumb_relpath)
    if thumb_relpath and thumb_path:
        rows.append(
            UnifiedTaskArtifactItem(
                id=f'{task_id}:thumbnail',
                task_id=task_id,
                artifact_type='thumbnail',
                storage_backend='local',
                storage_path=thumb_relpath,
                mime_type=mimetypes.guess_type(thumb_path.name)[0] or 'image/jpeg',
                bytes=int(thumb_path.stat().st_size),
                width=None,
                height=None,
                duration_ms=None,
                preview_ready=True,
                download_ready=False,
                created_at=created_at,
            )
        )

    return rows


@router.post('/active/chats')
async def check_active_chats(request: Request, form_data: ActiveChatsForm, user=Depends(get_verified_user)):
    """Check which chat IDs have active tasks."""
    from open_webui.tasks import get_active_chat_ids

    active = await get_active_chat_ids(request.app.state.redis, form_data.chat_ids)
    return {'active_chat_ids': active}


@router.get('/config')
async def get_task_config(request: Request, user=Depends(get_verified_user)):
    return {
        'TASK_MODEL': request.app.state.config.TASK_MODEL,
        'TASK_MODEL_EXTERNAL': request.app.state.config.TASK_MODEL_EXTERNAL,
        'TITLE_GENERATION_PROMPT_TEMPLATE': request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE,
        'IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE': request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_AUTOCOMPLETE_GENERATION': request.app.state.config.ENABLE_AUTOCOMPLETE_GENERATION,
        'AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH': request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH,
        'TAGS_GENERATION_PROMPT_TEMPLATE': request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE,
        'FOLLOW_UP_GENERATION_PROMPT_TEMPLATE': request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_FOLLOW_UP_GENERATION': request.app.state.config.ENABLE_FOLLOW_UP_GENERATION,
        'ENABLE_TAGS_GENERATION': request.app.state.config.ENABLE_TAGS_GENERATION,
        'ENABLE_TITLE_GENERATION': request.app.state.config.ENABLE_TITLE_GENERATION,
        'ENABLE_SEARCH_QUERY_GENERATION': request.app.state.config.ENABLE_SEARCH_QUERY_GENERATION,
        'ENABLE_RETRIEVAL_QUERY_GENERATION': request.app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION,
        'QUERY_GENERATION_PROMPT_TEMPLATE': request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE,
        'TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE': request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
        'VOICE_MODE_PROMPT_TEMPLATE': request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE,
    }


class TaskConfigForm(BaseModel):
    TASK_MODEL: Optional[str]
    TASK_MODEL_EXTERNAL: Optional[str]
    ENABLE_TITLE_GENERATION: bool
    TITLE_GENERATION_PROMPT_TEMPLATE: str
    IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE: str
    ENABLE_AUTOCOMPLETE_GENERATION: bool
    AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH: int
    TAGS_GENERATION_PROMPT_TEMPLATE: str
    FOLLOW_UP_GENERATION_PROMPT_TEMPLATE: str
    ENABLE_FOLLOW_UP_GENERATION: bool
    ENABLE_TAGS_GENERATION: bool
    ENABLE_SEARCH_QUERY_GENERATION: bool
    ENABLE_RETRIEVAL_QUERY_GENERATION: bool
    QUERY_GENERATION_PROMPT_TEMPLATE: str
    TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE: str
    VOICE_MODE_PROMPT_TEMPLATE: Optional[str]


@router.post('/config/update')
async def update_task_config(request: Request, form_data: TaskConfigForm, user=Depends(get_admin_user)):
    request.app.state.config.TASK_MODEL = form_data.TASK_MODEL
    request.app.state.config.TASK_MODEL_EXTERNAL = form_data.TASK_MODEL_EXTERNAL
    request.app.state.config.ENABLE_TITLE_GENERATION = form_data.ENABLE_TITLE_GENERATION
    request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE = form_data.TITLE_GENERATION_PROMPT_TEMPLATE

    request.app.state.config.ENABLE_FOLLOW_UP_GENERATION = form_data.ENABLE_FOLLOW_UP_GENERATION
    request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE = form_data.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE

    request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE = form_data.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE

    request.app.state.config.ENABLE_AUTOCOMPLETE_GENERATION = form_data.ENABLE_AUTOCOMPLETE_GENERATION
    request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH = (
        form_data.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH
    )

    request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE = form_data.TAGS_GENERATION_PROMPT_TEMPLATE
    request.app.state.config.ENABLE_TAGS_GENERATION = form_data.ENABLE_TAGS_GENERATION
    request.app.state.config.ENABLE_SEARCH_QUERY_GENERATION = form_data.ENABLE_SEARCH_QUERY_GENERATION
    request.app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION = form_data.ENABLE_RETRIEVAL_QUERY_GENERATION

    request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE = form_data.QUERY_GENERATION_PROMPT_TEMPLATE
    request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE = form_data.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE

    request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE = form_data.VOICE_MODE_PROMPT_TEMPLATE

    return {
        'TASK_MODEL': request.app.state.config.TASK_MODEL,
        'TASK_MODEL_EXTERNAL': request.app.state.config.TASK_MODEL_EXTERNAL,
        'ENABLE_TITLE_GENERATION': request.app.state.config.ENABLE_TITLE_GENERATION,
        'TITLE_GENERATION_PROMPT_TEMPLATE': request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE,
        'IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE': request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_AUTOCOMPLETE_GENERATION': request.app.state.config.ENABLE_AUTOCOMPLETE_GENERATION,
        'AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH': request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH,
        'TAGS_GENERATION_PROMPT_TEMPLATE': request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_TAGS_GENERATION': request.app.state.config.ENABLE_TAGS_GENERATION,
        'ENABLE_FOLLOW_UP_GENERATION': request.app.state.config.ENABLE_FOLLOW_UP_GENERATION,
        'FOLLOW_UP_GENERATION_PROMPT_TEMPLATE': request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_SEARCH_QUERY_GENERATION': request.app.state.config.ENABLE_SEARCH_QUERY_GENERATION,
        'ENABLE_RETRIEVAL_QUERY_GENERATION': request.app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION,
        'QUERY_GENERATION_PROMPT_TEMPLATE': request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE,
        'TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE': request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
        'VOICE_MODE_PROMPT_TEMPLATE': request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE,
    }


@router.post('/title/completions')
async def generate_title(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not request.app.state.config.ENABLE_TITLE_GENERATION:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Title generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating chat title using model {task_model_id} for user {user.email} ')

    if request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE != '':
        template = request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE

    content = title_generation_template(template, form_data['messages'], user)

    max_tokens = models[task_model_id].get('info', {}).get('params', {}).get('max_tokens', 1000)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        **(
            {'max_tokens': max_tokens}
            if models[task_model_id].get('owned_by') == 'ollama'
            else {
                'max_completion_tokens': max_tokens,
            }
        ),
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.TITLE_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/follow_up/completions')
async def generate_follow_ups(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not request.app.state.config.ENABLE_FOLLOW_UP_GENERATION:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Follow-up generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating chat title using model {task_model_id} for user {user.email} ')

    if request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE != '':
        template = request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_FOLLOW_UP_GENERATION_PROMPT_TEMPLATE

    content = follow_up_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.FOLLOW_UP_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/tags/completions')
async def generate_chat_tags(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not request.app.state.config.ENABLE_TAGS_GENERATION:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Tags generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating chat tags using model {task_model_id} for user {user.email} ')

    if request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE != '':
        template = request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TAGS_GENERATION_PROMPT_TEMPLATE

    content = tags_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.TAGS_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error(f'Error generating chat completion: {e}')
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/image_prompt/completions')
async def generate_image_prompt(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating image prompt using model {task_model_id} for user {user.email} ')

    if request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE != '':
        template = request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE

    content = image_prompt_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.IMAGE_PROMPT_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/queries/completions')
async def generate_queries(request: Request, form_data: dict, user=Depends(get_verified_user)):
    type = form_data.get('type')
    if type == 'web_search':
        if not request.app.state.config.ENABLE_SEARCH_QUERY_GENERATION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.FEATURE_DISABLED('Search query generation'),
            )
    elif type == 'retrieval':
        if not request.app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.FEATURE_DISABLED('Query generation'),
            )

    if getattr(request.state, 'cached_queries', None):
        log.info(f'Reusing cached queries: {request.state.cached_queries}')
        return request.state.cached_queries

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating {type} queries using model {task_model_id} for user {user.email}')

    if (request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE).strip() != '':
        template = request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE

    content = query_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.QUERY_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )


@router.post('/auto/completions')
async def generate_autocompletion(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not request.app.state.config.ENABLE_AUTOCOMPLETE_GENERATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.FEATURE_DISABLED('Autocompletion generation'),
        )

    type = form_data.get('type')
    prompt = form_data.get('prompt')
    messages = form_data.get('messages')

    if request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH > 0:
        if len(prompt) > request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.INPUT_TOO_LONG(request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH),
            )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating autocompletion using model {task_model_id} for user {user.email}')

    if (request.app.state.config.AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE).strip() != '':
        template = request.app.state.config.AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE

    content = autocomplete_generation_template(template, prompt, messages, type, user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.AUTOCOMPLETE_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error(f'Error generating chat completion: {e}')
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/emoji/completions')
async def generate_emoji(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating emoji using model {task_model_id} for user {user.email} ')

    template = DEFAULT_EMOJI_GENERATION_PROMPT_TEMPLATE

    content = emoji_generation_template(template, form_data['prompt'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        **(
            {'max_tokens': 4}
            if models[task_model_id].get('owned_by') == 'ollama'
            else {
                'max_completion_tokens': 4,
            }
        ),
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.EMOJI_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )


@router.post('/moa/completions')
async def generate_moa_response(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']

    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    template = DEFAULT_MOA_GENERATION_PROMPT_TEMPLATE

    content = moa_response_generation_template(
        template,
        form_data['prompt'],
        form_data['responses'],
    )

    payload = {
        'model': model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': form_data.get('stream', False),
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'chat_id': form_data.get('chat_id', None),
            'task': str(TASKS.MOA_RESPONSE_GENERATION),
            'task_body': form_data,
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )


@router.get('/', response_model=UnifiedTaskListResponse)
async def list_unified_tasks(
    user_id: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    skill_name: Optional[str] = Query(default=None),
    tool_name: Optional[str] = Query(default=None),
    task_status: Optional[str] = Query(default=None, alias='status'),
    model: Optional[str] = Query(default=None),
    include_deleted: bool = Query(default=False),
    refresh_status: bool = Query(default=True),
    refresh_min_interval_seconds: int = Query(default=5, ge=0, le=600),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=48, ge=1, le=200),
    user: UserModel = Depends(get_verified_user),
):
    material_packages_router._cleanup_expired_soft_deleted_records()

    desired_user = (user_id or '').strip() if user_id else None
    desired_provider = (provider or '').strip().lower() if provider else None
    desired_skill_name = (skill_name or '').strip().lower() if skill_name else None
    desired_tool_name = (tool_name or '').strip().lower() if tool_name else None
    desired_model = (model or '').strip().lower() if model else None
    desired_status = _parse_status_filter(task_status)

    user_name_cache: dict[str, str] = {}
    rows: list[UnifiedTaskItem] = []

    for owner_user_id, path in material_packages_router._iter_task_record_paths():
        if desired_user and str(owner_user_id) != desired_user:
            continue

        item = material_packages_router._load_task_record_from_path(path)
        if item is None:
            continue

        changed = material_packages_router._normalize_task_defaults(item, owner_user_id=owner_user_id)
        if refresh_status and material_packages_router._should_refresh_task_status(item, refresh_min_interval_seconds):
            item = await material_packages_router._refresh_task_record_from_ark(
                owner_user_id,
                item,
                timeout_seconds=120,
            )
            changed = True

        item = await material_packages_router._archive_task_record_if_needed(owner_user_id, item)
        if material_packages_router._normalize_task_defaults(item, owner_user_id=owner_user_id):
            changed = True

        task_id = str(item.get('task_id') or path.stem)
        if changed:
            material_packages_router._save_task_record(owner_user_id, task_id, item)

        if not include_deleted and material_packages_router._is_soft_deleted(item):
            continue

        task_provider = str(item.get('provider') or 'ark').strip().lower()
        if desired_provider and task_provider != desired_provider:
            continue

        task_skill_name = str(item.get('skill_name') or 'seedance').strip().lower()
        if desired_skill_name and task_skill_name != desired_skill_name:
            continue

        task_tool_name = str(item.get('tool_name') or 'material_packages.generate').strip().lower()
        if desired_tool_name and task_tool_name != desired_tool_name:
            continue

        task_model = str(item.get('model') or '').strip().lower()
        if desired_model and task_model != desired_model:
            continue

        status_value = _normalize_unified_status(item.get('status'))
        if desired_status:
            if desired_status == 'UNKNOWN':
                raw_status = str(item.get('status') or '').strip().upper()
                raw_alias = UNIFIED_TASK_STATUS_ALIASES.get(raw_status, raw_status)
                if raw_alias in {'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED'}:
                    continue
            elif status_value != desired_status:
                continue

        owner_user_name = str(item.get('user_name') or '').strip()
        if not owner_user_name:
            owner_user_name = await material_packages_router._resolve_user_name(owner_user_id, user_name_cache)
            item['user_name'] = owner_user_name
            material_packages_router._save_task_record(owner_user_id, task_id, item)

        rows.append(
            _to_unified_task_item(
                owner_user_id=owner_user_id,
                owner_user_name=owner_user_name,
                item=item,
                requester=user,
            )
        )

    rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
    total = len(rows)
    paged_rows = rows[offset : offset + limit]
    return UnifiedTaskListResponse(items=paged_rows, total=total, offset=offset, limit=limit)


@router.get('/users', response_model=UnifiedTaskUsersResponse)
async def list_unified_task_users(
    include_deleted: bool = Query(default=False),
    user: UserModel = Depends(get_verified_user),
):
    _ = user
    material_packages_router._cleanup_expired_soft_deleted_records()

    user_name_cache: dict[str, str] = {}
    seen: set[str] = set()
    rows: list[UnifiedTaskUserItem] = []

    for owner_user_id, path in material_packages_router._iter_task_record_paths():
        if owner_user_id in seen:
            continue

        item = material_packages_router._load_task_record_from_path(path)
        if item is None:
            continue
        if not include_deleted and material_packages_router._is_soft_deleted(item):
            continue

        owner_user_name = str(item.get('user_name') or '').strip()
        if not owner_user_name:
            owner_user_name = await material_packages_router._resolve_user_name(owner_user_id, user_name_cache)

        rows.append(UnifiedTaskUserItem(user_id=str(owner_user_id), user_name=owner_user_name))
        seen.add(owner_user_id)

    rows.sort(key=lambda row: row.user_name.lower())
    return UnifiedTaskUsersResponse(users=rows)


@router.get('/providers', response_model=UnifiedTaskProvidersResponse)
async def list_unified_task_providers(
    user_id: Optional[str] = Query(default=None),
    include_deleted: bool = Query(default=False),
    user: UserModel = Depends(get_verified_user),
):
    _ = user
    material_packages_router._cleanup_expired_soft_deleted_records()

    desired_user = (user_id or '').strip() if user_id else None
    seen: set[str] = set()

    for owner_user_id, path in material_packages_router._iter_task_record_paths():
        if desired_user and str(owner_user_id) != desired_user:
            continue

        item = material_packages_router._load_task_record_from_path(path)
        if item is None:
            continue
        if not include_deleted and material_packages_router._is_soft_deleted(item):
            continue

        provider_value = str(item.get('provider') or 'ark').strip().lower() or 'ark'
        seen.add(provider_value)

    providers = sorted(seen)
    ordered: list[str] = []
    for key in ('ark', 'happyhorse'):
        if key in providers:
            ordered.append(key)
            providers.remove(key)
    ordered.extend(providers)

    return UnifiedTaskProvidersResponse(providers=ordered)


@router.get('/{task_id}/preview', response_model=UnifiedTaskPreviewResponse)
async def get_unified_task_preview(
    task_id: str,
    refresh_status: bool = Query(default=True),
    user: UserModel = Depends(get_verified_user),
):
    owner_user_id, item = await material_packages_router._load_task_for_read(
        task_id,
        refresh_status=refresh_status,
        refresh_min_interval_seconds=5,
    )
    task_row = _to_unified_task_item(
        owner_user_id=owner_user_id,
        owner_user_name=str(item.get('user_name') or owner_user_id),
        item=item,
        requester=user,
    )
    return UnifiedTaskPreviewResponse(
        task_id=task_row.id,
        status=task_row.status,
        archive_status=task_row.archive_status,
        download_ready=task_row.download_ready,
        can_delete=task_row.can_delete,
        thumbnail_url=task_row.thumbnail_url,
        video_preview_url=task_row.video_preview_url,
    )


@router.get('/{task_id}/download')
async def download_unified_task(task_id: str, user: UserModel = Depends(get_verified_user)):
    _ = user
    owner_user_id, item = await material_packages_router._load_task_for_read(task_id, refresh_status=False)
    if not bool(item.get('download_ready')):
        raise HTTPException(status_code=409, detail='ArchiveNotReady')

    video_path = material_packages_router._task_file_from_relative(owner_user_id, item.get('archived_video_path'))
    if not video_path:
        raise HTTPException(status_code=404, detail='Archived video not found')

    media_type = mimetypes.guess_type(video_path.name)[0] or 'video/mp4'
    return FileResponse(path=video_path, media_type=media_type, filename=video_path.name)


@router.post('/{task_id}/archive/retry')
async def retry_unified_task_archive(task_id: str, user: UserModel = Depends(get_verified_user)):
    owner_user_id, item = await material_packages_router._load_task_for_read(task_id, refresh_status=True)
    if not material_packages_router._task_delete_allowed(user, owner_user_id):
        raise HTTPException(status_code=403, detail='No permission to retry archive')

    item['archive_status'] = material_packages_router.ARCHIVE_STATUS_PENDING
    item['archive_error'] = None
    item['archive_updated_at'] = int(time.time())
    material_packages_router._save_task_record(owner_user_id, str(item.get('task_id') or task_id), item)
    item = await material_packages_router._archive_task_record_if_needed(owner_user_id, item, force_retry=True)

    return {
        'ok': True,
        'task_id': str(item.get('task_id') or task_id),
        'archive_status': _normalize_archive_status(item.get('archive_status')),
    }


@router.delete('/{task_id}')
async def soft_delete_unified_task(
    task_id: str,
    delete_reason: Optional[str] = Query(default=None, max_length=200),
    user: UserModel = Depends(get_verified_user),
):
    owner_user_id, item = await material_packages_router._load_task_for_read(
        task_id,
        include_deleted=True,
        refresh_status=False,
    )
    if not material_packages_router._task_delete_allowed(user, owner_user_id):
        raise HTTPException(status_code=403, detail='No permission to delete this task')

    now = int(time.time())
    item['deleted_at'] = now
    item['deleted_by'] = str(user.id)
    item['delete_reason'] = (delete_reason or '').strip() or None
    item['updated_at'] = now
    material_packages_router._save_task_record(owner_user_id, str(item.get('task_id') or task_id), item)

    return {
        'ok': True,
        'task_id': str(item.get('task_id') or task_id),
        'deleted_at': now,
    }


@router.post('/{task_id}/cancel')
async def cancel_unified_task(task_id: str, user: UserModel = Depends(get_verified_user)):
    owner_user_id, item = await material_packages_router._load_task_for_read(task_id, refresh_status=False)
    if not material_packages_router._task_delete_allowed(user, owner_user_id):
        raise HTTPException(status_code=403, detail='No permission to cancel this task')

    status_value = _normalize_unified_status(item.get('status'))
    if status_value in UNIFIED_TASK_TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                'ok': False,
                'error_code': 'NotCancelable',
                'error_message': 'Task is already in terminal status',
                'request_id': item.get('request_id'),
            },
        )

    provider = str(item.get('provider') or 'ark').strip().lower()
    if not _provider_cancel_supported(provider):
        raise HTTPException(
            status_code=409,
            detail={
                'ok': False,
                'error_code': 'ProviderNotCancelable',
                'error_message': 'Provider does not support task cancellation',
                'request_id': item.get('request_id'),
            },
        )

    # Placeholder for provider cancel flow in a later phase.
    raise HTTPException(
        status_code=409,
        detail={
            'ok': False,
            'error_code': 'ProviderNotCancelable',
            'error_message': 'Provider cancellation is not enabled yet',
            'request_id': item.get('request_id'),
        },
    )


@router.get('/{task_id}', response_model=UnifiedTaskDetailResponse)
async def get_unified_task_detail(
    task_id: str,
    refresh_status: bool = Query(default=True),
    refresh_min_interval_seconds: int = Query(default=5, ge=0, le=600),
    user: UserModel = Depends(get_verified_user),
):
    owner_user_id, item = await material_packages_router._load_task_for_read(
        task_id,
        refresh_status=refresh_status,
        refresh_min_interval_seconds=refresh_min_interval_seconds,
    )

    owner_user_name = str(item.get('user_name') or '').strip() or str(owner_user_id)
    task_row = _to_unified_task_item(
        owner_user_id=owner_user_id,
        owner_user_name=owner_user_name,
        item=item,
        requester=user,
    )
    artifacts = _build_unified_artifacts(owner_user_id, item)
    return UnifiedTaskDetailResponse(task=task_row, artifacts=artifacts)

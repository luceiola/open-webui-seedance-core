import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from tempfile import mkdtemp
from typing import Optional

import pytest
from fastapi import HTTPException
from pydantic import BaseModel


REPO_ROOT = Path(__file__).resolve().parents[6]
TASKS_ROUTER_PATH = REPO_ROOT / 'backend' / 'open_webui' / 'routers' / 'tasks.py'
MATERIAL_PACKAGES_ROUTER_PATH = REPO_ROOT / 'backend' / 'open_webui' / 'routers' / 'material_packages.py'


class StubUserModel(BaseModel):
    id: str
    role: str = 'user'
    name: str = 'stub-user'
    username: Optional[str] = None
    email: str = 'stub@example.com'


def _load_module_with_stubs(module_name: str, file_path: Path, stubs: dict[str, types.ModuleType]):
    originals: dict[str, Optional[types.ModuleType]] = {name: sys.modules.get(name) for name in stubs}

    for name, stub in stubs.items():
        sys.modules[name] = stub

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f'Unable to load module: {file_path}')
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name in stubs:
            previous = originals[name]
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _build_tasks_router_fixture():
    open_webui_pkg = types.ModuleType('open_webui')
    open_webui_pkg.__path__ = []
    utils_pkg = types.ModuleType('open_webui.utils')
    utils_pkg.__path__ = []
    routers_pkg = types.ModuleType('open_webui.routers')
    routers_pkg.__path__ = []
    models_pkg = types.ModuleType('open_webui.models')
    models_pkg.__path__ = []

    chat_module = types.ModuleType('open_webui.utils.chat')

    async def _dummy_chat_completion(*args, **kwargs):
        return {'ok': True}

    chat_module.generate_chat_completion = _dummy_chat_completion

    task_utils_module = types.ModuleType('open_webui.utils.task')
    task_utils_module.title_generation_template = lambda *args, **kwargs: ''
    task_utils_module.follow_up_generation_template = lambda *args, **kwargs: ''
    task_utils_module.query_generation_template = lambda *args, **kwargs: ''
    task_utils_module.image_prompt_generation_template = lambda *args, **kwargs: ''
    task_utils_module.autocomplete_generation_template = lambda *args, **kwargs: ''
    task_utils_module.tags_generation_template = lambda *args, **kwargs: ''
    task_utils_module.emoji_generation_template = lambda *args, **kwargs: ''
    task_utils_module.moa_response_generation_template = lambda *args, **kwargs: ''
    task_utils_module.get_task_model_id = lambda *args, **kwargs: 'stub-model'

    auth_module = types.ModuleType('open_webui.utils.auth')
    auth_module.get_admin_user = lambda: StubUserModel(id='admin-1', role='admin')
    auth_module.get_verified_user = lambda: StubUserModel(id='user-1', role='user')

    constants_module = types.ModuleType('open_webui.constants')

    class _ErrorMessages:
        @staticmethod
        def MODEL_NOT_FOUND():
            return 'model not found'

    class _Tasks:
        def __getattr__(self, name):
            return name

    constants_module.ERROR_MESSAGES = _ErrorMessages()
    constants_module.TASKS = _Tasks()

    users_module = types.ModuleType('open_webui.models.users')
    users_module.UserModel = StubUserModel

    material_module = types.ModuleType('open_webui.routers.material_packages')
    material_module.ARCHIVE_STATUS_PENDING = 'PENDING'
    material_module.ARCHIVE_STATUS_NOT_REQUIRED = 'NOT_REQUIRED'
    material_module._cleanup_expired_soft_deleted_records = lambda: None
    material_module._iter_task_record_paths = lambda: []
    material_module._load_task_record_from_path = lambda path: None
    material_module._normalize_task_defaults = lambda item, owner_user_id: False
    material_module._should_refresh_task_status = lambda item, refresh_min_interval_seconds: False

    async def _refresh_task_record_from_ark(owner_user_id, item, timeout_seconds=120):
        return item

    async def _archive_task_record_if_needed(owner_user_id, item, force_retry=False):
        return item

    async def _resolve_user_name(owner_user_id, user_name_cache):
        return str(owner_user_id)

    async def _load_task_for_read(task_id, **kwargs):
        return 'user-1', {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'archive_status': 'NOT_REQUIRED',
            'download_ready': False,
            'created_at': 1,
            'updated_at': 1,
        }

    material_module._refresh_task_record_from_ark = _refresh_task_record_from_ark
    material_module._archive_task_record_if_needed = _archive_task_record_if_needed
    material_module._resolve_user_name = _resolve_user_name
    material_module._load_task_for_read = _load_task_for_read
    material_module._save_task_record = lambda owner_user_id, task_id, item: None
    material_module._is_soft_deleted = lambda item: False
    material_module._task_delete_allowed = (
        lambda requester, owner_user_id: requester.role == 'admin' or str(requester.id) == str(owner_user_id)
    )
    material_module._task_file_from_relative = lambda owner_user_id, relative_path: None
    material_module._generation_skill_from_model = lambda model: 'unknown'

    pipelines_module = types.ModuleType('open_webui.routers.pipelines')

    async def _process_pipeline_inlet_filter(request, payload, user, models):
        return payload

    pipelines_module.process_pipeline_inlet_filter = _process_pipeline_inlet_filter

    config_module = types.ModuleType('open_webui.config')
    config_module.DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE = 'title'
    config_module.DEFAULT_FOLLOW_UP_GENERATION_PROMPT_TEMPLATE = 'followup'
    config_module.DEFAULT_TAGS_GENERATION_PROMPT_TEMPLATE = 'tags'
    config_module.DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE = 'image'
    config_module.DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE = 'query'
    config_module.DEFAULT_AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE = 'autocomplete'
    config_module.DEFAULT_EMOJI_GENERATION_PROMPT_TEMPLATE = 'emoji'
    config_module.DEFAULT_MOA_GENERATION_PROMPT_TEMPLATE = 'moa'
    config_module.DEFAULT_VOICE_MODE_PROMPT_TEMPLATE = 'voice'

    stubs = {
        'open_webui': open_webui_pkg,
        'open_webui.utils': utils_pkg,
        'open_webui.utils.chat': chat_module,
        'open_webui.utils.task': task_utils_module,
        'open_webui.utils.auth': auth_module,
        'open_webui.constants': constants_module,
        'open_webui.models': models_pkg,
        'open_webui.models.users': users_module,
        'open_webui.routers': routers_pkg,
        'open_webui.routers.material_packages': material_module,
        'open_webui.routers.pipelines': pipelines_module,
        'open_webui.config': config_module,
    }

    module = _load_module_with_stubs('test_open_webui_routers_tasks_v113', TASKS_ROUTER_PATH, stubs)
    return module, material_module


def _build_material_packages_router_fixture(tmp_path: Path):
    open_webui_pkg = types.ModuleType('open_webui')
    open_webui_pkg.__path__ = []
    models_pkg = types.ModuleType('open_webui.models')
    models_pkg.__path__ = []
    storage_pkg = types.ModuleType('open_webui.storage')
    storage_pkg.__path__ = []
    utils_pkg = types.ModuleType('open_webui.utils')
    utils_pkg.__path__ = []

    config_module = types.ModuleType('open_webui.config')
    config_module.CACHE_DIR = Path(mkdtemp(prefix='owui-cache-', dir=str(tmp_path)))

    files_module = types.ModuleType('open_webui.models.files')

    class _Files:
        @staticmethod
        async def get_file_by_id(file_id):
            return None

    files_module.Files = _Files

    users_module = types.ModuleType('open_webui.models.users')
    users_module.UserModel = StubUserModel

    class _Users:
        @staticmethod
        async def get_user_by_id(user_id):
            return None

    users_module.Users = _Users

    storage_provider_module = types.ModuleType('open_webui.storage.provider')

    class _Storage:
        @staticmethod
        def upload_file(*args, **kwargs):
            return None

    storage_provider_module.Storage = _Storage

    auth_module = types.ModuleType('open_webui.utils.auth')
    auth_module.get_verified_user = lambda: StubUserModel(id='user-1', role='user')

    stubs = {
        'open_webui': open_webui_pkg,
        'open_webui.models': models_pkg,
        'open_webui.storage': storage_pkg,
        'open_webui.utils': utils_pkg,
        'open_webui.config': config_module,
        'open_webui.models.files': files_module,
        'open_webui.models.users': users_module,
        'open_webui.storage.provider': storage_provider_module,
        'open_webui.utils.auth': auth_module,
    }

    module = _load_module_with_stubs(
        'test_open_webui_routers_material_packages_v113',
        MATERIAL_PACKAGES_ROUTER_PATH,
        stubs,
    )
    return module


@pytest.fixture
def tasks_router_module():
    return _build_tasks_router_fixture()


@pytest.fixture
def material_packages_router_module(tmp_path):
    return _build_material_packages_router_fixture(tmp_path)


def _run(coro):
    return asyncio.run(coro)


def test_unified_tasks_list_contract_and_unknown_status_filter(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    task_records = {
        'task-known': {
            'task_id': 'task-known',
            'provider': 'ark',
            'status': 'SUBMITTED',
            'archive_status': 'NOT_REQUIRED',
            'model': 'doubao-seedance-1',
            'created_at': 100,
            'updated_at': 200,
            'download_ready': False,
            'user_name': 'Alice',
        },
        'task-unknown': {
            'task_id': 'task-unknown',
            'provider': 'happyhorse',
            'status': 'MYSTERY_STATUS',
            'archive_status': 'NOT_REQUIRED',
            'model': 'doubao-happyhorse-1',
            'created_at': 300,
            'updated_at': 400,
            'download_ready': False,
            'user_name': 'Bob',
        },
    }

    material_stub._iter_task_record_paths = lambda: [
        ('user-1', Path('/tmp/task-known.json')),
        ('user-2', Path('/tmp/task-unknown.json')),
    ]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._normalize_task_defaults = lambda item, owner_user_id: False
    material_stub._should_refresh_task_status = lambda item, refresh_min_interval_seconds: False
    material_stub._is_soft_deleted = lambda item: False
    material_stub._generation_skill_from_model = (
        lambda model: 'happyhorse' if 'happyhorse' in str(model).lower() else 'seedance'
    )

    requester = StubUserModel(id='admin-1', role='admin')
    response = _run(
        tasks_module.list_unified_tasks(
            user_id=None,
            provider=None,
            skill_name=None,
            tool_name=None,
            task_status='UNKNOWN',
            model=None,
            include_deleted=False,
            refresh_status=False,
            refresh_min_interval_seconds=5,
            offset=0,
            limit=48,
            user=requester,
        )
    )

    payload = response.model_dump()
    assert set(payload.keys()) == {'items', 'total', 'offset', 'limit'}
    assert response.total == 1
    assert response.offset == 0
    assert response.limit == 48
    assert len(response.items) == 1
    assert response.items[0].id == 'task-unknown'
    assert response.items[0].provider == 'happyhorse'
    # UNKNOWN filter includes non-standard raw statuses; normalized output remains RUNNING.
    assert response.items[0].status == 'RUNNING'


def test_unified_task_providers_endpoint_orders_providers(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    task_records = {
        'task-1': {'task_id': 'task-1', 'provider': 'happyhorse'},
        'task-2': {'task_id': 'task-2', 'provider': 'zzz'},
        'task-3': {'task_id': 'task-3', 'provider': 'ark'},
    }

    material_stub._iter_task_record_paths = lambda: [
        ('user-1', Path('/tmp/task-1.json')),
        ('user-2', Path('/tmp/task-2.json')),
        ('user-3', Path('/tmp/task-3.json')),
    ]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._is_soft_deleted = lambda item: False

    requester = StubUserModel(id='admin-1', role='admin')
    response = _run(tasks_module.list_unified_task_providers(user_id=None, include_deleted=False, user=requester))
    assert response.providers == ['ark', 'happyhorse', 'zzz']


def test_unified_task_download_returns_409_when_archive_not_ready(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    async def _load_task_for_read(task_id, **kwargs):
        return 'user-1', {
            'task_id': task_id,
            'status': 'RUNNING',
            'archive_status': 'PENDING',
            'download_ready': False,
        }

    material_stub._load_task_for_read = _load_task_for_read

    requester = StubUserModel(id='user-1', role='user')
    with pytest.raises(HTTPException) as exc_info:
        _run(tasks_module.download_unified_task(task_id='task-1', user=requester))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == 'ArchiveNotReady'


def test_happyhorse_generate_rejects_non_image_reference(material_packages_router_module):
    material_module = material_packages_router_module

    manifest = {
        'id': 'pkg-1',
        'user_id': 'user-1',
        'chat_id': 'chat-1',
        'assets': [
            {
                'reference_name': '01_FR1.mp4',
                'filename': '01_FR1.mp4',
                'relative_path': '01_FR1.mp4',
                'media_type': 'video',
                'size_bytes': 1024,
            }
        ],
        'updated_at': 100,
    }

    material_module._load_manifest = lambda path: manifest
    material_module._get_tos_context = lambda: object()
    material_module._get_ark_base_url = lambda: 'https://ark.example.com/api/v3'
    material_module._get_ark_headers = lambda: {'Authorization': 'Bearer stub'}

    requester = StubUserModel(id='user-1', role='user', name='User 1')
    form_data = material_module.GenerateWithPackageRequest(
        prompt='请参考 @01_FR1.mp4 生成视频',
        model='doubao-happyhorse-v1',
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(material_module.generate_with_material_package('pkg-1', form_data, requester))

    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get('error_code') == 'ModelConstraintViolation'
    assert exc_info.value.detail.get('details', {}).get('actual') == 'video'

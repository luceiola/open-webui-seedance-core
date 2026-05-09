import asyncio
import importlib.util
import json
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
    groups_module = types.ModuleType('open_webui.models.groups')

    class _Groups:
        @staticmethod
        async def get_all_groups(db=None):
            return []

        @staticmethod
        async def get_groups_by_member_id(user_id, db=None):
            return []

        @staticmethod
        async def get_groups_by_member_ids(user_ids, db=None):
            return {user_id: [] for user_id in user_ids}

    groups_module.Groups = _Groups

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
        'open_webui.models.groups': groups_module,
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

    groups_module = types.ModuleType('open_webui.models.groups')

    class _Groups:
        @staticmethod
        async def get_groups_by_member_id(user_id, db=None):
            return []

    groups_module.Groups = _Groups

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
        'open_webui.models.groups': groups_module,
        'open_webui.models.users': users_module,
        'open_webui.storage.provider': storage_provider_module,
        'open_webui.utils.auth': auth_module,
    }

    module = _load_module_with_stubs(
        'test_open_webui_routers_material_packages_v113',
        MATERIAL_PACKAGES_ROUTER_PATH,
        stubs,
    )
    controlled_env = config_module.CACHE_DIR / 'test.env'
    controlled_env.write_text('', encoding='utf-8')
    module.ARK_ENV_FILE_CANDIDATES = [controlled_env]
    return module


@pytest.fixture
def tasks_router_module():
    return _build_tasks_router_fixture()


@pytest.fixture
def material_packages_router_module(tmp_path):
    return _build_material_packages_router_fixture(tmp_path)


def _run(coro):
    return asyncio.run(coro)


def _set_key_routing_config(material_module, config_path: Path):
    material_module.KEY_ROUTING_CONFIG_PATH = config_path.resolve()
    material_module._KEY_ROUTING_CACHE_PATH = None
    material_module._KEY_ROUTING_CACHE_MTIME_NS = None
    material_module._KEY_ROUTING_CACHE_DATA = {}


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


def test_unified_tasks_list_filters_by_group_and_time(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    task_records = {
        'task-1': {
            'task_id': 'task-1',
            'provider': 'ark',
            'status': 'RUNNING',
            'archive_status': 'NOT_REQUIRED',
            'model': 'doubao-seedance-1',
            'created_at': 120,
            'updated_at': 130,
            'download_ready': False,
            'user_name': 'User 1',
        },
        'task-2': {
            'task_id': 'task-2',
            'provider': 'ark',
            'status': 'RUNNING',
            'archive_status': 'NOT_REQUIRED',
            'model': 'doubao-seedance-1',
            'created_at': 320,
            'updated_at': 330,
            'download_ready': False,
            'user_name': 'User 2',
        },
    }

    material_stub._iter_task_record_paths = lambda: [
        ('user-1', Path('/tmp/task-1.json')),
        ('user-2', Path('/tmp/task-2.json')),
    ]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._normalize_task_defaults = lambda item, owner_user_id: False
    material_stub._should_refresh_task_status = lambda item, refresh_min_interval_seconds: False
    material_stub._is_soft_deleted = lambda item: False
    material_stub._generation_skill_from_model = lambda model: 'seedance'

    async def _get_groups_by_member_ids(user_ids, db=None):
        _ = db
        mapping = {
            'user-1': [types.SimpleNamespace(id='g-a', name='A')],
            'user-2': [types.SimpleNamespace(id='g-b', name='B')],
        }
        return {user_id: mapping.get(user_id, []) for user_id in user_ids}

    tasks_module.Groups.get_groups_by_member_ids = staticmethod(_get_groups_by_member_ids)

    requester = StubUserModel(id='admin-1', role='admin')
    response = _run(
        tasks_module.list_unified_tasks(
            user_id=None,
            provider=None,
            skill_name=None,
            tool_name=None,
            task_status=None,
            model=None,
            group_id='g-b',
            start_at=300,
            end_at=500,
            include_deleted=False,
            refresh_status=False,
            refresh_min_interval_seconds=5,
            offset=0,
            limit=48,
            user=requester,
        )
    )

    assert response.total == 1
    assert len(response.items) == 1
    assert response.items[0].id == 'task-2'


def test_unified_tasks_list_rejects_invalid_time_range(tasks_router_module):
    tasks_module, material_stub = tasks_router_module
    material_stub._iter_task_record_paths = lambda: []

    requester = StubUserModel(id='admin-1', role='admin')
    with pytest.raises(HTTPException) as exc_info:
        _run(
            tasks_module.list_unified_tasks(
                user_id=None,
                provider=None,
                skill_name=None,
                tool_name=None,
                task_status=None,
                model=None,
                group_id=None,
                start_at=500,
                end_at=100,
                include_deleted=False,
                refresh_status=False,
                refresh_min_interval_seconds=5,
                offset=0,
                limit=48,
                user=requester,
            )
        )

    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get('code') == 'INVALID_TIME_RANGE'
    assert exc_info.value.detail.get('message') == 'start_at must be <= end_at'


def test_unified_tasks_groups_endpoint_returns_group_names(tasks_router_module):
    tasks_module, _material_stub = tasks_router_module

    async def _get_all_groups(db=None):
        _ = db
        return [
            types.SimpleNamespace(id='g-2', name='Beta'),
            types.SimpleNamespace(id='g-1', name='Alpha'),
        ]

    tasks_module.Groups.get_all_groups = staticmethod(_get_all_groups)

    requester = StubUserModel(id='user-1', role='user')
    response = _run(tasks_module.list_unified_task_groups(user=requester))

    assert [row.group_id for row in response.groups] == ['g-1', 'g-2']
    assert [row.group_name for row in response.groups] == ['Alpha', 'Beta']


def test_unified_tasks_prompt_fields_are_backward_compatible(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    task_records = {
        'task-modern': {
            'task_id': 'task-modern',
            'provider': 'ark',
            'status': 'SUCCEEDED',
            'archive_status': 'SUCCEEDED',
            'created_at': 200,
            'updated_at': 210,
            'download_ready': True,
            'user_name': 'Alice',
            'prompt_text': '请参考 @01_FR1.png 生成',
            'generation_params': {'model': 'doubao-seedance-1', 'duration': 5},
            'prompt_resources': [
                {'name': '01_FR1.png', 'url': 'https://example.com/01_FR1.png'},
                {'name': 'bad', 'url': 'ftp://invalid'},
            ],
        },
        'task-legacy': {
            'task_id': 'task-legacy',
            'provider': 'ark',
            'status': 'SUCCEEDED',
            'archive_status': 'SUCCEEDED',
            'created_at': 100,
            'updated_at': 110,
            'download_ready': True,
            'user_name': 'Bob',
        },
    }

    material_stub._iter_task_record_paths = lambda: [
        ('user-1', Path('/tmp/task-modern.json')),
        ('user-2', Path('/tmp/task-legacy.json')),
    ]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._normalize_task_defaults = lambda item, owner_user_id: False
    material_stub._should_refresh_task_status = lambda item, refresh_min_interval_seconds: False
    material_stub._is_soft_deleted = lambda item: False
    material_stub._generation_skill_from_model = lambda model: 'seedance'

    requester = StubUserModel(id='admin-1', role='admin')
    response = _run(
        tasks_module.list_unified_tasks(
            user_id=None,
            provider=None,
            skill_name=None,
            tool_name=None,
            task_status=None,
            model=None,
            group_id=None,
            start_at=None,
            end_at=None,
            include_deleted=False,
            refresh_status=False,
            refresh_min_interval_seconds=5,
            offset=0,
            limit=48,
            user=requester,
        )
    )

    assert response.total == 2
    assert response.items[0].id == 'task-modern'
    assert response.items[0].prompt_text == '请参考 @01_FR1.png 生成'
    assert response.items[0].generation_params == {'model': 'doubao-seedance-1', 'duration': 5}
    assert response.items[0].prompt_resources == [{'name': '01_FR1.png', 'url': 'https://example.com/01_FR1.png'}]

    assert response.items[1].id == 'task-legacy'
    assert response.items[1].prompt_text is None
    assert response.items[1].generation_params is None
    assert response.items[1].prompt_resources == []


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


def test_archive_succeeded_task_does_not_redownload(material_packages_router_module):
    material_module = material_packages_router_module
    user_id = 'user-1'
    task_id = 'task-1'

    video_relpath = f'task_archives/{task_id}.mp4'
    thumb_relpath = f'task_thumbnails/{task_id}.jpg'
    user_root = material_module._user_root_dir(user_id)
    video_path = user_root / video_relpath
    thumb_path = user_root / thumb_relpath
    video_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b'fake-video')
    thumb_path.write_bytes(b'fake-thumb')

    task_record = {
        'task_id': task_id,
        'user_id': user_id,
        'status': 'SUCCEEDED',
        'archive_status': 'SUCCEEDED',
        'archive_retry_count': 0,
        'archive_updated_at': 1,
        'created_at': 1,
        'updated_at': 1,
        'artifact_kind': 'video',
        'video_url': 'https://example.com/video.mp4',
        'archived_video_path': video_relpath,
        'thumbnail_path': thumb_relpath,
        'download_ready': True,
        'video_download_url': material_module._build_task_download_url(task_id),
        'video_preview_url': material_module._build_task_preview_url(task_id),
        'thumbnail_url': material_module._build_task_thumbnail_url(task_id),
    }

    calls = {'download': 0}

    async def _download_video_file(*args, **kwargs):
        calls['download'] += 1

    material_module._download_video_file = _download_video_file

    result = _run(material_module._archive_task_record_if_needed(user_id, dict(task_record)))

    assert calls['download'] == 0
    assert result['archive_status'] == 'SUCCEEDED'
    assert int(result.get('archive_retry_count') or 0) == 0
    assert result['download_ready'] is True
    assert result['video_download_url'] == material_module._build_task_download_url(task_id)
    assert result['video_preview_url'] == material_module._build_task_preview_url(task_id)
    assert result['thumbnail_url'] == material_module._build_task_thumbnail_url(task_id)


def test_key_routing_alias_raises_on_multi_group_conflict(material_packages_router_module):
    material_module = material_packages_router_module
    provider_config = {
        'strict_single_group': True,
        'bindings': [
            {'group_id': 'g1', 'alias': 'k1', 'priority': 100},
            {'group_id': 'g2', 'alias': 'k2', 'priority': 100},
        ],
    }

    with pytest.raises(HTTPException) as exc_info:
        material_module._resolve_key_routing_alias(
            provider='seedance',
            provider_config=provider_config,
            user_group_ids={'g1', 'g2'},
            user_group_names=set(),
        )

    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get('code') == material_module.KEY_ROUTING_ERROR_MULTI_GROUP


def test_key_routing_alias_uses_default_when_no_group_match(material_packages_router_module):
    material_module = material_packages_router_module
    provider_config = {
        'strict_single_group': True,
        'default_alias': 'k-default',
        'bindings': [
            {'group_id': 'g1', 'alias': 'k1'},
        ],
    }

    alias, group_id = material_module._resolve_key_routing_alias(
        provider='seedance',
        provider_config=provider_config,
        user_group_ids={'g-not-match'},
        user_group_names=set(),
    )

    assert alias == 'k-default'
    assert group_id is None


def test_resolve_provider_credential_falls_back_to_legacy_env(
    material_packages_router_module,
    monkeypatch,
    tmp_path,
):
    material_module = material_packages_router_module
    _set_key_routing_config(material_module, tmp_path / 'missing-key-routing.json')

    monkeypatch.setenv('ARK_API_KEY', 'legacy-seedance-key')

    resolved = _run(
        material_module._resolve_provider_credential(
            provider='seedance',
            user_id='user-1',
        )
    )

    assert resolved['credential_alias'] == 'legacy_env'
    assert resolved['routing_group_id'] is None
    assert resolved['api_key'] == 'legacy-seedance-key'


def test_resolve_provider_credential_raises_no_group_when_configured(
    material_packages_router_module,
    monkeypatch,
    tmp_path,
):
    material_module = material_packages_router_module
    config_path = tmp_path / 'key-routing.json'
    config_path.write_text(
        json.dumps(
            {
                'providers': {
                    'seedance': {
                        'strict_single_group': True,
                        'default_alias': None,
                        'credentials': {'k1': {'env': 'ARK_API_KEY_SEEDANCE_K1'}},
                        'bindings': [{'group_id': 'g1', 'alias': 'k1'}],
                    }
                }
            }
        ),
        encoding='utf-8',
    )
    _set_key_routing_config(material_module, config_path)
    monkeypatch.delenv('ARK_API_KEY_SEEDANCE_K1', raising=False)

    with pytest.raises(HTTPException) as exc_info:
        _run(
            material_module._resolve_provider_credential(
                provider='seedance',
                user_id='user-1',
            )
        )

    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get('code') == material_module.KEY_ROUTING_ERROR_NO_GROUP


def test_resolve_provider_credential_raises_env_missing(
    material_packages_router_module,
    monkeypatch,
    tmp_path,
):
    material_module = material_packages_router_module
    config_path = tmp_path / 'key-routing.json'
    config_path.write_text(
        json.dumps(
            {
                'providers': {
                    'seedance': {
                        'strict_single_group': True,
                        'default_alias': None,
                        'credentials': {'k1': {'env': 'ARK_API_KEY_SEEDANCE_K1'}},
                        'bindings': [{'group_id': 'g1', 'alias': 'k1'}],
                    }
                }
            }
        ),
        encoding='utf-8',
    )
    _set_key_routing_config(material_module, config_path)
    monkeypatch.delenv('ARK_API_KEY_SEEDANCE_K1', raising=False)

    class _Groups:
        @staticmethod
        async def get_groups_by_member_id(user_id, db=None):
            return [types.SimpleNamespace(id='g1', name='seedance-k1')]

    material_module.Groups = _Groups

    with pytest.raises(HTTPException) as exc_info:
        _run(
            material_module._resolve_provider_credential(
                provider='seedance',
                user_id='user-1',
            )
        )

    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get('code') == material_module.KEY_ROUTING_ERROR_ENV_MISSING


def test_resolve_provider_credential_returns_alias_and_group(
    material_packages_router_module,
    monkeypatch,
    tmp_path,
):
    material_module = material_packages_router_module
    config_path = tmp_path / 'key-routing.json'
    config_path.write_text(
        json.dumps(
            {
                'providers': {
                    'seedance': {
                        'strict_single_group': True,
                        'default_alias': None,
                        'credentials': {'k1': {'env': 'ARK_API_KEY_SEEDANCE_K1'}},
                        'bindings': [{'group_id': 'g1', 'alias': 'k1'}],
                    }
                }
            }
        ),
        encoding='utf-8',
    )
    _set_key_routing_config(material_module, config_path)
    monkeypatch.setenv('ARK_API_KEY_SEEDANCE_K1', 'seedance-k1-secret')

    class _Groups:
        @staticmethod
        async def get_groups_by_member_id(user_id, db=None):
            return [types.SimpleNamespace(id='g1', name='seedance-k1')]

    material_module.Groups = _Groups

    resolved = _run(
        material_module._resolve_provider_credential(
            provider='seedance',
            user_id='user-1',
        )
    )

    assert resolved['credential_alias'] == 'k1'
    assert resolved['routing_group_id'] == 'g1'
    assert resolved['api_key'] == 'seedance-k1-secret'

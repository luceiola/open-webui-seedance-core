import asyncio
import importlib.util
import io
import json
import sys
import types
import zipfile
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

    class _TaskCatalog:
        @staticmethod
        def _rows():
            rows = []
            for owner_user_id, path in material_module._iter_task_record_paths():
                item = material_module._load_task_record_from_path(path)
                if item is not None:
                    rows.append((str(owner_user_id), item))
            return rows

        def owners(self, *, include_deleted=False):
            return sorted({owner_user_id for owner_user_id, item in self._rows() if include_deleted or not material_module._is_soft_deleted(item)})

        def query(self, *, user_id=None, owner_ids=None, provider=None, skill_name=None, tool_name=None, status=None, model=None,
                  start_at=None, end_at=None, include_deleted=False, deletion_status=None, offset=0, limit=48):
            aliases = {'SUBMITTED': 'PENDING', 'QUEUED': 'PENDING', 'IN_PROGRESS': 'RUNNING', 'SUCCESS': 'SUCCEEDED', 'COMPLETED': 'SUCCEEDED', 'ERROR': 'FAILED'}
            rows = []
            for owner_user_id, item in self._rows():
                if user_id and owner_user_id != user_id:
                    continue
                if owner_ids is not None and owner_user_id not in owner_ids:
                    continue
                is_deleted = material_module._is_soft_deleted(item)
                if deletion_status == 'deleted' and not is_deleted:
                    continue
                if (deletion_status == 'active' or not include_deleted) and is_deleted:
                    continue
                if provider and str(item.get('provider') or 'ark').lower() != provider:
                    continue
                if skill_name and str(item.get('skill_name') or 'seedance').lower() != skill_name:
                    continue
                if tool_name and str(item.get('tool_name') or 'material_packages.generate').lower() != tool_name:
                    continue
                if model and str(item.get('model') or '').lower() != model:
                    continue
                if status and aliases.get(str(item.get('status') or '').upper(), str(item.get('status') or '').upper()) != status:
                    continue
                created_at = int(item.get('created_at') or 0)
                if start_at is not None and created_at < start_at:
                    continue
                if end_at is not None and created_at > end_at:
                    continue
                rows.append((owner_user_id, item))
            rows.sort(key=lambda row: (int(row[1].get('created_at') or 0), str(row[1].get('task_id') or '')), reverse=True)
            return rows[offset : offset + limit], len(rows)

    material_module.TASK_CATALOG = _TaskCatalog()
    material_module.ensure_task_catalog = lambda: None

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
    routers_pkg = types.ModuleType('open_webui.routers')
    routers_pkg.__path__ = []

    task_catalog_module = types.ModuleType('open_webui.routers.task_catalog')

    class _TaskCatalog:
        def __init__(self, path):
            self.path = path

        def upsert(self, *args, **kwargs):
            return None

        def count(self):
            return 0

        def rebuild(self, *args, **kwargs):
            return None

        def find(self, *args, **kwargs):
            return None

    task_catalog_module.TaskCatalog = _TaskCatalog

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
        'open_webui.routers': routers_pkg,
        'open_webui.routers.task_catalog': task_catalog_module,
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


def test_extract_error_info_supports_runninghub_final_response(material_packages_router_module):
    payload = {
        'query': {'taskId': '2077650260884590594', 'status': 'RUNNING'},
        'final': {
            'taskId': '2077650260884590594',
            'status': 'FAILED',
            'errorCode': '1501',
            'errorMessage': (
                'The request failed because the output audio may contain sensitive information | '
                '请求失败，输出视频的音频中可能包含敏感内容或涉及版权信息'
            ),
        },
    }

    error_info = material_packages_router_module._extract_error_info(payload)

    assert error_info == {
        'error_code': '1501',
        'error_message': (
            'The request failed because the output audio may contain sensitive information | '
            '请求失败，输出视频的音频中可能包含敏感内容或涉及版权信息'
        ),
        'request_id': None,
    }


def _run(coro):
    return asyncio.run(coro)


def test_soft_delete_cleanup_retains_records_and_artifacts(material_packages_router_module, tmp_path):
    record_path = tmp_path / 'task.json'
    video_path = tmp_path / 'task.mp4'
    thumbnail_path = tmp_path / 'task.jpg'
    record_path.write_text('{"deleted_at": 1}', encoding='utf-8')
    video_path.write_bytes(b'video')
    thumbnail_path.write_bytes(b'image')

    material_packages_router_module._cleanup_expired_soft_deleted_records()

    assert record_path.exists()
    assert video_path.exists()
    assert thumbnail_path.exists()


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


def test_admin_can_filter_only_deleted_tasks(tasks_router_module):
    tasks_module, material_stub = tasks_router_module
    task_records = {
        'task-active': {'task_id': 'task-active', 'user_name': 'Alice', 'created_at': 2, 'updated_at': 2},
        'task-deleted': {'task_id': 'task-deleted', 'user_name': 'Bob', 'deleted_at': 10, 'created_at': 1, 'updated_at': 10},
    }
    material_stub._iter_task_record_paths = lambda: [
        ('user-1', Path('/tmp/task-active.json')),
        ('user-2', Path('/tmp/task-deleted.json')),
    ]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._is_soft_deleted = lambda item: int(item.get('deleted_at') or 0) > 0

    response = _run(tasks_module.list_unified_tasks(
        user_id=None, provider=None, skill_name=None, tool_name=None, task_status=None, model=None,
        group_id=None, start_at=None, end_at=None, include_deleted=True, deletion_status='deleted',
        refresh_status=False, refresh_min_interval_seconds=5, offset=0, limit=48,
        user=StubUserModel(id='admin-1', role='admin'),
    ))

    assert response.total == 1
    assert [item.id for item in response.items] == ['task-deleted']


def test_admin_can_preview_deleted_task(tasks_router_module):
    tasks_module, material_stub = tasks_router_module
    requested_options = {}

    async def _load_task_for_read(task_id, **kwargs):
        requested_options.update(kwargs)
        return 'user-2', {
            'task_id': task_id,
            'user_name': 'Bob',
            'status': 'SUCCEEDED',
            'archive_status': 'SUCCEEDED',
            'download_ready': True,
            'deleted_at': 10,
            'created_at': 1,
            'updated_at': 10,
        }

    material_stub._load_task_for_read = _load_task_for_read
    response = _run(tasks_module.get_unified_task_preview(
        task_id='task-deleted',
        refresh_status=False,
        user=StubUserModel(id='admin-1', role='admin'),
    ))

    assert requested_options['include_deleted'] is True
    assert response.task_id == 'task-deleted'
    assert response.download_ready is True


def test_regular_user_can_read_another_users_active_task(tasks_router_module):
    tasks_module, material_stub = tasks_router_module
    requested_options = {}

    async def _load_task_for_read(task_id, **kwargs):
        requested_options.update(kwargs)
        return 'user-2', {'task_id': task_id}

    material_stub._load_task_for_read = _load_task_for_read
    owner_user_id, item = _run(tasks_module._load_task_for_request(
        'other-task',
        StubUserModel(id='user-1', role='user'),
    ))

    assert requested_options['include_deleted'] is False
    assert owner_user_id == 'user-2'
    assert item['task_id'] == 'other-task'


def test_regular_user_can_query_other_users_but_not_deleted_tasks(tasks_router_module):
    tasks_module, material_stub = tasks_router_module
    task_records = {
        'own-active': {'task_id': 'own-active', 'user_name': 'Alice', 'created_at': 3, 'updated_at': 3},
        'own-deleted': {'task_id': 'own-deleted', 'user_name': 'Alice', 'deleted_at': 10, 'created_at': 2, 'updated_at': 10},
        'other-active': {'task_id': 'other-active', 'user_name': 'Bob', 'created_at': 1, 'updated_at': 1},
    }
    material_stub._iter_task_record_paths = lambda: [
        ('user-1', Path('/tmp/own-active.json')),
        ('user-1', Path('/tmp/own-deleted.json')),
        ('user-2', Path('/tmp/other-active.json')),
    ]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._is_soft_deleted = lambda item: int(item.get('deleted_at') or 0) > 0

    selected_user_response = _run(tasks_module.list_unified_tasks(
        user_id='user-2', provider=None, skill_name=None, tool_name=None, task_status=None, model=None,
        group_id=None, start_at=None, end_at=None, include_deleted=True, deletion_status='all',
        refresh_status=False, refresh_min_interval_seconds=5, offset=0, limit=48,
        user=StubUserModel(id='user-1', role='user'),
    ))

    all_users_response = _run(tasks_module.list_unified_tasks(
        user_id=None, provider=None, skill_name=None, tool_name=None, task_status=None, model=None,
        group_id=None, start_at=None, end_at=None, include_deleted=True, deletion_status='all',
        refresh_status=False, refresh_min_interval_seconds=5, offset=0, limit=48,
        user=StubUserModel(id='user-1', role='user'),
    ))
    users_response = _run(tasks_module.list_unified_task_users(
        include_deleted=True,
        user=StubUserModel(id='user-1', role='user'),
    ))

    assert selected_user_response.total == 1
    assert [item.id for item in selected_user_response.items] == ['other-active']
    assert all_users_response.total == 2
    assert [item.id for item in all_users_response.items] == ['own-active', 'other-active']
    assert [item.user_id for item in users_response.users] == ['user-1', 'user-2']


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


def test_unified_tasks_list_includes_image_artifact_fields(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    task_records = {
        'task-image': {
            'task_id': 'task-image',
            'provider': 'btn_image2',
            'status': 'SUCCEEDED',
            'archive_status': 'NOT_REQUIRED',
            'created_at': 200,
            'updated_at': 210,
            'download_ready': False,
            'user_name': 'Alice',
            'artifact_kind': 'image',
            'image_urls': [
                'https://example.com/out-1.png',
                'https://example.com/out-2.png',
                'ftp://invalid.example.com/out-3.png',
            ],
            'primary_image_url': 'https://example.com/out-1.png',
        }
    }

    material_stub._iter_task_record_paths = lambda: [('user-1', Path('/tmp/task-image.json'))]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._normalize_task_defaults = lambda item, owner_user_id: False
    material_stub._should_refresh_task_status = lambda item, refresh_min_interval_seconds: False
    material_stub._is_soft_deleted = lambda item: False
    material_stub._generation_skill_from_model = lambda model: 'btn-image2'

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

    assert response.total == 1
    assert response.items[0].id == 'task-image'
    assert response.items[0].artifact_kind == 'image'
    assert response.items[0].image_urls == ['https://example.com/out-1.png', 'https://example.com/out-2.png']
    assert response.items[0].primary_image_url == 'https://example.com/out-1.png'
    assert response.items[0].thumbnail_url == 'https://example.com/out-1.png'


def test_unified_task_preview_returns_image_fields(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    async def _load_task_for_read(task_id, **kwargs):
        _ = kwargs
        return 'user-1', {
            'task_id': task_id,
            'provider': 'btn_image2',
            'status': 'SUCCEEDED',
            'archive_status': 'NOT_REQUIRED',
            'download_ready': False,
            'created_at': 10,
            'updated_at': 20,
            'artifact_kind': 'image',
            'image_urls': ['https://example.com/preview.png'],
            'primary_image_url': 'https://example.com/preview.png',
        }

    material_stub._load_task_for_read = _load_task_for_read
    requester = StubUserModel(id='user-1', role='user')

    response = _run(tasks_module.get_unified_task_preview(task_id='task-image', refresh_status=False, user=requester))

    assert response.task_id == 'task-image'
    assert response.artifact_kind == 'image'
    assert response.image_urls == ['https://example.com/preview.png']
    assert response.primary_image_url == 'https://example.com/preview.png'
    assert response.thumbnail_url == 'https://example.com/preview.png'


def test_unified_task_preview_uses_local_image_output_when_artifacts_are_mounted(tasks_router_module, tmp_path):
    tasks_module, material_stub = tasks_router_module
    owner_user_id = 'user-1'
    task_id = 'task-image-local-mounted'
    local_user_root = tmp_path / 'data' / 'cache' / 'material_packages' / owner_user_id
    image_dir = local_user_root / 'task_vendor_artifacts' / 'btn_image2' / task_id / 'images'
    mounted_image_dir = tmp_path / 'mounted-artifacts' / owner_user_id / image_dir.relative_to(local_user_root)
    mounted_image_dir.mkdir(parents=True, exist_ok=True)
    (mounted_image_dir / 'image_001.png').write_bytes(b'fake-image-1')
    (mounted_image_dir / 'image_002.png').write_bytes(b'fake-image-2')

    async def _load_task_for_read(task_id_arg, **kwargs):
        _ = kwargs
        return owner_user_id, {
            'task_id': task_id_arg,
            'provider': 'btn_image2',
            'status': 'SUCCEEDED',
            'archive_status': 'NOT_REQUIRED',
            'download_ready': False,
            'created_at': 10,
            'updated_at': 20,
            'artifact_kind': 'image',
            'image_urls': [],
            'generation_params': {
                'saved_image_dir': str(image_dir),
                'image_files': "['image_001.png', 'image_002.png']",
            },
        }

    material_stub._load_task_for_read = _load_task_for_read
    material_stub._user_root_dir = lambda user_id: tmp_path / 'data' / 'cache' / 'material_packages' / str(user_id)
    material_stub._task_artifact_user_root_dir = lambda user_id: tmp_path / 'mounted-artifacts' / str(user_id)

    requester = StubUserModel(id=owner_user_id, role='user')
    response = _run(tasks_module.get_unified_task_preview(task_id=task_id, refresh_status=False, user=requester))

    assert response.image_urls == [
        f'/api/v1/tasks/{task_id}/images/0',
        f'/api/v1/tasks/{task_id}/images/1',
    ]
    assert response.primary_image_url == f'/api/v1/tasks/{task_id}/images/0'
    assert response.thumbnail_url == f'/api/v1/tasks/{task_id}/images/0'


def test_unified_task_preview_rebases_migrated_image_output_path(tasks_router_module, tmp_path):
    tasks_module, material_stub = tasks_router_module
    owner_user_id = 'user-1'
    task_id = 'task-image-migrated'
    user_root = tmp_path / 'data' / 'cache' / 'material_packages' / owner_user_id
    relative_image_dir = Path('task_vendor_artifacts') / 'btn_image2' / task_id / 'images'
    migrated_image_dir = (
        Path('/Users/lucas/srv/open-webui-seedance-prod/.data-prod/cache/material_packages')
        / owner_user_id
        / relative_image_dir
    )
    current_image_dir = user_root / relative_image_dir
    current_image_dir.mkdir(parents=True)
    (current_image_dir / 'image_001.png').write_bytes(b'fake-image')

    async def _load_task_for_read(task_id_arg, **kwargs):
        _ = kwargs
        return owner_user_id, {
            'task_id': task_id_arg,
            'provider': 'btn_image2',
            'status': 'SUCCEEDED',
            'archive_status': 'NOT_REQUIRED',
            'download_ready': False,
            'created_at': 10,
            'updated_at': 20,
            'artifact_kind': 'image',
            'image_urls': [],
            'generation_params': {
                'saved_image_dir': str(migrated_image_dir),
                'image_files': "['image_001.png']",
            },
        }

    material_stub._load_task_for_read = _load_task_for_read
    material_stub._user_root_dir = lambda user_id: tmp_path / 'data' / 'cache' / 'material_packages' / str(user_id)
    requester = StubUserModel(id=owner_user_id, role='user')

    response = _run(tasks_module.get_unified_task_preview(task_id=task_id, refresh_status=False, user=requester))

    expected_url = f'/api/v1/tasks/{task_id}/images/0'
    assert response.image_urls == [expected_url]
    assert response.primary_image_url == expected_url
    assert response.thumbnail_url == expected_url
    _, task_item = _run(_load_task_for_read(task_id))
    assert tasks_module._build_declared_task_image_thumbnail_url(
        task_id,
        owner_user_id,
        task_item,
    ) == expected_url


def test_unified_tasks_list_uses_task_artifact_thumbnail_without_directory_scan(tasks_router_module, tmp_path):
    tasks_module, material_stub = tasks_router_module
    owner_user_id = 'user-1'
    task_id = 'task-image-local'

    artifact_user_root = tmp_path / 'generated-artifacts' / owner_user_id
    image_dir = artifact_user_root / 'task_vendor_artifacts' / 'btn_image2' / task_id / 'images'
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / 'image_001.png').write_bytes(b'fake-image-1')
    (image_dir / 'image_002.png').write_bytes(b'fake-image-2')

    task_records = {
        task_id: {
            'task_id': task_id,
            'provider': 'btn_image2',
            'status': 'SUCCEEDED',
            'archive_status': 'NOT_REQUIRED',
            'created_at': 200,
            'updated_at': 210,
            'download_ready': False,
            'user_name': 'Alice',
            'artifact_kind': 'image',
            'image_urls': [],
            'primary_image_url': None,
            'generation_params': {
                'saved_image_dir': str(image_dir),
                'image_files': "['image_001.png', 'image_002.png']",
            },
        }
    }

    material_stub._iter_task_record_paths = lambda: [(owner_user_id, Path(f'/tmp/{task_id}.json'))]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._normalize_task_defaults = lambda item, owner_user_id: False
    material_stub._should_refresh_task_status = lambda item, refresh_min_interval_seconds: False
    material_stub._is_soft_deleted = lambda item: False
    material_stub._generation_skill_from_model = lambda model: 'btn-image2'
    material_stub._user_root_dir = lambda uid: tmp_path / 'cache' / 'material_packages' / str(uid)
    material_stub._task_artifact_user_root_dir = lambda uid: tmp_path / 'generated-artifacts' / str(uid)

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

    assert response.total == 1
    assert response.items[0].id == task_id
    assert response.items[0].image_urls == [f'/api/v1/tasks/{task_id}/images/0']
    assert response.items[0].primary_image_url == f'/api/v1/tasks/{task_id}/images/0'
    assert response.items[0].thumbnail_url == f'/api/v1/tasks/{task_id}/images/0'


def test_declared_image_thumbnail_rejects_path_outside_task_roots(tasks_router_module, tmp_path):
    tasks_module, material_stub = tasks_router_module
    owner_user_id = 'user-1'
    task_id = 'task-image-outside'
    material_stub._user_root_dir = lambda uid: tmp_path / 'cache' / 'material_packages' / str(uid)
    material_stub._task_artifact_user_root_dir = lambda uid: tmp_path / 'generated-artifacts' / str(uid)

    thumbnail_url = tasks_module._build_declared_task_image_thumbnail_url(
        task_id,
        owner_user_id,
        {
            'generation_params': {
                'saved_image_dir': str(tmp_path / 'outside' / 'images'),
                'image_files': "['image_001.png']",
            },
        },
    )

    assert thumbnail_url is None


def test_unified_tasks_list_skips_image_directory_scan(tasks_router_module, tmp_path):
    tasks_module, material_stub = tasks_router_module
    owner_user_id = 'user-1'
    task_id = 'task-image-local-merge'

    user_root = tmp_path / 'cache' / 'material_packages' / owner_user_id
    image_dir = user_root / 'task_vendor_artifacts' / 'btn_image2' / task_id / 'images'
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / 'image_001.png').write_bytes(b'fake-image-1')
    (image_dir / 'image_002.png').write_bytes(b'fake-image-2')
    (image_dir / 'image_003.png').write_bytes(b'fake-image-3')
    (image_dir / 'image_004.png').write_bytes(b'fake-image-4')

    task_records = {
        task_id: {
            'task_id': task_id,
            'provider': 'btn_image2',
            'status': 'SUCCEEDED',
            'archive_status': 'NOT_REQUIRED',
            'created_at': 200,
            'updated_at': 210,
            'download_ready': False,
            'user_name': 'Alice',
            'artifact_kind': 'image',
            'image_urls': [],
            'primary_image_url': None,
            'generation_params': {
                'saved_image_dir': str(image_dir),
                'image_files': "['image_001.png', 'image_002.png', 'image_003.png']",
            },
        }
    }

    material_stub._iter_task_record_paths = lambda: [(owner_user_id, Path(f'/tmp/{task_id}.json'))]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._normalize_task_defaults = lambda item, owner_user_id: False
    material_stub._should_refresh_task_status = lambda item, refresh_min_interval_seconds: False
    material_stub._is_soft_deleted = lambda item: False
    material_stub._generation_skill_from_model = lambda model: 'btn-image2'
    material_stub._user_root_dir = lambda uid: tmp_path / 'cache' / 'material_packages' / str(uid)

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

    assert response.total == 1
    assert response.items[0].id == task_id
    assert response.items[0].image_urls == [f'/api/v1/tasks/{task_id}/images/0']
    assert response.items[0].primary_image_url == f'/api/v1/tasks/{task_id}/images/0'
    assert response.items[0].thumbnail_url == f'/api/v1/tasks/{task_id}/images/0'


def test_unified_task_image_route_streams_local_image(tasks_router_module, tmp_path):
    tasks_module, material_stub = tasks_router_module
    owner_user_id = 'user-1'
    task_id = 'task-image-route'

    user_root = tmp_path / 'cache' / 'material_packages' / owner_user_id
    image_dir = user_root / 'task_vendor_artifacts' / 'btn_image2' / task_id / 'images'
    image_dir.mkdir(parents=True, exist_ok=True)
    image_file = image_dir / 'image_001.png'
    image_file.write_bytes(b'fake-image')

    async def _load_task_for_read(task_id_arg, **kwargs):
        _ = kwargs
        return owner_user_id, {
            'task_id': task_id_arg,
            'artifact_kind': 'image',
            'generation_params': {
                'saved_image_dir': str(image_dir),
                'image_files': "['image_001.png']",
            },
            'status': 'SUCCEEDED',
            'archive_status': 'NOT_REQUIRED',
            'download_ready': False,
            'created_at': 10,
            'updated_at': 20,
        }

    material_stub._load_task_for_read = _load_task_for_read
    material_stub._user_root_dir = lambda uid: tmp_path / 'cache' / 'material_packages' / str(uid)

    requester = StubUserModel(id='user-1', role='user')
    response = _run(tasks_module.get_unified_task_image(task_id=task_id, index=0, user=requester))

    assert Path(response.path) == image_file
    assert response.media_type == 'image/png'


def test_unified_task_images_download_route_returns_zip(tasks_router_module, tmp_path):
    tasks_module, material_stub = tasks_router_module
    owner_user_id = 'user-1'
    task_id = 'task-image-download'

    user_root = tmp_path / 'cache' / 'material_packages' / owner_user_id
    image_dir = user_root / 'task_vendor_artifacts' / 'btn_image2' / task_id / 'images'
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / 'image_001.png').write_bytes(b'fake-image-1')
    (image_dir / 'image_002.png').write_bytes(b'fake-image-2')
    (image_dir / 'image_003.png').write_bytes(b'fake-image-3')
    (image_dir / 'image_004.png').write_bytes(b'fake-image-4')

    async def _load_task_for_read(task_id_arg, **kwargs):
        _ = kwargs
        return owner_user_id, {
            'task_id': task_id_arg,
            'artifact_kind': 'image',
            'generation_params': {
                'saved_image_dir': str(image_dir),
                'image_files': "['image_001.png', 'image_002.png', 'image_003.png']",
            },
            'status': 'SUCCEEDED',
            'archive_status': 'NOT_REQUIRED',
            'download_ready': False,
            'created_at': 10,
            'updated_at': 20,
        }

    material_stub._load_task_for_read = _load_task_for_read
    material_stub._user_root_dir = lambda uid: tmp_path / 'cache' / 'material_packages' / str(uid)

    requester = StubUserModel(id='user-1', role='user')
    response = _run(tasks_module.download_unified_task_images(task_id=task_id, user=requester))

    assert response.media_type == 'application/zip'
    assert response.headers.get('content-disposition') == f'attachment; filename="{task_id}_images.zip"'
    archive = zipfile.ZipFile(io.BytesIO(response.body))
    assert sorted(archive.namelist()) == [
        'image_001.png',
        'image_002.png',
        'image_003.png',
        'image_004.png',
    ]
    assert archive.read('image_004.png') == b'fake-image-4'


def test_unified_tasks_backfills_error_fields_from_raw_payload(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    task_records = {
        'task-failed': {
            'task_id': 'task-failed',
            'provider': 'ark',
            'status': 'FAILED',
            'archive_status': 'FAILED',
            'created_at': 100,
            'updated_at': 200,
            'download_ready': False,
            'user_name': 'Alice',
            # message already exists and should not be overwritten by fallback.
            'error_message': 'top-level message',
            'raw_last_response': {
                'error_code': 'AccountOverdueError',
                'error_message': 'fallback message from raw_last_response',
                'request_id': 'req-from-raw-last',
            },
            'raw_submit_response': {
                'error_code': 'FallbackSubmitError',
                'error_message': 'fallback message from raw_submit_response',
                'request_id': 'req-from-raw-submit',
            },
        }
    }

    material_stub._iter_task_record_paths = lambda: [('user-1', Path('/tmp/task-failed.json'))]
    material_stub._load_task_record_from_path = lambda path: dict(task_records[path.stem])
    material_stub._normalize_task_defaults = lambda item, owner_user_id: False
    material_stub._should_refresh_task_status = lambda item, refresh_min_interval_seconds: False
    material_stub._is_soft_deleted = lambda item: False
    material_stub._generation_skill_from_model = lambda model: 'seedance'
    material_stub._extract_error_info = lambda payload: {
        'error_code': payload.get('error_code'),
        'error_message': payload.get('error_message'),
        'request_id': payload.get('request_id'),
    }

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

    assert response.total == 1
    assert len(response.items) == 1
    assert response.items[0].id == 'task-failed'
    assert response.items[0].error_code == 'AccountOverdueError'
    assert response.items[0].error_message == 'top-level message'
    assert response.items[0].request_id == 'req-from-raw-last'


def test_unified_tasks_includes_archive_error(tasks_router_module):
    tasks_module, material_stub = tasks_router_module

    task_records = {
        'task-archive-failed': {
            'task_id': 'task-archive-failed',
            'provider': 'ark',
            'status': 'SUCCEEDED',
            'archive_status': 'FAILED',
            'archive_error': 'download timeout while archiving',
            'created_at': 100,
            'updated_at': 200,
            'download_ready': False,
            'user_name': 'Alice',
        }
    }

    material_stub._iter_task_record_paths = lambda: [('user-1', Path('/tmp/task-archive-failed.json'))]
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

    assert response.total == 1
    assert len(response.items) == 1
    assert response.items[0].id == 'task-archive-failed'
    assert response.items[0].archive_error == 'download timeout while archiving'


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

    requester = StubUserModel(id='user-1', role='user')
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


def test_deleted_task_media_routes_allow_admin_only(material_packages_router_module, tmp_path):
    material_module = material_packages_router_module
    task_id = 'deleted-video-task'
    owner_user_id = 'owner-1'
    video_path = tmp_path / 'task.mp4'
    thumbnail_path = tmp_path / 'task.jpg'
    video_path.write_bytes(b'video')
    thumbnail_path.write_bytes(b'thumbnail')

    task_record = {
        'task_id': task_id,
        'download_ready': True,
        'archived_video_path': 'task_archives/deleted-video-task.mp4',
        'thumbnail_path': 'task_thumbnails/deleted-video-task.jpg',
    }
    seen_include_deleted = []

    async def _load_task_for_read(task_id_value, **kwargs):
        assert task_id_value == task_id
        seen_include_deleted.append(kwargs.get('include_deleted'))
        return owner_user_id, task_record

    def _task_file_from_relative(_owner_user_id, relative_path):
        return video_path if str(relative_path).endswith('.mp4') else thumbnail_path

    material_module._load_task_for_read = _load_task_for_read
    material_module._task_file_from_relative = _task_file_from_relative
    admin = StubUserModel(id='admin-1', role='admin')

    video_response = _run(material_module.stream_generation_task_video(task_id, admin))
    thumbnail_response = _run(material_module.get_generation_task_thumbnail(task_id, admin))
    download_response = _run(material_module.download_generation_task(task_id, admin))

    assert video_response.path == video_path
    assert thumbnail_response.path == thumbnail_path
    assert download_response.path == video_path
    assert seen_include_deleted == [True, True, True]


def test_deleted_task_media_routes_remain_hidden_from_regular_users(material_packages_router_module):
    material_module = material_packages_router_module
    seen_include_deleted = []

    async def _load_task_for_read(_task_id, **kwargs):
        seen_include_deleted.append(kwargs.get('include_deleted'))
        raise HTTPException(status_code=404, detail='Task not found')

    material_module._load_task_for_read = _load_task_for_read
    user = StubUserModel(id='user-1', role='user')

    for route in (
        material_module.stream_generation_task_video,
        material_module.get_generation_task_thumbnail,
        material_module.download_generation_task,
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(route('deleted-video-task', user))
        assert exc_info.value.status_code == 404

    assert seen_include_deleted == [False, False, False]


def test_empty_video_download_is_retryable_and_removes_partial_file(
    material_packages_router_module,
    tmp_path,
):
    material_module = material_packages_router_module

    class EmptyResponse:
        status_code = 200
        headers = {'content-length': '0'}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self, chunk_size):
            if False:
                yield b''

    class EmptyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url):
            return EmptyResponse()

    material_module.httpx.AsyncClient = lambda **kwargs: EmptyClient()
    video_path = tmp_path / 'video.mp4'

    with pytest.raises(material_module._RetryableVideoDownloadError) as exc_info:
        _run(material_module._download_video_file('https://example.com/video.mp4', video_path))

    assert 'HTTP 200' in str(exc_info.value)
    assert 'Content-Length=0' in str(exc_info.value)
    assert not video_path.with_suffix('.mp4.part').exists()


def test_empty_archive_download_stays_pending_until_retry_limit(material_packages_router_module):
    material_module = material_packages_router_module
    user_id = 'user-1'
    task_id = 'task-empty-video'

    async def _empty_download(*args, **kwargs):
        raise material_module._RetryableVideoDownloadError(
            'Video download returned an empty body (HTTP 200, Content-Length=0)'
        )

    material_module._download_video_file = _empty_download
    material_module.TASK_ARCHIVE_MAX_RETRIES = 3
    task_record = {
        'task_id': task_id,
        'user_id': user_id,
        'status': 'SUCCEEDED',
        'artifact_kind': 'video',
        'video_url': 'https://example.com/video.mp4',
        'archive_status': 'PENDING',
        'archive_retry_count': 0,
        'created_at': 1,
        'updated_at': 1,
    }

    pending = _run(material_module._archive_task_record_if_needed(user_id, dict(task_record)))
    assert pending['archive_status'] == 'PENDING'
    assert pending['archive_retry_count'] == 1
    assert 'empty body' in pending['archive_error']

    pending['archive_retry_count'] = 2
    exhausted = _run(material_module._archive_task_record_if_needed(user_id, pending))
    assert exhausted['archive_status'] == 'FAILED'
    assert exhausted['archive_retry_count'] == 3


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

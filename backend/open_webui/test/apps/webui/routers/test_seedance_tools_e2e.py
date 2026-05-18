import asyncio
import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
DOUBAO_TOOL_PATH = REPO_ROOT / 'templates' / 'doubao_seed_prompt_tool.py'


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load module: {file_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_describe_media_assets_template_mode_fills_missing_fields(monkeypatch):
    module = _load_module('test_doubao_template_mode', DOUBAO_TOOL_PATH)
    tool = module.Tools()

    async def _fake_resolve_asset(asset_id: str, expected_media_type: str, __request__=None):
        return (
            {
                'asset_id': asset_id,
                'media_type': expected_media_type,
                'url': 'https://example.com/video.mp4',
                'expires_in': 3600,
            },
            None,
        )

    async def _fake_resolve_credential(__user__=None, preferred_alias=''):
        return {
            'ok': True,
            'provider': 'seedance',
            'credential_alias': 'default',
            'routing_group_id': 'group-1',
            'api_key': 'fake-key',
            'source': 'key_routing',
        }

    async def _fake_chat(*args, **kwargs):
        return {
            'ok': True,
            'status_code': 200,
            'data': {
                'request_id': 'req-template-1',
                'choices': [
                    {
                        'message': {
                            'content': json.dumps({'视频类型': '广告片'}, ensure_ascii=False)
                        }
                    }
                ],
            },
        }

    monkeypatch.setattr(tool, '_resolve_media_asset_to_url', _fake_resolve_asset)
    monkeypatch.setattr(tool, '_resolve_ark_credential', _fake_resolve_credential)
    monkeypatch.setattr(tool, '_ark_chat_completions', _fake_chat)

    raw = asyncio.run(
        tool.describe_media_assets_for_prompt(
            description_request='按模板输出',
            reference_video_asset_id='asset-video-1',
            enforce_template_output=True,
            __request__=None,
            __user__={'id': 'u1'},
        )
    )
    payload = json.loads(raw)

    assert payload['ok'] is True
    assert payload['template_mode'] is True
    assert payload['template_id'] == 'storyboard_list_v1'
    assert '[待补充]' in payload['description_text']
    assert '### 专业视频分镜脚本模板' in payload['description_text']


def test_describe_media_assets_template_mode_requires_video(monkeypatch):
    module = _load_module('test_doubao_template_requires_video', DOUBAO_TOOL_PATH)
    tool = module.Tools()

    async def _fake_resolve_asset(asset_id: str, expected_media_type: str, __request__=None):
        return (
            {
                'asset_id': asset_id,
                'media_type': expected_media_type,
                'url': 'https://example.com/image.png',
                'expires_in': 3600,
            },
            None,
        )

    monkeypatch.setattr(tool, '_resolve_media_asset_to_url', _fake_resolve_asset)

    raw = asyncio.run(
        tool.describe_media_assets_for_prompt(
            description_request='按专业分镜模板输出',
            reference_image_asset_id='asset-image-1',
            enforce_template_output=True,
            __request__=None,
            __user__={'id': 'u1'},
        )
    )
    payload = json.loads(raw)

    assert payload['ok'] is False
    assert payload['error_code'] == 'InvalidParameter'
    assert 'requires a video reference' in payload['error_message']


def test_describe_media_assets_non_template_path_keeps_text(monkeypatch):
    module = _load_module('test_doubao_non_template', DOUBAO_TOOL_PATH)
    tool = module.Tools()

    async def _fake_resolve_asset(asset_id: str, expected_media_type: str, __request__=None):
        return (
            {
                'asset_id': asset_id,
                'media_type': expected_media_type,
                'url': 'https://example.com/image.png',
                'expires_in': 3600,
            },
            None,
        )

    async def _fake_resolve_credential(__user__=None, preferred_alias=''):
        return {
            'ok': True,
            'provider': 'seedance',
            'credential_alias': 'default',
            'routing_group_id': 'group-1',
            'api_key': 'fake-key',
            'source': 'key_routing',
        }

    async def _fake_chat(*args, **kwargs):
        return {
            'ok': True,
            'status_code': 200,
            'data': {
                'request_id': 'req-normal-1',
                'choices': [{'message': {'content': '这是普通描述正文'}}],
            },
        }

    monkeypatch.setattr(tool, '_resolve_media_asset_to_url', _fake_resolve_asset)
    monkeypatch.setattr(tool, '_resolve_ark_credential', _fake_resolve_credential)
    monkeypatch.setattr(tool, '_ark_chat_completions', _fake_chat)

    raw = asyncio.run(
        tool.describe_media_assets_for_prompt(
            description_request='请描述这张图',
            reference_image_asset_id='asset-image-1',
            __request__=None,
            __user__={'id': 'u1'},
        )
    )
    payload = json.loads(raw)

    assert payload['ok'] is True
    assert payload['template_mode'] is False
    assert payload['description_text'] == '这是普通描述正文'

import importlib.util
from pathlib import Path

import httpx
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLKIT_PATH = REPO_ROOT / 'templates' / 'shared' / 'toolkit.py'


def _load_toolkit_module():
    spec = importlib.util.spec_from_file_location('seedance_shared_toolkit', str(TOOLKIT_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load module: {TOOLKIT_PATH}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            'type': 'http',
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': 'GET',
            'scheme': 'http',
            'path': '/',
            'query_string': b'',
            'headers': headers or [],
            'client': ('127.0.0.1', 12345),
            'server': ('localhost', 8080),
        }
    )


def test_extract_media_asset_references_dedup_and_trim():
    toolkit = _load_toolkit_module()
    prompt = '使用 %a.png, 再用 %a.png。然后是 %folder/b.mov 和 %中文_角色.mp4！'
    refs = toolkit.extract_media_asset_references(prompt)
    assert refs == ['a.png', 'folder/b.mov', '中文_角色.mp4']


def test_build_auth_headers_priority():
    toolkit = _load_toolkit_module()

    request_with_auth = _build_request(headers=[(b'authorization', b'Bearer from-request')])
    headers = toolkit.build_auth_headers(request_with_auth, 'fallback-token', include_content_type=True)
    assert headers['Authorization'] == 'Bearer from-request'
    assert headers['Content-Type'] == 'application/json'

    request_with_cookie = _build_request(headers=[(b'cookie', b'token=cookie-token')])
    cookie_headers = toolkit.build_auth_headers(request_with_cookie, 'fallback-token', include_content_type=False)
    assert cookie_headers['Authorization'] == 'Bearer cookie-token'
    assert 'Content-Type' not in cookie_headers


def test_normalize_httpx_error_nested_detail_json():
    toolkit = _load_toolkit_module()
    response = httpx.Response(
        400,
        json={
            'detail': 'Ark create failed: {"error": {"code": "BadRequest", "message": "invalid input request id: req_abc"}}'
        },
    )

    payload = toolkit.normalize_httpx_error(response)
    assert payload['ok'] is False
    assert payload['error_code'] == 'BadRequest'
    assert 'invalid input' in payload['error_message']
    assert payload['request_id'] == 'req_abc'

import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
BUILD_SCRIPT = REPO_ROOT / 'scripts' / 'seedance' / 'build_standalone_tools.py'
STANDALONE_DOUBAO_TOOL = REPO_ROOT / 'templates' / 'dist' / 'doubao_seed_prompt_tool.py'


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load module: {file_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_tool_can_load_without_shared_package_module_cache():
    result = subprocess.run(
        ['python', str(BUILD_SCRIPT)],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    assert STANDALONE_DOUBAO_TOOL.exists()

    backups = {name: sys.modules[name] for name in list(sys.modules) if name == 'shared' or name.startswith('shared.')}
    for name in list(backups):
        sys.modules.pop(name, None)

    try:
        module = _load_module('standalone_doubao_tool_test', STANDALONE_DOUBAO_TOOL)
        assert hasattr(module, 'Tools')
        instance = module.Tools()
        assert instance is not None
    finally:
        for name in list(sys.modules):
            if name == 'shared' or name.startswith('shared.'):
                sys.modules.pop(name, None)
        sys.modules.update(backups)

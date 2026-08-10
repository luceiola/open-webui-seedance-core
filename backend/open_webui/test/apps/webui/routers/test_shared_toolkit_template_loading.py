import sys
import types
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[6]
TOOL_PATHS = (
    REPO_ROOT / "templates" / "seedance_material_package_tool.py",
    REPO_ROOT / "templates" / "happyhorse_media_tool.py",
)


@pytest.mark.parametrize("tool_path", TOOL_PATHS, ids=lambda path: path.stem)
def test_tool_template_loads_from_frontend_temporary_file(monkeypatch, tmp_path, tool_path):
    monkeypatch.chdir(REPO_ROOT)
    preserved_modules = {
        name: sys.modules.pop(name)
        for name in ("shared.toolkit", "shared")
        if name in sys.modules
    }
    module_name = f"tool_frontend_update_{tool_path.stem}"
    module = types.ModuleType(module_name)
    temp_file = tmp_path / "uploaded_tool.py"
    content = tool_path.read_text(encoding="utf-8")
    temp_file.write_text(content, encoding="utf-8")
    module.__dict__["__file__"] = str(temp_file)
    sys.modules[module_name] = module

    try:
        exec(compile(content, str(temp_file), "exec"), module.__dict__)
        assert hasattr(module, "Tools")
        assert module.Tools().__class__.__name__ == "Tools"
    finally:
        sys.modules.pop(module_name, None)
        sys.modules.pop("shared.toolkit", None)
        sys.modules.pop("shared", None)
        sys.modules.update(preserved_modules)

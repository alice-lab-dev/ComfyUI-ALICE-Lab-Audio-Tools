from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).parents[2]
ENTRYPOINT = ROOT / "__init__.py"
MAPPINGS = {"AliceLabMediaRange": object}
DISPLAY_NAMES = {"AliceLabMediaRange": "Load Media Range (Upload)"}


def _nodes_module() -> types.ModuleType:
    module = types.ModuleType("nodes")
    module.NODE_CLASS_MAPPINGS = MAPPINGS
    module.NODE_DISPLAY_NAME_MAPPINGS = DISPLAY_NAMES
    return module


def test_entrypoint_exports_mappings_when_loaded_as_a_package(monkeypatch) -> None:
    package_name = "alice_lab_audio_tools_entrypoint_package_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    source_package = types.ModuleType(f"{package_name}.src")
    source_package.__path__ = [str(ROOT / "src")]
    nodes = _nodes_module()

    monkeypatch.setitem(sys.modules, package_name, package)
    monkeypatch.setitem(sys.modules, f"{package_name}.src", source_package)
    monkeypatch.setitem(sys.modules, f"{package_name}.src.nodes", nodes)

    spec = importlib.util.spec_from_file_location(
        package_name,
        ENTRYPOINT,
        submodule_search_locations=[str(ROOT)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.NODE_CLASS_MAPPINGS is MAPPINGS
    assert module.NODE_DISPLAY_NAME_MAPPINGS is DISPLAY_NAMES
    assert module.WEB_DIRECTORY == "./web"


def test_entrypoint_exports_mappings_when_loaded_directly(monkeypatch) -> None:
    source_package = types.ModuleType("src")
    source_package.__path__ = [str(ROOT / "src")]
    nodes = _nodes_module()
    monkeypatch.setitem(sys.modules, "src", source_package)
    monkeypatch.setitem(sys.modules, "src.nodes", nodes)

    # Some Registry/Manager readers execute the entry point without package
    # metadata.  Reproduce the documented ``__package__ == ""`` condition.
    module = types.ModuleType("alice_lab_audio_tools_registry_entrypoint_test")
    module.__file__ = str(ENTRYPOINT)
    module.__package__ = ""
    sys.modules[module.__name__] = module
    exec(compile(ENTRYPOINT.read_text(encoding="utf-8"), str(ENTRYPOINT), "exec"), module.__dict__)

    assert module.NODE_CLASS_MAPPINGS is MAPPINGS
    assert module.NODE_DISPLAY_NAME_MAPPINGS is DISPLAY_NAMES
    assert module.WEB_DIRECTORY == "./web"

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


def _load_module(
    temp_directory: Path,
    monkeypatch,
    user_directory: Path | None = None,
):
    user_directory = user_directory or temp_directory / "user"
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_temp_directory = lambda: str(temp_directory)
    folder_paths.get_user_directory = lambda: str(user_directory)
    folder_paths.get_system_user_directory = lambda name: str(
        user_directory / f"__{name}"
    )
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)
    spec = importlib.util.spec_from_file_location(
        "alice_test_cache_store",
        Path(__file__).parents[2] / "src" / "cache_store.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_manager(cache_store, monkeypatch):
    package = types.ModuleType("alice_test_cache_package")
    package.__path__ = [str(Path(__file__).parents[2] / "src")]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, f"{package.__name__}.cache_store", cache_store)
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.cache_manager",
        Path(__file__).parents[2] / "src" / "cache_manager.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cache_key_is_stable_and_does_not_expose_source(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)
    source = {"path": "/private/audio.wav", "size": 12, "mtime_ns": 34}

    first = module.cache_key("waveform", source, {"points": 1000})
    second = module.cache_key("waveform", source, {"points": 1000})

    assert first == second
    assert len(first) == 64
    assert "audio" not in first


def test_cache_path_is_scoped_to_a_whitelisted_category(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)
    key = "a" * 64

    path = module.cache_path("metadata", key, ".json", namespace="waveforms")

    assert path == (
        tmp_path
        / "user"
        / "__alice_lab_audio_tools"
        / "cache"
        / "metadata"
        / "waveforms"
        / f"{key}.json"
    )
    with pytest.raises(ValueError, match="Unknown"):
        module.cache_path("../outside", key, ".json")
    with pytest.raises(ValueError, match="namespace"):
        module.cache_path("metadata", key, ".json", namespace="../outside")


def test_cache_survives_a_changed_or_cleaned_temp_directory(tmp_path, monkeypatch) -> None:
    user_directory = tmp_path / "persistent-user"
    first_runtime = _load_module(tmp_path / "first-temp", monkeypatch, user_directory)
    key = first_runtime.cache_key("restart-test", "same-source")
    first_path = first_runtime.cache_path("metadata", key, ".json")
    first_runtime.atomic_write_json(first_path, {"cached": True})

    second_runtime = _load_module(tmp_path / "second-temp", monkeypatch, user_directory)
    second_path = second_runtime.cache_path("metadata", key, ".json")

    assert second_path == first_path
    assert second_path.read_text(encoding="utf-8") == '{"cached":true}'


def test_clear_cache_removes_only_selected_category(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)
    root = tmp_path / "user" / "__alice_lab_audio_tools" / "cache"
    transcript = root / "transcripts" / "one.json"
    media = root / "media" / "one.mp4"
    transcript.parent.mkdir(parents=True)
    media.parent.mkdir(parents=True)
    transcript.write_bytes(b"abc")
    media.write_bytes(b"12345")

    removed = module.clear_cache("transcripts")

    assert removed == {"transcripts": {"files": 1, "bytes": 3}}
    assert not transcript.exists()
    assert media.read_bytes() == b"12345"


def test_clear_cache_unlinks_symlink_without_touching_target(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    category = tmp_path / "user" / "__alice_lab_audio_tools" / "cache" / "media"
    category.mkdir(parents=True)
    (category / "escape").symlink_to(outside)

    removed = module.clear_cache("media")

    assert removed["media"]["files"] == 1
    assert outside.read_text(encoding="utf-8") == "keep"


def test_clear_cache_rejects_category_symlink(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "user" / "__alice_lab_audio_tools" / "cache"
    root.mkdir(parents=True)
    (root / "media").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="escaped|Unsafe"):
        module.clear_cache("media")


def test_cache_path_rejects_namespace_symlink(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    category = (
        tmp_path / "user" / "__alice_lab_audio_tools" / "cache" / "metadata"
    )
    category.mkdir(parents=True)
    (category / "waveforms").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="namespace"):
        module.cache_path("metadata", "a" * 64, ".json", namespace="waveforms")


def test_cache_manager_requires_explicit_confirmation(tmp_path, monkeypatch) -> None:
    store = _load_module(tmp_path, monkeypatch)
    manager = _load_manager(store, monkeypatch).AliceLabCacheManager()

    with pytest.raises(ValueError, match="confirm_clear"):
        manager.manage("clear", "all", False)


def test_cache_manager_reports_and_clears_selected_category(tmp_path, monkeypatch) -> None:
    store = _load_module(tmp_path, monkeypatch)
    manager = _load_manager(store, monkeypatch).AliceLabCacheManager()
    cached = (
        tmp_path
        / "user"
        / "__alice_lab_audio_tools"
        / "cache"
        / "metadata"
        / "one.json"
    )
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"1234")

    inspected = manager.manage("inspect", "metadata", False)["result"][0]
    cleared = manager.manage("clear", "metadata", True)["result"][0]
    inspected_report = json.loads(inspected)
    cleared_report = json.loads(cleared)
    expected_root = tmp_path / "user" / "__alice_lab_audio_tools" / "cache"

    assert '"files": 1' in inspected
    assert '"bytes": 4' in inspected
    assert inspected_report["cache_root"] == str(expected_root)
    assert inspected_report["category_paths"] == {
        "metadata": str(expected_root / "metadata")
    }
    assert '"files": 1' in cleared
    assert cleared_report["cache_root"] == str(expected_root)
    assert cleared_report["category_paths"] == {
        "metadata": str(expected_root / "metadata")
    }
    assert not cached.exists()


def test_cache_manager_reports_all_category_locations(tmp_path, monkeypatch) -> None:
    store = _load_module(tmp_path, monkeypatch)
    manager = _load_manager(store, monkeypatch).AliceLabCacheManager()
    root = tmp_path / "user" / "__alice_lab_audio_tools" / "cache"

    report = json.loads(manager.manage("inspect", "all", False)["result"][0])

    assert report["cache_root"] == str(root)
    assert report["category_paths"] == {
        category: str(root / category) for category in store.CACHE_CATEGORIES
    }

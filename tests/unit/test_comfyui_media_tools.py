from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "alice_test_media_tools",
    Path(__file__).parents[2] / "media_tools.py",
)
assert _SPEC and _SPEC.loader
media_tools = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(media_tools)


def test_resolve_media_tool_prefers_process_path(monkeypatch) -> None:
    monkeypatch.setattr(media_tools.shutil, "which", lambda name: f"/path/bin/{name}")

    assert media_tools.resolve_media_tool("ffmpeg") == "/path/bin/ffmpeg"


def test_resolve_media_tool_uses_fallback_directory(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "ffprobe"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(media_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(media_tools, "_FALLBACK_BIN_DIRECTORIES", (tmp_path,))

    assert media_tools.resolve_media_tool("ffprobe") == str(executable)


def test_resolve_media_tool_reports_missing_executable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(media_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(media_tools, "_FALLBACK_BIN_DIRECTORIES", (tmp_path,))

    with pytest.raises(RuntimeError, match="ffmpeg was not found"):
        media_tools.resolve_media_tool("ffmpeg")

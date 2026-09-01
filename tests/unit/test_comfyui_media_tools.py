from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "alice_test_media_tools",
    Path(__file__).parents[2] / "src" / "media_tools.py",
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


def test_auto_video_encoder_uses_first_working_hardware_encoder(monkeypatch) -> None:
    checked = []

    def can_encode(_ffmpeg, encoder):
        checked.append(encoder)
        return encoder == "nvenc"

    monkeypatch.setattr(media_tools.platform, "system", lambda: "Linux")
    monkeypatch.setattr(media_tools, "_can_encode_video", can_encode)

    assert media_tools.resolve_video_encoder("ffmpeg", "auto") == "nvenc"
    assert checked == ["nvenc"]


def test_auto_video_encoder_falls_back_to_cpu(monkeypatch) -> None:
    monkeypatch.setattr(media_tools.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        media_tools,
        "_can_encode_video",
        lambda _ffmpeg, encoder: encoder == "cpu",
    )

    assert media_tools.resolve_video_encoder("ffmpeg", "auto") == "cpu"


def test_auto_video_encoder_prefers_videotoolbox_on_macos(monkeypatch) -> None:
    checked = []

    def can_encode(_ffmpeg, encoder):
        checked.append(encoder)
        return True

    monkeypatch.setattr(media_tools.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(media_tools, "_can_encode_video", can_encode)

    assert media_tools.resolve_video_encoder("ffmpeg", "auto") == "videotoolbox"
    assert checked == ["videotoolbox"]


def test_explicit_unavailable_video_encoder_does_not_fall_back(monkeypatch) -> None:
    monkeypatch.setattr(media_tools, "_can_encode_video", lambda _ffmpeg, _encoder: False)

    with pytest.raises(RuntimeError, match="h264_nvenc.*not usable"):
        media_tools.resolve_video_encoder("ffmpeg", "nvenc")


def test_encoder_probe_uses_a_hardware_compatible_frame_size(monkeypatch) -> None:
    captured = {}

    def run(command, **_kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    media_tools._can_encode_video.cache_clear()
    monkeypatch.setattr(media_tools.subprocess, "run", run)

    assert media_tools._can_encode_video("ffmpeg", "nvenc") is True
    assert "color=c=black:s=256x256:d=0.04" in captured["command"]


@pytest.mark.parametrize(
    ("keyframes", "duration", "expected"),
    [
        ([0.0, 2.0, 4.0], 6.0, False),
        ([6.0, 5.0, 0.0], 10.0, True),
        ([], 10.0, True),
    ],
)
def test_seekable_preview_is_only_needed_for_sparse_keyframes(
    monkeypatch, tmp_path, keyframes, duration, expected
) -> None:
    payload = {
        "frames": [
            {"best_effort_timestamp_time": str(timestamp)}
            for timestamp in keyframes
        ],
        "format": {"duration": str(duration)},
    }
    monkeypatch.setattr(media_tools, "resolve_media_tool", lambda _name: "/bin/ffprobe")
    monkeypatch.setattr(
        media_tools.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    assert media_tools.needs_seekable_video_preview(tmp_path / "video.mp4") is expected

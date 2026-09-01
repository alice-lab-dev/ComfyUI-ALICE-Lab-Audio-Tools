from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


_ROOT = Path(__file__).parents[2]
_SRC = _ROOT / "src"
_PACKAGE_NAME = "alice_test_video_output_package"
_PACKAGE = ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [str(_ROOT)]
sys.modules[_PACKAGE_NAME] = _PACKAGE


def _load_relative(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{_PACKAGE_NAME}.{name}",
        _SRC / f"{name}.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_load_relative("media_tools")
_load_relative("video_encoding")

folder_paths = ModuleType("folder_paths")
folder_paths.get_temp_directory = lambda: "/tmp"
sys.modules["folder_paths"] = folder_paths

server = ModuleType("server")
server.PromptServer = SimpleNamespace(
    instance=SimpleNamespace(routes=SimpleNamespace(get=lambda _path: lambda func: func))
)
server.web = SimpleNamespace(Response=object, FileResponse=object)
sys.modules["server"] = server


class FakeVideoFromFile:
    pass


latest = ModuleType("comfy_api.latest")
latest.InputImpl = SimpleNamespace(VideoFromFile=FakeVideoFromFile)
latest.Types = SimpleNamespace(VideoContainer=SimpleNamespace(MP4="mp4"))
sys.modules["comfy_api"] = ModuleType("comfy_api")
sys.modules["comfy_api.latest"] = latest

video_output = _load_relative("video_output")


class MockFileVideo(FakeVideoFromFile):
    def __init__(self, source: Path, start: float, duration: float):
        self.source = source
        self.start = start
        self.duration = duration

    def get_stream_source(self):
        return str(self.source)

    def get_active_trim_window(self):
        return self.start, self.duration


def _successful_run(command):
    output = Path(command[-1])
    output.write_bytes(b"preview")
    return SimpleNamespace(returncode=0, stderr="")


def test_untrimmed_file_preview_uses_stream_copy(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "output.mp4"
    commands = []

    monkeypatch.setattr(video_output, "resolve_media_tool", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        video_output,
        "_run_preview_ffmpeg",
        lambda command: commands.append(command) or _successful_run(command),
    )

    used = video_output._write_file_video_preview(
        MockFileVideo(source, 0.0, 0.0), output, "auto"
    )

    assert used == "copy"
    assert commands[0][commands[0].index("-c:v") + 1] == "copy"


def test_trimmed_file_preview_uses_resolved_nvenc(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "output.mp4"
    commands = []

    monkeypatch.setattr(video_output, "resolve_media_tool", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        video_output,
        "resolve_video_encoder",
        lambda _ffmpeg, requested: "nvenc" if requested == "auto" else requested,
    )
    monkeypatch.setattr(
        video_output,
        "_run_preview_ffmpeg",
        lambda command: commands.append(command) or _successful_run(command),
    )

    used = video_output._write_file_video_preview(
        MockFileVideo(source, 1.25, 5.5), output, "auto"
    )

    assert used == "nvenc"
    command = commands[0]
    assert command[command.index("-c:v") + 1] == "h264_nvenc"
    assert command[command.index("-ss") + 1] == "1.250000000"
    assert command[command.index("-t") + 1] == "5.500000000"

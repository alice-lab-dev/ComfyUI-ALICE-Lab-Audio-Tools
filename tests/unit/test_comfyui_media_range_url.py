from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


_SRC = Path(__file__).parents[2] / "src"
_PACKAGE_NAME = "alice_test_media_range_url_package"
_PACKAGE = ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [str(_SRC)]
sys.modules[_PACKAGE_NAME] = _PACKAGE

_ENCODING_SPEC = importlib.util.spec_from_file_location(
    f"{_PACKAGE_NAME}.video_encoding",
    _SRC / "video_encoding.py",
)
assert _ENCODING_SPEC and _ENCODING_SPEC.loader
_ENCODING_MODULE = importlib.util.module_from_spec(_ENCODING_SPEC)
sys.modules[_ENCODING_SPEC.name] = _ENCODING_MODULE
_ENCODING_SPEC.loader.exec_module(_ENCODING_MODULE)

_MODULE_SPEC = importlib.util.spec_from_file_location(
    f"{_PACKAGE_NAME}.media_range_url",
    _SRC / "media_range_url.py",
)
assert _MODULE_SPEC and _MODULE_SPEC.loader
_MODULE = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(_MODULE)


def test_untrimmed_preview_command_uses_stream_copy() -> None:
    command = _ENCODING_MODULE.build_video_preview_command(
        "ffmpeg",
        "/tmp/source.mp4",
        "/tmp/preview.mp4",
        start_seconds=0.0,
        duration_seconds=0.0,
        video_encoder=None,
        copy_audio=True,
    )

    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "copy"
    assert "-ss" not in command
    assert "-t" not in command


def test_trimmed_preview_command_uses_selected_hardware_encoder() -> None:
    command = _ENCODING_MODULE.build_video_preview_command(
        "ffmpeg",
        "/tmp/source.mp4",
        "/tmp/preview.mp4",
        start_seconds=2.5,
        duration_seconds=7.25,
        video_encoder="nvenc",
    )

    assert command[command.index("-c:v") + 1] == "h264_nvenc"
    assert command[command.index("-ss") + 1] == "2.500000000"
    assert command[command.index("-t") + 1] == "7.250000000"
    assert command[command.index("-map") + 1] == "0:v:0"
    assert "0:a:0?" in command


def test_preview_video_node_exposes_encoder_selection_and_result_status() -> None:
    source_path = _SRC / "video_output.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    preview_class = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.ClassDef)
        and statement.name == "AliceLabOutputFFmpeg"
    )
    source = ast.unparse(preview_class)

    assert "video_encoder" in source
    assert "_write_file_video_preview" in source
    assert "used_encoder" in source


def test_http_and_https_direct_urls_are_accepted() -> None:
    assert _MODULE.validate_media_url("http://example.com/media.mp4") == (
        "http://example.com/media.mp4"
    )
    assert _MODULE.validate_media_url(" https://example.com/audio.wav ") == (
        "https://example.com/audio.wav"
    )


@pytest.mark.parametrize(
    "url",
    ["", "file:///tmp/video.mp4", "ftp://example.com/a.mp4", "https://example.com/a\n.mp4"],
)
def test_non_http_media_urls_are_rejected(url: str) -> None:
    with pytest.raises(
        ValueError,
        match="direct media URL|http:// or https://|control characters",
    ):
        _MODULE.validate_media_url(url)


def test_video_clip_command_preserves_signed_query_as_one_argv_value() -> None:
    url = "https://cdn.example/video.mp4?token=a%2Bb&expires=123&x=1"
    command = _MODULE.build_url_clip_command(
        "/usr/bin/ffmpeg",
        url,
        10.0,
        20.0,
        "/tmp/clip.part.mp4",
        has_video=True,
    )

    assert command[command.index("-i") + 1] == url
    assert command.count(url) == 1
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-t") + 1] == "10.000000000"
    assert ["-map", "0:v:0"] == command[command.index("-map") : command.index("-map") + 2]
    assert "libx264" in command
    assert command[-2:] == ["-y", "/tmp/clip.part.mp4"]


@pytest.mark.parametrize(
    ("selection", "codec"),
    [
        ("nvenc", "h264_nvenc"),
        ("videotoolbox", "h264_videotoolbox"),
        ("cpu", "libx264"),
    ],
)
def test_video_clip_command_uses_selected_encoder(selection: str, codec: str) -> None:
    command = _MODULE.build_url_clip_command(
        "ffmpeg",
        "https://example.com/video.mp4",
        0.0,
        1.0,
        "/tmp/clip.mp4",
        has_video=True,
        video_encoder=selection,
    )

    assert command[command.index("-c:v") + 1] == codec


def test_url_node_exposes_encoder_selection_without_changing_existing_defaults() -> None:
    nodes_path = Path(__file__).parents[2] / "src" / "nodes.py"
    module = ast.parse(nodes_path.read_text(encoding="utf-8"))
    url_class = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.ClassDef)
        and statement.name == "AliceLabMediaRangeURL"
    )
    input_types = next(
        statement
        for statement in url_class.body
        if isinstance(statement, ast.FunctionDef) and statement.name == "INPUT_TYPES"
    )

    assert "video_encoder" in ast.unparse(input_types)
    extract = next(
        statement
        for statement in url_class.body
        if isinstance(statement, ast.FunctionDef) and statement.name == "extract"
    )
    assert extract.args.defaults[-1].value == "auto"


def test_replace_video_audio_exposes_encoder_selection_with_auto_default() -> None:
    source_path = Path(__file__).parents[2] / "src" / "video_audio_replace.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    replace_class = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.ClassDef)
        and statement.name == "AliceLabReplaceVideoAudio"
    )
    source = ast.unparse(replace_class)

    assert "video_encoder" in source
    replace = next(
        statement
        for statement in replace_class.body
        if isinstance(statement, ast.FunctionDef) and statement.name == "replace"
    )
    assert replace.args.defaults[-1].value == "auto"


def test_audio_only_clip_command_writes_a_browser_compatible_wav() -> None:
    command = _MODULE.build_url_clip_command(
        "ffmpeg",
        "https://example.com/audio.mp3",
        1.25,
        2.75,
        "/tmp/clip.part.wav",
        has_video=False,
    )

    assert "-vn" in command
    assert "pcm_s16le" in command
    assert command[command.index("-ac") + 1] == "2"
    assert command[command.index("-ar") + 1] == "44100"


def test_invalid_or_non_finite_clip_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="later than start"):
        _MODULE.build_url_clip_command(
            "ffmpeg",
            "https://example.com/video.mp4",
            5.0,
            5.0,
            "/tmp/clip.mp4",
            has_video=True,
        )


def test_url_failure_is_actionable_and_redacts_signed_query() -> None:
    url = "https://cdn.example/video.mp4?secret=do-not-show&expires=1"
    message = _MODULE.media_url_failure(f"HTTP 403 while opening {url}", url)

    assert "Failed to open media URL" in message
    assert "may have expired" in message
    assert "HTTP 403" in message
    assert "secret=do-not-show" not in message
    assert message.endswith("https://cdn.example/video.mp4")


def test_display_name_omits_embedded_credentials_and_query() -> None:
    assert _MODULE.media_url_display_name(
        "https://user:password@cdn.example:8443/video.mp4?token=secret"
    ) == "https://cdn.example:8443/video.mp4"


def test_remote_reads_have_a_finite_io_timeout() -> None:
    args = _MODULE.remote_input_args()

    assert args[0] == "-rw_timeout"
    assert 0 < int(args[1]) <= 60_000_000


def test_url_node_never_materializes_video_components() -> None:
    nodes_path = Path(__file__).parents[2] / "src" / "nodes.py"
    module = ast.parse(nodes_path.read_text(encoding="utf-8"))
    url_class = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.ClassDef)
        and statement.name == "AliceLabMediaRangeURL"
    )
    attributes = {
        node.attr
        for node in ast.walk(url_class)
        if isinstance(node, ast.Attribute)
    }

    assert "get_components" not in attributes
    assert "VideoFromFile" in attributes


def test_url_node_reuses_a_content_addressed_interval_cache() -> None:
    nodes_path = Path(__file__).parents[2] / "src" / "nodes.py"
    module = ast.parse(nodes_path.read_text(encoding="utf-8"))
    url_class = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.ClassDef)
        and statement.name == "AliceLabMediaRangeURL"
    )
    extract = next(
        statement
        for statement in url_class.body
        if isinstance(statement, ast.FunctionDef) and statement.name == "extract"
    )
    source = ast.unparse(extract)

    assert "url-range-media" in source
    assert "cache_path('media', clip_key, suffix, namespace='url_ranges')" in source
    assert "_input_preview_name(f'url:{clip_key}', clip_path)" in source

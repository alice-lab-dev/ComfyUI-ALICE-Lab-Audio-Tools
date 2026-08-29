from __future__ import annotations

import hashlib
import asyncio
import json
import math
import subprocess
import uuid
from array import array
from pathlib import Path

import folder_paths
import server
import torch
from comfy.cli_args import args
from comfy_api.latest import InputImpl, Types

from .cache_manager import AliceLabCacheManager
from .cache_store import (
    atomic_write_json,
    cache_key,
    cache_path,
    local_source_identity,
)
from .audio_compare import (
    AliceLabCompareAudio,
    analyse_compare_region,
    compare_audio_wav,
)
from .audio_output import AliceLabOutputWaveform
from .float_output import AliceLabOutputFloat
from .media_tools import needs_seekable_video_preview, resolve_media_tool
from .media_range_input import (
    audio_duration,
    normalize_local_range,
    normalize_range,
    range_ui_payload,
    slice_video_components,
    trim_audio,
    validate_static_range,
)
from .media_range_url import (
    build_url_clip_command,
    media_url_display_name,
    media_url_failure,
    remote_input_args,
    validate_media_url,
)
from .mixer import AliceLabAudioMixer
from .video_audio_replace import AliceLabReplaceVideoAudio, _write_audio_wave
from .video_output import AliceLabOutputFFmpeg
from .audio_spectrogram import AliceLabSpectrogram
from .irodori_ref_config import AliceLabAudioToIrodoriRefConfig
from .transcript_range import AliceLabTranscriptRangeSelector
from .video_frames import AliceLabVideoFirstLastFrame


MEDIA_EXTENSIONS = {
    ".aac", ".aiff", ".avi", ".flac", ".m2ts", ".m4a", ".m4v",
    ".mkv", ".mov", ".mp3", ".mp4", ".mpg", ".mpeg", ".ogg",
    ".opus", ".ts", ".wav", ".webm", ".wma",
}


_input_preview_sources: dict[str, Path] = {}


def _input_preview_name(key: str, path: Path) -> str:
    """Register a server-owned preview source under a stable opaque name."""
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    suffix = ".wav" if path.suffix.lower() == ".wav" else ".mp4"
    name = f"alice_lab_media_range_input_{digest}{suffix}"
    if len(_input_preview_sources) >= 128 and name not in _input_preview_sources:
        _input_preview_sources.pop(next(iter(_input_preview_sources)))
    _input_preview_sources[name] = path
    return name


def _media_files() -> list[str]:
    """Return media paths relative to ComfyUI's input directory for the combo UI."""
    input_dir = Path(folder_paths.get_input_directory())
    return sorted(
        str(path.relative_to(input_dir))
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    )


def _resolve_input(filename: str) -> Path:
    """Validate a client filename and resolve it to a supported input file.

    The containment check is deliberately performed before resolving symlinks.
    This permits media libraries explicitly linked into ``ComfyUI/input`` while
    still rejecting absolute paths and ``..`` traversal supplied by a client.
    """
    if not filename or Path(filename).is_absolute():
        raise ValueError(f"Invalid ComfyUI input file: {filename}")
    input_root = Path(folder_paths.get_input_directory()).resolve()
    input_entry = input_root / filename
    try:
        input_entry.absolute().relative_to(input_root)
    except ValueError as error:
        raise ValueError("Load Media Range (Upload) only accepts files inside ComfyUI/input") from error
    path = input_entry.resolve()
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError(f"Unsupported media file: {filename}")
    return path


def _resolve_external(filename: str) -> Path:
    """Resolve an absolute media path for the explicit Path variant of the node."""
    path = Path(filename).expanduser()
    if not filename or not path.is_absolute():
        raise ValueError("Load Media Range (Path) requires an absolute path")
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError(f"Unsupported media file: {filename}")
    return path


def _resolve_preview_media(filename: str) -> Path:
    """Resolve only temporary previews created by Media Range (Input)."""
    name = Path(filename).name
    if name != filename or not name.startswith("alice_lab_media_range_input_"):
        raise ValueError("Invalid Media Range input preview")
    if Path(name).suffix.lower() not in {".mp4", ".wav"}:
        raise ValueError("Unsupported Media Range input preview")
    path = _input_preview_sources.get(name)
    if path is None:
        path = Path(folder_paths.get_temp_directory()) / name
    if not path.is_file():
        raise ValueError("Media Range input preview expired; run the node again")
    return path


def _resolve_request_media(request) -> Path:
    """Resolve either an input-relative filename or an explicit Path-node source."""
    preview = request.rel_url.query.get("preview")
    if preview is not None:
        return _resolve_preview_media(preview)
    external = request.rel_url.query.get("path")
    if external is not None:
        return _resolve_external(external)
    return _resolve_input(request.rel_url.query.get("filename", ""))


def _prompt_input_is_linked(prompt, unique_id, input_name: str) -> bool:
    """Return whether one input of this node is supplied by an upstream link."""
    if not isinstance(prompt, dict) or unique_id is None:
        return False
    node = prompt.get(str(unique_id), prompt.get(unique_id))
    if not isinstance(node, dict):
        return False
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return False
    value = inputs.get(input_name)
    return isinstance(value, (list, tuple)) and len(value) == 2


def _probe(
    path: str | Path,
    require_audio: bool = True,
    input_args: list[str] | None = None,
) -> dict[str, object]:
    """Read the small subset of ffprobe metadata needed by the node and UI."""
    completed = subprocess.run(
        [
            resolve_media_tool("ffprobe"), "-v", "error", "-show_entries",
            (
                "format=duration:stream=codec_type,width,height,avg_frame_rate:"
                "stream_disposition=attached_pic"
            ),
            "-of", "json", *(input_args or []), str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    data = json.loads(completed.stdout)
    streams = data.get("streams", [])
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    video = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and not bool(stream.get("disposition", {}).get("attached_pic", 0))
        ),
        None,
    )
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    if duration <= 0 or (require_audio and not has_audio):
        raise ValueError("The selected file has no usable audio duration")
    return {
        "duration": duration,
        "has_video": video is not None,
        "has_audio": has_audio,
        "width": int(video.get("width", 0)) if video else 0,
        "height": int(video.get("height", 0)) if video else 0,
    }


def _waveform(path: Path, point_count: int) -> list[float]:
    """Decode a low-rate mono signal and reduce it to normalized peak buckets."""
    point_count = max(256, min(point_count, 8000))
    completed = subprocess.run(
        [
            resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar",
            "2000", "-f", "s16le", "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    samples = array("h")
    samples.frombytes(completed.stdout[: len(completed.stdout) // 2 * 2])
    if not samples:
        return []
    count = min(point_count, len(samples))
    peaks: list[float] = []
    # Integer bucket boundaries cover every decoded sample without accumulating
    # the rounding error that a floating-point step size would introduce.
    for index in range(count):
        start = index * len(samples) // count
        end = max(start + 1, (index + 1) * len(samples) // count)
        peaks.append(min(1.0, max(abs(value) for value in samples[start:end]) / 32768.0))
    return peaks


def _waveform_detail(
    path: Path,
    point_count: int,
    start_seconds: float,
    end_seconds: float,
) -> list[list[float]]:
    """Decode only one viewport and return signed min/max sample buckets.

    The decode rate rises as the viewport narrows. Long overviews therefore
    remain inexpensive, while sample-level zoom approaches the source detail
    available through ComfyUI's standard 44.1 kHz audio representation.
    """
    point_count = max(256, min(int(point_count), 12000))
    start = max(0.0, float(start_seconds))
    span = max(1 / 44100, float(end_seconds) - start)
    sample_rate = min(44100, max(2000, int(point_count * 4 / span)))
    completed = subprocess.run(
        [
            resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-nostdin", "-ss", f"{start:.9f}", "-i", str(path), "-t",
            f"{span:.9f}", "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-f", "s16le", "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    samples = array("h")
    samples.frombytes(completed.stdout[: len(completed.stdout) // 2 * 2])
    if not samples:
        return []
    count = min(point_count, len(samples))
    envelope: list[list[float]] = []
    for index in range(count):
        bucket_start = index * len(samples) // count
        bucket_end = max(bucket_start + 1, (index + 1) * len(samples) // count)
        bucket = samples[bucket_start:bucket_end]
        envelope.append([
            max(-1.0, min(bucket) / 32768.0),
            min(1.0, max(bucket) / 32768.0),
        ])
    return envelope


def _extract_audio(path: Path, start_seconds: float, end_seconds: float) -> dict:
    """Decode an A-B range into ComfyUI's batched AUDIO tensor representation."""
    duration = end_seconds - start_seconds
    completed = subprocess.run(
        [
            resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-nostdin", "-ss", f"{start_seconds:.6f}", "-i", str(path),
            "-t", f"{duration:.6f}", "-vn", "-ac", "2", "-ar", "44100",
            "-f", "f32le", "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    audio = torch.frombuffer(bytearray(completed.stdout), dtype=torch.float32)
    if audio.numel() == 0:
        raise ValueError("No audio was decoded from the selected range")
    waveform = audio.reshape((-1, 2)).transpose(0, 1).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": 44100}


def _cached_input_video_preview(video, total: float) -> tuple[str, Path, float]:
    """Register a stable VIDEO source without copying file-backed inputs."""
    if isinstance(video, InputImpl.VideoFromFile):
        source = video.get_stream_source()
        active_start, active_duration = video.get_active_trim_window()
        if isinstance(source, (str, Path)):
            source_path = Path(source).resolve()
            stat = source_path.stat()
            key = (
                f"file:{source_path}:{stat.st_mtime_ns}:{stat.st_size}:"
                f"{active_start:.9f}:{active_duration:.9f}"
            )
            # Keep the original source path. The preview endpoint receives the
            # active A-B window and transcodes only that interval for the UI.
            return _input_preview_name(key, source_path), source_path, active_start
        else:
            key = f"buffer-window:{id(video)}:{active_start:.9f}:{active_duration:.9f}"
    else:
        key = f"components:{id(video)}:{total:.9f}"

    digest = cache_key("input-video-preview", key)
    preview_path = cache_path("media", digest, ".mp4", namespace="input_video")
    if not preview_path.is_file() or preview_path.stat().st_size == 0:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        partial = preview_path.with_suffix(".part.mp4")
        partial.unlink(missing_ok=True)
        video.save_to(
            str(partial),
            format=Types.VideoContainer.MP4,
            codec="auto",
            metadata=None,
        )
        if not partial.is_file() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            raise RuntimeError("Media Range (Input) could not create its video preview")
        partial.replace(preview_path)
    return _input_preview_name(key, preview_path), preview_path, 0.0


def _trim_component_video(
    video,
    input_start: float,
    input_end: float,
    a: float,
    b: float,
    total: float,
):
    """Encode one coarse tensor-backed window, then lazily trim its local A-B."""
    source_components = video.get_components()
    sliced = slice_video_components(source_components, input_start, input_end, total)
    components = Types.VideoComponents(
        images=sliced["images"],
        audio=sliced["audio"],
        frame_rate=sliced["frame_rate"],
        metadata=sliced["metadata"],
        alpha=sliced["alpha"],
    )
    compact = InputImpl.VideoFromComponents(
        components,
        bit_depth=video.get_bit_depth(),
        color_space=video.get_color_space(),
    )
    key = (
        f"component-window:{id(video)}:{total:.9f}:"
        f"{input_start:.9f}:{input_end:.9f}"
    )
    digest = cache_key("component-video-preview", key)
    preview_path = cache_path("media", digest, ".mp4", namespace="input_video")
    if not preview_path.is_file() or preview_path.stat().st_size == 0:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        partial = preview_path.with_suffix(".part.mp4")
        partial.unlink(missing_ok=True)
        compact.save_to(
            str(partial),
            format=Types.VideoContainer.MP4,
            codec="auto",
            metadata=None,
        )
        if not partial.is_file() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            raise RuntimeError("Media Range (Input) could not create its video preview")
        partial.replace(preview_path)

    selected = InputImpl.VideoFromFile(str(preview_path)).as_trimmed(
        start_time=sliced["frame_offset"] + a,
        duration=b - a,
        strict_duration=False,
    )
    if selected is None:
        raise ValueError("The selected video range could not be created")
    selected_audio = None
    if sliced["audio"] is not None:
        selected_audio, _, _, _ = trim_audio(
            sliced["audio"],
            sliced["frame_offset"] + a,
            sliced["frame_offset"] + b,
        )
    preview_name = _input_preview_name(key, preview_path)
    frame_start = input_start - sliced["frame_offset"]

    waveform_name = preview_name
    waveform_offset = -frame_start
    if source_components.audio is not None:
        audio_key = f"component-audio:{id(video)}:{total:.9f}"
        audio_digest = cache_key("component-audio-preview", audio_key)
        audio_path = cache_path("media", audio_digest, ".wav", namespace="input_audio")
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            partial_audio = audio_path.with_suffix(".part.wav")
            partial_audio.unlink(missing_ok=True)
            _write_audio_wave(partial_audio, source_components.audio)
            partial_audio.replace(audio_path)
        waveform_name = _input_preview_name(audio_key, audio_path)
        waveform_offset = 0.0
    return (
        selected,
        selected_audio,
        preview_name,
        preview_path,
        -frame_start,
        waveform_name,
        waveform_offset,
    )


class AliceLabMediaRange:
    """ComfyUI node that exposes one shared A-B range as audio and video."""

    @classmethod
    def INPUT_TYPES(cls):
        files = _media_files()
        if not files:
            files = [""]
        return {
            "required": {
                "media": (files,),
                "start_seconds": (
                    "FLOAT",
                    {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.001},
                ),
                "end_seconds": (
                    "FLOAT",
                    {"default": 10.0, "min": 0.001, "max": 86400.0, "step": 0.001},
                ),
            }
        }

    RETURN_TYPES = ("AUDIO", "FLOAT", "FLOAT", "FLOAT", "VIDEO")
    RETURN_NAMES = (
        "audio",
        "start_seconds",
        "end_seconds",
        "duration_seconds",
        "video",
    )
    FUNCTION = "extract"
    CATEGORY = "ALICE_Lab/Media"
    DESCRIPTION = "Select a media range with an interactive waveform and output its audio."

    def extract(self, media: str, start_seconds: float, end_seconds: float):
        """Validate the range and produce synchronized trimmed outputs."""
        path = _resolve_input(media)
        info = _probe(path)
        total = float(info["duration"])
        start = max(0.0, float(start_seconds))
        end = min(total, float(end_seconds))
        if end <= start:
            raise ValueError("End time must be later than start time")
        audio = _extract_audio(path, start, end)
        video = None
        if bool(info["has_video"]):
            video = InputImpl.VideoFromFile(str(path)).as_trimmed(
                start,
                end - start,
                strict_duration=True,
            )
            if video is None:
                raise ValueError("The selected video range could not be created")
        result = (audio, start, end, end - start, video)
        return {
            "ui": {
                "alice_lab_media_range": [
                    json.dumps(range_ui_payload(media, start, end))
                ]
            },
            "result": result,
        }

    @classmethod
    def IS_CHANGED(cls, media: str, start_seconds: float, end_seconds: float):
        """Invalidate ComfyUI's cache when the source file or range changes."""
        path = _resolve_input(media)
        stat = path.stat()
        payload = f"{path}:{stat.st_mtime_ns}:{stat.st_size}:{start_seconds}:{end_seconds}"
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def VALIDATE_INPUTS(cls, media: str, start_seconds: float, end_seconds: float):
        """Reject invalid media and inverted ranges before queue execution."""
        try:
            _resolve_input(media)
        except ValueError as error:
            return str(error)
        return validate_static_range(start_seconds, end_seconds)


class AliceLabMediaRangePath(AliceLabMediaRange):
    """Media Range variant that reads a large source directly by absolute path."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "media_path": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "/path/to/audio-or-video.mp4",
                        "tooltip": (
                            "Enter an absolute path or connect a STRING output. "
                            "A connected value takes precedence over this widget."
                        ),
                        # Video Helper Suite upgrades STRING widgets carrying
                        # this metadata to its server-side path browser. Without
                        # VHS installed this remains a normal editable field.
                        "vhs_path_extensions": sorted(
                            extension.removeprefix(".")
                            for extension in MEDIA_EXTENSIONS
                        ),
                    },
                ),
                "start_seconds": (
                    "FLOAT",
                    {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.001},
                ),
                "end_seconds": (
                    "FLOAT",
                    {"default": 10.0, "min": 0.001, "max": 86400.0, "step": 0.001},
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    DESCRIPTION = "Select a range from media at an absolute path without copying it."

    def extract(
        self,
        media_path: str,
        start_seconds: float,
        end_seconds: float,
        prompt=None,
        unique_id=None,
    ):
        path = _resolve_external(media_path)
        previous_path = getattr(self, "_last_media_path", None)
        path_is_linked = _prompt_input_is_linked(prompt, unique_id, "media_path")
        range_is_linked = any(
            _prompt_input_is_linked(prompt, unique_id, name)
            for name in ("start_seconds", "end_seconds")
        )
        reset_range = (
            path_is_linked
            and previous_path != path
            and not range_is_linked
        )
        result = self._extract_path(
            path,
            start_seconds,
            end_seconds,
            reset_range=reset_range,
        )
        self._last_media_path = path
        return {
            "ui": {
                "alice_lab_media_range": [
                    json.dumps(range_ui_payload(str(path), result[1], result[2]))
                ]
            },
            "result": result,
        }

    @staticmethod
    def _extract_path(
        path: Path,
        start_seconds: float,
        end_seconds: float,
        *,
        reset_range: bool = False,
    ):
        info = _probe(path)
        total = float(info["duration"])
        start = 0.0 if reset_range else max(0.0, float(start_seconds))
        end = total if reset_range else min(total, float(end_seconds))
        if end <= start:
            raise ValueError("End time must be later than start time")
        audio = _extract_audio(path, start, end)
        video = None
        if bool(info["has_video"]):
            video = InputImpl.VideoFromFile(str(path)).as_trimmed(
                start, end - start, strict_duration=True
            )
            if video is None:
                raise ValueError("The selected video range could not be created")
        return audio, start, end, end - start, video

    @classmethod
    def IS_CHANGED(
        cls,
        media_path: str,
        start_seconds: float,
        end_seconds: float,
        prompt=None,
        unique_id=None,
    ):
        path = _resolve_external(media_path)
        stat = path.stat()
        payload = f"{path}:{stat.st_mtime_ns}:{stat.st_size}:{start_seconds}:{end_seconds}"
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def VALIDATE_INPUTS(
        cls,
        media_path: str,
        start_seconds: float,
        end_seconds: float,
        prompt=None,
        unique_id=None,
    ):
        # ComfyUI supplies ``None`` while a required primitive input is linked;
        # the resolved STRING is validated normally when the node executes.
        if media_path is None:
            return validate_static_range(start_seconds, end_seconds)
        try:
            _resolve_external(media_path)
        except ValueError as error:
            return str(error)
        return validate_static_range(start_seconds, end_seconds)


class AliceLabMediaRangeURL(AliceLabMediaRange):
    """Read only one requested interval from a direct HTTP(S) media URL."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "url": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "https://example.com/video.mp4",
                        "dynamicPrompts": False,
                    },
                ),
                "start_seconds": (
                    "FLOAT",
                    {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.001},
                ),
                "end_seconds": (
                    "FLOAT",
                    {"default": 10.0, "min": 0.001, "max": 86400.0, "step": 0.001},
                ),
            }
        }

    DESCRIPTION = (
        "Read a selected range from a direct FFmpeg-compatible HTTP(S) media URL. "
        "Page URL resolution is intentionally not included."
    )
    OUTPUT_NODE = True

    def extract(self, url: str, start_seconds: float, end_seconds: float):
        source = validate_media_url(url)
        try:
            info = _probe(source, input_args=remote_input_args())
        except subprocess.CalledProcessError as error:
            raise ValueError(media_url_failure(error.stderr, source)) from error
        except Exception as error:
            raise ValueError(media_url_failure(str(error), source)) from error

        total = float(info["duration"])
        start = max(0.0, float(start_seconds))
        end = min(total, float(end_seconds))
        if end <= start:
            raise ValueError("End time must be later than start time")

        has_video = bool(info["has_video"])
        suffix = ".mp4" if has_video else ".wav"
        clip_key = cache_key(
            "url-range-media",
            {"url": source},
            {"start": round(start, 9), "end": round(end, 9), "video": has_video},
        )
        clip_path = cache_path("media", clip_key, suffix, namespace="url_ranges")
        partial = clip_path.with_name(f".{clip_path.stem}.{uuid.uuid4().hex}.part{suffix}")
        if not clip_path.is_file() or clip_path.stat().st_size == 0:
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            command = build_url_clip_command(
                resolve_media_tool("ffmpeg"),
                source,
                start,
                end,
                str(partial),
                has_video=has_video,
            )
            completed = subprocess.run(command, capture_output=True)
            if completed.returncode != 0:
                partial.unlink(missing_ok=True)
                raise ValueError(media_url_failure(completed.stderr, source))
            if not partial.is_file() or partial.stat().st_size == 0:
                partial.unlink(missing_ok=True)
                raise ValueError(media_url_failure("FFmpeg produced no media data.", source))
            partial.replace(clip_path)

        try:
            clip_info = _probe(clip_path)
            clip_duration = min(end - start, float(clip_info["duration"]))
            selected_audio = _extract_audio(clip_path, 0.0, clip_duration)
        except Exception:
            clip_path.unlink(missing_ok=True)
            raise

        selected_video = None
        if has_video:
            selected_video = InputImpl.VideoFromFile(str(clip_path)).as_trimmed(
                0.0,
                end - start,
                strict_duration=False,
            )
            if selected_video is None:
                clip_path.unlink(missing_ok=True)
                raise ValueError("The selected video range could not be created")

        preview_name = _input_preview_name(f"url:{clip_key}", clip_path)
        payload = {
            "display_source": media_url_display_name(source),
            "filename": preview_name,
            "waveform_filename": preview_name,
            "duration": total,
            "clip_duration": clip_duration,
            "has_video": has_video,
            "has_audio": True,
            "start": start,
            "end": end,
            "preview_start": start,
            "waveform_offset": -start,
        }
        return {
            "ui": {"alice_lab_media_range_url": [json.dumps(payload)]},
            "result": (
                selected_audio,
                start,
                end,
                end - start,
                selected_video,
            ),
        }

    @classmethod
    def IS_CHANGED(cls, url: str, start_seconds: float, end_seconds: float):
        # Remote content and signed stream validity can change without the URL
        # widget value changing, so do not treat a URL as a permanent cache key.
        return float("nan")

    @classmethod
    def VALIDATE_INPUTS(cls, url: str, start_seconds: float, end_seconds: float):
        if url is not None:
            try:
                validate_media_url(url)
            except ValueError as error:
                return str(error)
        return validate_static_range(start_seconds, end_seconds)


class AliceLabMediaRangeInput:
    """Select a range from one upstream AUDIO or VIDEO value."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "start_seconds": (
                    "FLOAT",
                    {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.001},
                ),
                "end_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 86400.0,
                        "step": 0.001,
                        "tooltip": "0 selects the full input on the first run.",
                    },
                ),
                "a_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 86400.0,
                        "step": 0.001,
                        "tooltip": "Local A marker inside the input start/end window.",
                    },
                ),
                "b_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 86400.0,
                        "step": 0.001,
                        "tooltip": "Local B marker; 0 selects the complete input window.",
                    },
                ),
            },
            "optional": {
                "audio": ("AUDIO",),
                "video": ("VIDEO",),
            },
        }

    RETURN_TYPES = ("AUDIO", "FLOAT", "FLOAT", "FLOAT", "VIDEO")
    RETURN_NAMES = (
        "audio",
        "start_seconds",
        "end_seconds",
        "duration_seconds",
        "video",
    )
    FUNCTION = "select"
    CATEGORY = "ALICE_Lab/Media"
    DESCRIPTION = (
        "Limit one connected AUDIO or VIDEO to an input window, then preview "
        "and refine it with a local A-B range."
    )
    OUTPUT_NODE = True

    def select(
        self,
        start_seconds: float,
        end_seconds: float,
        a_seconds: float = 0.0,
        b_seconds: float = 0.0,
        audio: dict | None = None,
        video=None,
    ):
        if (audio is None) == (video is None):
            raise ValueError("Connect exactly one AUDIO or VIDEO input to Media Range (Input)")

        temp = Path(folder_paths.get_temp_directory())
        if audio is not None:
            token = uuid.uuid4().hex
            total = audio_duration(audio)
            input_start, input_end = normalize_range(
                start_seconds, end_seconds, total
            )
            input_duration = input_end - input_start
            a, b = normalize_local_range(a_seconds, b_seconds, input_duration)
            selected_audio, selected_start, selected_end, _ = trim_audio(
                audio, input_start + a, input_start + b
            )
            a = selected_start - input_start
            b = selected_end - input_start
            filename = f"alice_lab_media_range_input_{token}.wav"
            _write_audio_wave(temp / filename, audio)
            selected_video = None
            has_video = False
            has_audio = True
            source_offset = input_start
            waveform_filename = filename
            waveform_offset = input_start
        else:
            total = float(video.get_duration())
            input_start, input_end = normalize_range(
                start_seconds, end_seconds, total
            )
            input_duration = input_end - input_start
            a, b = normalize_local_range(a_seconds, b_seconds, input_duration)
            selected_start = input_start + a
            selected_end = input_start + b
            has_video = True
            if isinstance(video, InputImpl.VideoFromComponents):
                (
                    selected_video,
                    selected_audio,
                    filename,
                    preview_source,
                    source_offset,
                    waveform_filename,
                    waveform_offset,
                ) = _trim_component_video(
                    video,
                    input_start,
                    input_end,
                    a,
                    b,
                    total,
                )
                has_audio = selected_audio is not None
            else:
                filename, preview_source, source_offset = _cached_input_video_preview(
                    video, total
                )
                waveform_filename = filename
                waveform_offset = source_offset
                info = _probe(preview_source, require_audio=False)
                has_audio = bool(info["has_audio"])

            if isinstance(video, InputImpl.VideoFromFile):
                selected_video = video.as_trimmed(
                    start_time=selected_start,
                    duration=selected_end - selected_start,
                    strict_duration=False,
                )
                selected_audio = None
                if has_audio:
                    try:
                        selected_audio = _extract_audio(
                            preview_source,
                            source_offset + selected_start,
                            source_offset + selected_end,
                        )
                    except ValueError:
                        selected_audio = None
            elif not isinstance(video, InputImpl.VideoFromComponents):
                selected_video = video.as_trimmed(
                    start_time=selected_start,
                    duration=selected_end - selected_start,
                    strict_duration=False,
                )
                selected_audio = None
                if has_audio:
                    try:
                        selected_audio = _extract_audio(
                            preview_source, selected_start, selected_end
                        )
                    except ValueError:
                        selected_audio = None
            if selected_video is None:
                raise ValueError("The selected video range could not be created")

            source_offset += input_start
            waveform_offset += input_start

        payload = {
            "filename": filename,
            "duration": input_duration,
            "has_video": has_video,
            "has_audio": has_audio,
            "input_start": input_start,
            "input_end": input_end,
            "a": a,
            "b": b,
            # Retain these payload aliases for a browser tab that still has the
            # previous extension loaded while ComfyUI is restarting.
            "start": a,
            "end": b,
            "source_offset": source_offset,
            "waveform_filename": waveform_filename,
            "waveform_offset": waveform_offset,
        }
        return {
            "ui": {"alice_lab_media_range_input": [json.dumps(payload)]},
            "result": (
                selected_audio,
                selected_start,
                selected_end,
                selected_end - selected_start,
                selected_video,
            ),
        }


web = server.web
_preview_locks: dict[str, asyncio.Lock] = {}
_preview_modes: dict[str, bool] = {}


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/config")
async def alice_lab_audio_tools_config(request):
    """Expose the active upload limit for browser-side file preflight."""
    return web.json_response({"max_upload_size_mb": float(args.max_upload_size)})


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/media_info")
async def media_info(request):
    """Serve duration and video-presence metadata to the browser widget."""
    try:
        path = _resolve_request_media(request)
        return web.json_response(await asyncio.to_thread(_probe, path))
    except Exception as error:
        return web.json_response({"error": str(error)}, status=400)


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/waveform")
async def waveform(request):
    """Serve cached waveform peaks without blocking ComfyUI's event loop."""
    try:
        path = _resolve_request_media(request)
        query = request.rel_url.query
        points = max(256, min(int(query.get("points", "2000")), 12000))
        start = max(0.0, float(query.get("start", "0")))
        end_value = query.get("end")
        end = float(end_value) if end_value is not None else None
        range_key = f"{start:.9f}:{end:.9f}" if end is not None else "overview"
        waveform_key = cache_key(
            "waveform-v2",
            local_source_identity(path),
            {"points": points, "range": range_key},
        )
        waveform_path = cache_path("metadata", waveform_key, ".json", namespace="waveforms")
        # Source metadata is part of the key, making cached data stale-proof
        # when a media file is replaced in place.
        cache_hit = waveform_path.is_file()
        if cache_hit:
            peaks = json.loads(waveform_path.read_text(encoding="utf-8"))
        else:
            if end is not None and end > start:
                peaks = await asyncio.to_thread(_waveform_detail, path, points, start, end)
            else:
                peaks = await asyncio.to_thread(_waveform, path, points)
            atomic_write_json(waveform_path, peaks)
        return web.json_response({
            "peaks": peaks,
            "start": start,
            "end": end,
            "cached": cache_hit,
        })
    except Exception as error:
        return web.json_response({"error": str(error)}, status=400)


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/source")
async def source(request):
    """Serve validated input media directly for browser-native audio playback."""
    try:
        path = _resolve_request_media(request)
        return web.FileResponse(path=path)
    except Exception as error:
        return web.Response(text=str(error), status=400)


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/audio_preview")
async def audio_preview(request):
    """Serve a browser-compatible AAC proxy for audio-only source formats.

    Waveform extraction uses FFmpeg and therefore accepts more codecs than a
    browser media element.  Always proxy audio-only previews so WAV variants,
    FLAC, Opus, and platform-specific browser support behave consistently.
    The original file remains untouched and Run still decodes from the source.
    """
    try:
        path = _resolve_request_media(request)
    except Exception as error:
        return web.Response(text=str(error), status=400)
    query = request.rel_url.query
    clip_start = clip_end = None
    if query.get("clip_start") is not None or query.get("clip_end") is not None:
        try:
            clip_start = max(0.0, float(query.get("clip_start")))
            clip_end = float(query.get("clip_end"))
        except (TypeError, ValueError):
            return web.Response(text="Invalid audio preview range", status=400)
        if (
            not math.isfinite(clip_start)
            or not math.isfinite(clip_end)
            or clip_end <= clip_start
        ):
            return web.Response(text="Invalid audio preview range", status=400)
    clip_key = (
        f":{clip_start:.9f}:{clip_end:.9f}"
        if clip_start is not None
        else ":full"
    )
    audio_key = cache_key(
        "audio-preview",
        local_source_identity(path),
        {"clip": clip_key, "codec": "aac-192k"},
    )
    proxy = cache_path("media", audio_key, ".m4a", namespace="audio_previews")
    lock = _preview_locks.setdefault(f"audio-{audio_key}", asyncio.Lock())
    async with lock:
        if not proxy.is_file() or proxy.stat().st_size == 0:
            proxy.parent.mkdir(parents=True, exist_ok=True)
            partial = proxy.with_suffix(".part.m4a")
            partial.unlink(missing_ok=True)
            input_args = []
            duration_args = []
            if clip_start is not None:
                input_args = ["-ss", f"{clip_start:.9f}"]
                duration_args = ["-t", f"{clip_end - clip_start:.9f}"]
            process = await asyncio.create_subprocess_exec(
                resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-nostdin", *input_args, "-i", str(path), *duration_args,
                "-map", "0:a:0", "-vn",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-y", str(partial),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                partial.unlink(missing_ok=True)
                detail = stderr.decode("utf-8", errors="replace").strip()
                return web.Response(text=detail or "Audio preview conversion failed", status=500)
            partial.replace(proxy)
    return web.FileResponse(
        path=proxy,
        headers={"Content-Disposition": f'inline; filename="{proxy.name}"'},
    )


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/audio_compare_analysis")
async def audio_compare_analysis(request):
    """Re-analyse only the visible Audio Compare interval at requested detail."""
    try:
        query = request.rel_url.query
        payload = await asyncio.to_thread(
            analyse_compare_region,
            query.get("id", ""),
            query.get("view", "aligned"),
            float(query.get("start", 0)),
            float(query.get("end", 0)),
            int(query.get("points", 1200)),
        )
        return web.json_response(payload)
    except Exception as error:
        return web.json_response({"error": str(error)}, status=400)


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/audio_compare_audio")
async def audio_compare_audio(request):
    """Serve one selected comparison interval for synchronized browser playback."""
    try:
        query = request.rel_url.query
        content = await asyncio.to_thread(
            compare_audio_wav,
            query.get("id", ""),
            query.get("view", "aligned"),
            query.get("track", "a"),
            float(query.get("start", 0)),
            float(query.get("end", 0)),
        )
        return web.Response(
            body=content,
            content_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as error:
        return web.Response(text=str(error), status=400)


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/preview")
async def preview(request):
    """Serve a browser-seekable MP4 proxy for formats such as MKV and MPEG-TS."""
    try:
        path = _resolve_request_media(request)
    except Exception as error:
        return web.Response(text=str(error), status=400)
    source_identity = local_source_identity(path)
    source_version = json.dumps(source_identity, sort_keys=True, separators=(",", ":"))
    query = request.rel_url.query
    clip_start_value = query.get("clip_start")
    clip_end_value = query.get("clip_end")
    clip_start = clip_end = None
    if clip_start_value is not None or clip_end_value is not None:
        try:
            clip_start = max(0.0, float(clip_start_value))
            clip_end = float(clip_end_value)
        except (TypeError, ValueError):
            return web.Response(text="Invalid preview range", status=400)
        if (
            not math.isfinite(clip_start)
            or not math.isfinite(clip_end)
            or clip_end <= clip_start
        ):
            return web.Response(text="Invalid preview range", status=400)

    if clip_start is not None:
        # Media Range (Input) already supplies its requested A-B interval. Skip
        # the full-file keyframe scan and encode only that small browser proxy.
        transcode_video = True
        preview_mode = f"clip:{clip_start:.9f}:{clip_end:.9f}"
    else:
        if source_version not in _preview_modes:
            if len(_preview_modes) >= 128:
                _preview_modes.pop(next(iter(_preview_modes)))
            _preview_modes[source_version] = await asyncio.to_thread(
                needs_seekable_video_preview, path
            )
        transcode_video = _preview_modes[source_version]
        preview_mode = "seekable" if transcode_video else "remux"
    preview_key = cache_key(
        "adaptive-preview-v3",
        source_identity,
        {"mode": preview_mode, "max_width": 854, "max_height": 480},
    )
    proxy = cache_path("thumbnails", preview_key, ".mp4", namespace="media_range")
    # Multiple nodes can request one preview concurrently. A per-source lock
    # prevents competing ffmpeg processes from writing the same proxy.
    lock = _preview_locks.setdefault(preview_key, asyncio.Lock())
    async with lock:
        if not proxy.is_file() or proxy.stat().st_size == 0:
            proxy.parent.mkdir(parents=True, exist_ok=True)
            # Replace atomically so the browser never observes a partial MP4.
            partial = proxy.with_suffix(".part.mp4")
            partial.unlink(missing_ok=True)
            video_args = (
                [
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                    "-vf",
                    "scale=854:480:force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*1)",
                ]
                if transcode_video
                else ["-c:v", "copy"]
            )
            input_args = []
            duration_args = []
            if clip_start is not None:
                input_args = ["-ss", f"{clip_start:.9f}"]
                duration_args = ["-t", f"{clip_end - clip_start:.9f}"]
            process = await asyncio.create_subprocess_exec(
                resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-nostdin", *input_args, "-i", str(path), *duration_args,
                "-map", "0:v:0", "-map", "0:a:0?", *video_args,
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-y", str(partial),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                partial.unlink(missing_ok=True)
                detail = stderr.decode("utf-8", errors="replace").strip()
                return web.Response(text=detail or "Preview conversion failed", status=500)
            partial.replace(proxy)
    return web.FileResponse(
        path=proxy,
        headers={"Content-Disposition": f'inline; filename="{proxy.name}"'},
    )


NODE_CLASS_MAPPINGS = {
    "AliceLabMediaRange": AliceLabMediaRange,
    "AliceLabMediaRangePath": AliceLabMediaRangePath,
    "AliceLabMediaRangeURL": AliceLabMediaRangeURL,
    "AliceLabMediaRangeInput": AliceLabMediaRangeInput,
    "AliceLabAudioMixer": AliceLabAudioMixer,
    "AliceLabOutputWaveform": AliceLabOutputWaveform,
    "AliceLabCompareAudio": AliceLabCompareAudio,
    "AliceLabOutputFloat": AliceLabOutputFloat,
    "AliceLabReplaceVideoAudio": AliceLabReplaceVideoAudio,
    "AliceLabOutputFFmpeg": AliceLabOutputFFmpeg,
    "AliceLabSpectrogram": AliceLabSpectrogram,
    "AliceLabAudioToIrodoriRefConfig": AliceLabAudioToIrodoriRefConfig,
    "AliceLabTranscriptRangeSelector": AliceLabTranscriptRangeSelector,
    "AliceLabVideoFirstLastFrame": AliceLabVideoFirstLastFrame,
    "AliceLabCacheManager": AliceLabCacheManager,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "AliceLabMediaRange": "Load Media Range (Upload)",
    "AliceLabMediaRangePath": "Load Media Range (Path)",
    "AliceLabMediaRangeURL": "Media Range (URL)",
    "AliceLabMediaRangeInput": "Media Range (Input)",
    "AliceLabAudioMixer": "Audio Mixer",
    "AliceLabOutputWaveform": "Output Waveform",
    "AliceLabCompareAudio": "Compare Audio",
    "AliceLabOutputFloat": "Output Float",
    "AliceLabReplaceVideoAudio": "Replace Video Audio",
    "AliceLabOutputFFmpeg": "Preview Video",
    "AliceLabSpectrogram": "Audio Spectrogram",
    "AliceLabAudioToIrodoriRefConfig": "Audio to Irodori Ref Config",
    "AliceLabTranscriptRangeSelector": "Transcript Range Selector",
    "AliceLabVideoFirstLastFrame": "Video First / Last Frame",
    "AliceLabCacheManager": "ALICE Lab Cache Manager",
}

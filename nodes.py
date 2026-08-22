from __future__ import annotations

import hashlib
import asyncio
import json
import subprocess
import uuid
from array import array
from pathlib import Path

import folder_paths
import server
import torch
from comfy.cli_args import args
from comfy_api.latest import InputImpl, Types

from .audio_compare import (
    AliceLabCompareAudio,
    analyse_compare_region,
    compare_audio_wav,
)
from .audio_output import AliceLabOutputWaveform
from .float_output import AliceLabOutputFloat
from .media_tools import resolve_media_tool
from .media_range_input import normalize_range, trim_audio
from .mixer import AliceLabAudioMixer
from .video_audio_replace import AliceLabReplaceVideoAudio, _write_audio_wave
from .video_output import AliceLabOutputFFmpeg
from .audio_spectrogram import AliceLabSpectrogram


MEDIA_EXTENSIONS = {
    ".aac", ".aiff", ".avi", ".flac", ".m2ts", ".m4a", ".m4v",
    ".mkv", ".mov", ".mp3", ".mp4", ".mpg", ".mpeg", ".ogg",
    ".opus", ".ts", ".wav", ".webm", ".wma",
}


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


def _probe(path: Path, require_audio: bool = True) -> dict[str, object]:
    """Read the small subset of ffprobe metadata needed by the node and UI."""
    completed = subprocess.run(
        [
            resolve_media_tool("ffprobe"), "-v", "error", "-show_entries",
            (
                "format=duration:stream=codec_type,width,height,avg_frame_rate:"
                "stream_disposition=attached_pic"
            ),
            "-of", "json", str(path),
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


def _create_seekable_video_preview(source: Path, output: Path) -> None:
    """Create a browser-only MP4 with regular keyframes for arbitrary A seeks."""
    completed = subprocess.run(
        [
            resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-nostdin", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-pix_fmt", "yuv420p", "-force_key_frames",
            "expr:gte(t,n_forced*1)", "-c:a", "copy", "-movflags", "+faststart",
            "-y", str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        detail = completed.stderr.strip()
        raise RuntimeError(detail or "Media Range (Input) could not create a seekable preview")


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
        return audio, start, end, end - start, video

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
        if start_seconds < 0 or end_seconds <= start_seconds:
            return "End time must be later than start time"
        return True


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
            }
        }

    DESCRIPTION = "Select a range from media at an absolute path without copying it."

    def extract(self, media_path: str, start_seconds: float, end_seconds: float):
        return self._extract_path(_resolve_external(media_path), start_seconds, end_seconds)

    @staticmethod
    def _extract_path(path: Path, start_seconds: float, end_seconds: float):
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
                start, end - start, strict_duration=True
            )
            if video is None:
                raise ValueError("The selected video range could not be created")
        return audio, start, end, end - start, video

    @classmethod
    def IS_CHANGED(cls, media_path: str, start_seconds: float, end_seconds: float):
        path = _resolve_external(media_path)
        stat = path.stat()
        payload = f"{path}:{stat.st_mtime_ns}:{stat.st_size}:{start_seconds}:{end_seconds}"
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def VALIDATE_INPUTS(cls, media_path: str, start_seconds: float, end_seconds: float):
        try:
            _resolve_external(media_path)
        except ValueError as error:
            return str(error)
        if start_seconds < 0 or end_seconds <= start_seconds:
            return "End time must be later than start time"
        return True


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
    DESCRIPTION = "Preview and select an A-B range from one connected AUDIO or VIDEO."
    OUTPUT_NODE = True

    def select(
        self,
        start_seconds: float,
        end_seconds: float,
        audio: dict | None = None,
        video=None,
    ):
        if (audio is None) == (video is None):
            raise ValueError("Connect exactly one AUDIO or VIDEO input to Media Range (Input)")

        token = uuid.uuid4().hex
        temp = Path(folder_paths.get_temp_directory())
        if audio is not None:
            selected_audio, start, end, total = trim_audio(
                audio, start_seconds, end_seconds
            )
            filename = f"alice_lab_media_range_input_{token}.wav"
            _write_audio_wave(temp / filename, audio)
            selected_video = None
            has_video = False
            has_audio = True
        else:
            filename = f"alice_lab_media_range_input_{token}.mp4"
            preview_path = temp / filename
            source_path = temp / f"alice_lab_media_range_input_source_{token}.mp4"
            video.save_to(
                str(source_path),
                format=Types.VideoContainer.MP4,
                codec="auto",
                metadata=None,
            )
            if not source_path.is_file() or source_path.stat().st_size == 0:
                raise RuntimeError("Media Range (Input) could not create its video preview")
            info = _probe(source_path, require_audio=False)
            # The materialized file is also what the browser operates on. Use
            # its actual muxed duration so UI markers and backend trimming share
            # exactly the same timeline for generated and file-backed VIDEO.
            total = float(info["duration"])
            start, end = normalize_range(start_seconds, end_seconds, total)
            has_video = True
            has_audio = bool(info["has_audio"])
            _create_seekable_video_preview(source_path, preview_path)
            selected_audio = None
            if has_audio:
                try:
                    selected_audio = _extract_audio(source_path, start, end)
                except ValueError:
                    # A container can advertise an audio stream that ends before
                    # the selected video interval. Keep the VIDEO range usable.
                    selected_audio = None
            selected_video = InputImpl.VideoFromFile(str(source_path)).as_trimmed(
                start_time=start,
                duration=end - start,
                strict_duration=False,
            )
            if selected_video is None:
                raise ValueError("The selected video range could not be created")

        payload = {
            "filename": filename,
            "duration": total,
            "has_video": has_video,
            "has_audio": has_audio,
            "start": start,
            "end": end,
        }
        return {
            "ui": {"alice_lab_media_range_input": [json.dumps(payload)]},
            "result": (selected_audio, start, end, end - start, selected_video),
        }


web = server.web
_preview_locks: dict[str, asyncio.Lock] = {}


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
        stat = path.stat()
        range_key = f"{start:.9f}:{end:.9f}" if end is not None else "overview"
        cache_key = hashlib.sha256(
            f"waveform-v2:{path}:{stat.st_mtime_ns}:{stat.st_size}:{points}:{range_key}".encode()
        ).hexdigest()[:24]
        cache_path = (
            Path(folder_paths.get_temp_directory())
            / f"alice_lab_audio_waveform_{cache_key}.json"
        )
        # Source metadata is part of the key, making cached data stale-proof
        # when a media file is replaced in place.
        if cache_path.is_file():
            peaks = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            if end is not None and end > start:
                peaks = await asyncio.to_thread(_waveform_detail, path, points, start, end)
            else:
                peaks = await asyncio.to_thread(_waveform, path, points)
            cache_path.write_text(json.dumps(peaks), encoding="utf-8")
        return web.json_response({
            "peaks": peaks,
            "start": start,
            "end": end,
            "cached": cache_path.is_file(),
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
    stat = path.stat()
    cache_key = hashlib.sha256(
        f"audio-preview:{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()
    ).hexdigest()[:20]
    proxy = Path(folder_paths.get_temp_directory()) / f"alice_lab_audio_{cache_key}.m4a"
    lock = _preview_locks.setdefault(f"audio-{cache_key}", asyncio.Lock())
    async with lock:
        if not proxy.is_file() or proxy.stat().st_size == 0:
            partial = proxy.with_suffix(".part.m4a")
            partial.unlink(missing_ok=True)
            process = await asyncio.create_subprocess_exec(
                resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-nostdin", "-i", str(path), "-map", "0:a:0", "-vn",
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
    stat = path.stat()
    cache_key = hashlib.sha256(
        f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()
    ).hexdigest()[:20]
    proxy = Path(folder_paths.get_temp_directory()) / f"alice_lab_audio_tools_{cache_key}.mp4"
    # Multiple nodes can request one preview concurrently. A per-source lock
    # prevents competing ffmpeg processes from writing the same proxy.
    lock = _preview_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        if not proxy.is_file() or proxy.stat().st_size == 0:
            # Replace atomically so the browser never observes a partial MP4.
            partial = proxy.with_suffix(".part.mp4")
            partial.unlink(missing_ok=True)
            process = await asyncio.create_subprocess_exec(
                resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-nostdin", "-i", str(path), "-map", "0:v:0", "-map",
                "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
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
    "AliceLabMediaRangeInput": AliceLabMediaRangeInput,
    "AliceLabAudioMixer": AliceLabAudioMixer,
    "AliceLabOutputWaveform": AliceLabOutputWaveform,
    "AliceLabCompareAudio": AliceLabCompareAudio,
    "AliceLabOutputFloat": AliceLabOutputFloat,
    "AliceLabReplaceVideoAudio": AliceLabReplaceVideoAudio,
    "AliceLabOutputFFmpeg": AliceLabOutputFFmpeg,
    "AliceLabSpectrogram": AliceLabSpectrogram,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "AliceLabMediaRange": "Load Media Range (Upload)",
    "AliceLabMediaRangePath": "Load Media Range (Path)",
    "AliceLabMediaRangeInput": "Media Range (Input)",
    "AliceLabAudioMixer": "Audio Mixer",
    "AliceLabOutputWaveform": "Output Waveform",
    "AliceLabCompareAudio": "Compare Audio",
    "AliceLabOutputFloat": "Output Float",
    "AliceLabReplaceVideoAudio": "Replace Video Audio",
    "AliceLabOutputFFmpeg": "Preview Video",
    "AliceLabSpectrogram": "Audio Spectrogram",
}

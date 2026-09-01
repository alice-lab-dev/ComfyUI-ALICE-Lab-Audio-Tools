from __future__ import annotations

import io
import subprocess
import uuid
import wave
from pathlib import Path

import folder_paths
import torch
from comfy_api.latest import InputImpl
from comfy_execution.graph_utils import ExecutionBlocker

from .media_tools import resolve_media_tool, resolve_video_encoder
from .media_range_input import trim_video_to_logical_duration
from .video_encoding import VIDEO_ENCODER_CHOICES, video_encoder_args


def _write_audio_wave(path: Path, audio: dict) -> None:
    """Write the first ComfyUI audio batch as PCM16 for reliable FFmpeg input."""
    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate", 0))
    if waveform is None or waveform.ndim not in (2, 3) or waveform.shape[-1] == 0:
        raise ValueError("Replacement audio is empty")
    if sample_rate <= 0:
        raise ValueError("Replacement audio has an invalid sample rate")
    if waveform.ndim == 3:
        waveform = waveform[0]
    if waveform.shape[0] > 2:
        waveform = waveform[:2]
    pcm = (
        waveform.detach().to(dtype=torch.float32, device="cpu")
        .clamp(-1.0, 1.0)
        .mul(32767.0)
        .round()
        .to(torch.int16)
        .transpose(0, 1)
        .contiguous()
        .numpy()
        .tobytes()
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(waveform.shape[0])
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)


def _materialize_video_source(video, path: Path) -> tuple[Path, float, float]:
    """Return a filesystem source plus the VIDEO object's active trim window."""
    start, duration = video.get_active_trim_window()
    source = video.get_stream_source()
    if isinstance(source, (str, Path)):
        return Path(source), float(start), float(duration or video.get_duration())
    if isinstance(source, io.BytesIO):
        source.seek(0)
        path.write_bytes(source.read())
        return path, 0.0, float(video.get_duration())
    raise ValueError("Unsupported VIDEO stream source")


def _mux(
    source: Path,
    audio: Path,
    output: Path,
    start: float,
    duration: float,
    copy_video: bool,
    video_encoder: str = "cpu",
) -> subprocess.CompletedProcess:
    command = [resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start > 0:
        command += ["-ss", f"{start:.6f}"]
    command += ["-i", str(source), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
    if copy_video:
        command += ["-c:v", "copy"]
    else:
        command += video_encoder_args(video_encoder, cpu_preset="medium")
    command += [
        "-c:a", "aac", "-b:a", "192k", "-af", "apad",
        "-t", f"{duration:.6f}", "-movflags", "+faststart", "-y", str(output),
    ]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")


class AliceLabReplaceVideoAudio:
    """Replace a VIDEO object's soundtrack while preserving its selected duration."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"audio": ("AUDIO",)},
            "optional": {
                "video": ("VIDEO",),
                "video_encoder": (VIDEO_ENCODER_CHOICES, {"default": "auto"}),
            },
        }

    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("video",)
    FUNCTION = "replace"
    CATEGORY = "ALICE_Lab/Video"
    DESCRIPTION = "Replace video audio, padding or trimming it to the video duration."

    def replace(self, audio, video=None, video_encoder="auto"):
        # Keep graph wiring intact while an upstream Media Range temporarily
        # selects audio-only media. A silent blocker prevents downstream video
        # output nodes from receiving None; selecting video reactivates the path.
        if video is None:
            return (ExecutionBlocker(None),)
        temp = Path(folder_paths.get_temp_directory())
        token = uuid.uuid4().hex
        audio_path = temp / f"alice_lab_audio_tools_replace_{token}.wav"
        source_path = temp / f"alice_lab_audio_tools_replace_{token}_source.mp4"
        output_path = temp / f"alice_lab_audio_tools_replace_{token}.mp4"
        materialized_source = False
        try:
            _write_audio_wave(audio_path, audio)
            source, start, duration = _materialize_video_source(video, source_path)
            materialized_source = source == source_path
            if duration <= 0:
                raise ValueError("Video duration must be greater than zero")
            result = _mux(source, audio_path, output_path, start, duration, copy_video=True)
            if result.returncode != 0:
                output_path.unlink(missing_ok=True)
                ffmpeg = resolve_media_tool("ffmpeg")
                resolved_encoder = resolve_video_encoder(ffmpeg, video_encoder)
                result = _mux(
                    source,
                    audio_path,
                    output_path,
                    start,
                    duration,
                    copy_video=False,
                    video_encoder=resolved_encoder,
                )
            if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
                detail = result.stderr.strip()
                raise RuntimeError(detail or "Video audio replacement failed")
            # Stream-copy muxing can only stop video on a packet/frame boundary,
            # so the physical MP4 may be a fraction of a frame longer than the
            # requested VIDEO range. Preserve the logical duration on the
            # returned VIDEO so downstream nodes do not inherit that mux drift.
            replaced_video = trim_video_to_logical_duration(
                InputImpl.VideoFromFile(str(output_path)), duration
            )
            return (replaced_video,)
        finally:
            audio_path.unlink(missing_ok=True)
            if materialized_source:
                source_path.unlink(missing_ok=True)

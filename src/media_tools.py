from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


# GUI-launched macOS applications may not inherit Homebrew paths from the
# user's shell.  Keep platform fallbacks centralized so every ALICE node
# resolves FFmpeg and ffprobe consistently.
_FALLBACK_BIN_DIRECTORIES = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


def resolve_media_tool(name: str) -> str:
    """Return an executable media-tool path or raise an actionable error."""
    found = shutil.which(name)
    if found:
        return found
    for directory in _FALLBACK_BIN_DIRECTORIES:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError(
        f"{name} was not found. Install FFmpeg and make {name} available "
        "to the ComfyUI process."
    )


_VIDEO_ENCODER_CODECS = {
    "nvenc": "h264_nvenc",
    "videotoolbox": "h264_videotoolbox",
    "cpu": "libx264",
}


@lru_cache(maxsize=12)
def _can_encode_video(ffmpeg: str, encoder: str) -> bool:
    """Test an encoder with one synthetic frame, including device availability."""
    codec = _VIDEO_ENCODER_CODECS[encoder]
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:d=0.04",
            "-frames:v",
            "1",
            "-an",
            "-c:v",
            codec,
            "-pix_fmt",
            "yuv420p",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
    )
    return completed.returncode == 0


def resolve_video_encoder(ffmpeg: str, requested: str = "auto") -> str:
    """Resolve auto or validate one explicitly requested H.264 encoder."""
    choice = str(requested or "auto").strip().lower()
    if choice not in {"auto", *_VIDEO_ENCODER_CODECS}:
        raise ValueError(f"Unsupported video encoder: {requested}")

    if choice == "auto":
        hardware = (
            ["videotoolbox", "nvenc"]
            if platform.system() == "Darwin"
            else ["nvenc", "videotoolbox"]
        )
        for candidate in [*hardware, "cpu"]:
            if _can_encode_video(ffmpeg, candidate):
                return candidate
        raise RuntimeError("FFmpeg has no usable H.264 encoder")

    if not _can_encode_video(ffmpeg, choice):
        codec = _VIDEO_ENCODER_CODECS[choice]
        raise RuntimeError(
            f"The requested {choice} encoder ({codec}) is not usable by this FFmpeg process"
        )
    return choice


def needs_seekable_video_preview(path: Path, max_keyframe_gap: float = 2.5) -> bool:
    """Return whether browser playback needs a low-cost seekable proxy.

    Regular-GOP videos can be remuxed without re-encoding. Sparse keyframes
    make the programmatic B-to-A seek followed immediately by play unreliable
    in some browsers, so only those sources need a dedicated preview encode.
    """
    completed = subprocess.run(
        [
            resolve_media_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-skip_frame", "nokey", "-show_entries",
            "frame=best_effort_timestamp_time:format=duration", "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload = json.loads(completed.stdout)
    duration = float(payload.get("format", {}).get("duration", 0) or 0)
    keyframes = sorted(
        {
            float(frame["best_effort_timestamp_time"])
            for frame in payload.get("frames", [])
            if frame.get("best_effort_timestamp_time") not in (None, "N/A")
        }
    )
    if duration <= 0 or not keyframes:
        return True
    boundaries = [0.0, *keyframes, duration]
    return any(
        later - earlier > max_keyframe_gap
        for earlier, later in zip(boundaries, boundaries[1:])
    )

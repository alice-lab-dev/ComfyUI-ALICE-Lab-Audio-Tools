from __future__ import annotations

import json
import os
import shutil
import subprocess
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

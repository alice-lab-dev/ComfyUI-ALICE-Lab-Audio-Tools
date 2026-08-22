from __future__ import annotations

import os
import shutil
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


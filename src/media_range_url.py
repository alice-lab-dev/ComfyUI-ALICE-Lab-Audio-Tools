from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

from .video_encoding import video_encoder_args


REMOTE_IO_TIMEOUT_MICROSECONDS = 15_000_000


def validate_media_url(value: str) -> str:
    """Return one normalized direct HTTP(S) media URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Media Range (URL) requires a direct media URL")
    url = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("Media Range (URL) received invalid control characters")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Media Range (URL) accepts only http:// or https:// URLs")
    return url


def remote_input_args() -> list[str]:
    """FFmpeg input options that bound stalled remote reads."""
    return ["-rw_timeout", str(REMOTE_IO_TIMEOUT_MICROSECONDS)]


def media_url_display_name(url: str) -> str:
    """Produce a useful UI label without exposing a signed query string."""
    parsed = urlsplit(validate_media_url(url))
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    authority = f"{host}:{port}" if port is not None else host
    path = parsed.path or "/"
    return f"{parsed.scheme}://{authority}{path}"


def build_url_clip_command(
    ffmpeg: str,
    url: str,
    start_seconds: float,
    end_seconds: float,
    output_path: str,
    *,
    has_video: bool,
    video_encoder: str = "cpu",
) -> list[str]:
    """Build an argv-only FFmpeg command for one requested remote interval."""
    source = validate_media_url(url)
    start = float(start_seconds)
    end = float(end_seconds)
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("End time must be later than start time")

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        *remote_input_args(),
        "-ss",
        f"{start:.9f}",
        "-i",
        source,
        "-t",
        f"{end - start:.9f}",
    ]
    if has_video:
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                *video_encoder_args(video_encoder),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        )
    else:
        command.extend(
            [
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
            ]
        )
    command.extend(["-y", output_path])
    return command


def media_url_failure(stderr: str | bytes | None, url: str) -> str:
    """Return an actionable, bounded error without leaking signed URL queries."""
    if isinstance(stderr, bytes):
        detail = stderr.decode("utf-8", errors="replace")
    else:
        detail = str(stderr or "")
    detail = detail.replace(url, media_url_display_name(url))
    detail = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1", detail).strip()
    if len(detail) > 1200:
        detail = detail[-1200:]
    message = (
        "Failed to open media URL. The stream URL may have expired. "
        "Please resolve the URL again and retry."
    )
    return f"{message}\nFFmpeg: {detail}" if detail else message

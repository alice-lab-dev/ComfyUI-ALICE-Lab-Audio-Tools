from __future__ import annotations

import io
import json
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import quote

import folder_paths
import server
from comfy_api.latest import InputImpl, Types

from .media_tools import resolve_media_tool, resolve_video_encoder
from .video_encoding import VIDEO_ENCODER_CHOICES, build_video_preview_command


def _run_preview_ffmpeg(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _write_file_video_preview(video, output: Path, requested_encoder: str) -> str:
    """Write a file-backed VIDEO through stream copy or the selected FFmpeg encoder."""
    source = video.get_stream_source()
    start, active_duration = video.get_active_trim_window()
    source_copy = None
    try:
        if isinstance(source, io.BytesIO):
            source.seek(0)
            source_copy = output.with_name(f".{output.stem}.source")
            source_copy.write_bytes(source.read())
            source_path = source_copy
        elif isinstance(source, (str, Path)):
            source_path = Path(source)
        else:
            raise ValueError("Preview Video received an unsupported stream source")

        ffmpeg = resolve_media_tool("ffmpeg")
        # An untrimmed file can usually be remuxed into MP4 without touching its
        # video frames. Trimmed VIDEO values require a real encode so their
        # logical duration remains exact instead of ending on a packet boundary.
        if float(start) == 0.0 and float(active_duration) == 0.0:
            copied = _run_preview_ffmpeg(
                build_video_preview_command(
                    ffmpeg,
                    str(source_path),
                    str(output),
                    start_seconds=0.0,
                    duration_seconds=0.0,
                    video_encoder=None,
                    copy_audio=True,
                )
            )
            if copied.returncode == 0 and output.is_file() and output.stat().st_size > 0:
                return "copy"
            output.unlink(missing_ok=True)
            copied = _run_preview_ffmpeg(
                build_video_preview_command(
                    ffmpeg,
                    str(source_path),
                    str(output),
                    start_seconds=0.0,
                    duration_seconds=0.0,
                    video_encoder=None,
                )
            )
            if copied.returncode == 0 and output.is_file() and output.stat().st_size > 0:
                return "copy"
            output.unlink(missing_ok=True)

        resolved_encoder = resolve_video_encoder(ffmpeg, requested_encoder)
        encoded = _run_preview_ffmpeg(
            build_video_preview_command(
                ffmpeg,
                str(source_path),
                str(output),
                start_seconds=float(start),
                duration_seconds=float(active_duration),
                video_encoder=resolved_encoder,
            )
        )
        if encoded.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            output.unlink(missing_ok=True)
            detail = encoded.stderr.strip()
            raise RuntimeError(detail or "Preview Video FFmpeg conversion failed")
        return resolved_encoder
    finally:
        if source_copy is not None:
            source_copy.unlink(missing_ok=True)


@server.PromptServer.instance.routes.get("/alice_lab_audio_tools/video_out_download")
async def download_video_out(request):
    """Download only Video Out temp files under a user-selected safe name."""
    filename = Path(request.rel_url.query.get("filename", "")).name
    if not filename.startswith("alice_lab_video_out_") or not filename.endswith(".mp4"):
        return server.web.Response(status=400, text="Invalid ALICE preview file")
    path = Path(folder_paths.get_temp_directory()) / filename
    if not path.is_file():
        return server.web.Response(status=404, text="ALICE Lab Audio Tools preview file was not found")
    requested = AliceLabOutputFFmpeg._safe_filename(
        request.rel_url.query.get("download_name", "ALICE_Lab_video")
    ) + ".mp4"
    ascii_name = requested.encode("ascii", "ignore").decode("ascii") or "ALICE_Lab_video.mp4"
    return server.web.FileResponse(
        path=path,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(requested)}"
            )
        },
    )


class AliceLabOutputFFmpeg:
    """Create a browser-friendly preview and pass the VIDEO through unchanged."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("VIDEO",),
                "filename": ("STRING", {"default": "ALICE_Lab_video"}),
                "video_encoder": (VIDEO_ENCODER_CHOICES, {"default": "auto"}),
            }
        }

    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("video",)
    FUNCTION = "preview"
    CATEGORY = "ALICE_Lab/Video"
    DESCRIPTION = "Preview a VIDEO responsively inside the node and pass it through."
    OUTPUT_NODE = True

    def preview(self, video, filename="ALICE_Lab_video", video_encoder="auto"):
        width, height = video.get_dimensions()
        duration = float(video.get_duration())
        if width <= 0 or height <= 0 or duration <= 0:
            raise ValueError("Output FFmpeg received an invalid video")

        temp_filename = f"alice_lab_video_out_{uuid.uuid4().hex}.mp4"
        output = Path(folder_paths.get_temp_directory()) / temp_filename
        if isinstance(video, InputImpl.VideoFromFile):
            used_encoder = _write_file_video_preview(video, output, video_encoder)
        else:
            # Tensor-backed VIDEO has no encoded stream for FFmpeg to consume.
            # Keep ComfyUI's bounded frame-by-frame writer for this uncommon path.
            video.save_to(
                str(output),
                format=Types.VideoContainer.MP4,
                codec="auto",
                metadata=None,
            )
            used_encoder = "comfy"
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("Output FFmpeg could not create its preview")

        payload = {
            "filename": temp_filename,
            "download_name": f"{self._safe_filename(filename)}.mp4",
            "subfolder": "",
            "type": "temp",
            "width": int(width),
            "height": int(height),
            "duration": duration,
            "video_encoder": used_encoder,
        }
        return {
            "ui": {"alice_lab_video_out": [json.dumps(payload)]},
            "result": (video,),
        }

    @staticmethod
    def _safe_filename(value: str) -> str:
        """Create a portable download name without changing the temp path."""
        name = Path(str(value or "")).name
        if name.lower().endswith(".mp4"):
            name = name[:-4]
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")
        return name[:120] or "ALICE_Lab_video"

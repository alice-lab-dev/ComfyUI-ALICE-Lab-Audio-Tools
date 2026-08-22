from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from urllib.parse import quote

import folder_paths
import server
from comfy_api.latest import Types


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
            }
        }

    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("video",)
    FUNCTION = "preview"
    CATEGORY = "ALICE_Lab/Video"
    DESCRIPTION = "Preview a VIDEO responsively inside the node and pass it through."
    OUTPUT_NODE = True

    def preview(self, video, filename="ALICE_Lab_video"):
        width, height = video.get_dimensions()
        duration = float(video.get_duration())
        if width <= 0 or height <= 0 or duration <= 0:
            raise ValueError("Output FFmpeg received an invalid video")

        temp_filename = f"alice_lab_video_out_{uuid.uuid4().hex}.mp4"
        output = Path(folder_paths.get_temp_directory()) / temp_filename
        video.save_to(
            str(output),
            format=Types.VideoContainer.MP4,
            codec="auto",
            metadata=None,
        )
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

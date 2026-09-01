from __future__ import annotations


VIDEO_ENCODER_CHOICES = ("auto", "nvenc", "videotoolbox", "cpu")


def video_encoder_args(
    encoder: str,
    *,
    fast: bool = False,
    cpu_preset: str = "veryfast",
) -> list[str]:
    """Return H.264 output arguments for one already validated encoder."""
    if encoder == "nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p2" if fast else "p4",
            "-cq", "30" if fast else "18", "-b:v", "0", "-pix_fmt", "yuv420p",
        ]
    if encoder == "videotoolbox":
        return [
            "-c:v", "h264_videotoolbox", "-q:v", "45" if fast else "65",
            "-pix_fmt", "yuv420p",
        ]
    if encoder == "cpu":
        return [
            "-c:v", "libx264", "-preset", "ultrafast" if fast else cpu_preset,
            "-crf", "30" if fast else "18", "-pix_fmt", "yuv420p",
        ]
    raise ValueError(f"Unsupported resolved video encoder: {encoder}")


def build_video_preview_command(
    ffmpeg: str,
    source_path: str,
    output_path: str,
    *,
    start_seconds: float,
    duration_seconds: float,
    video_encoder: str | None,
    copy_audio: bool = False,
) -> list[str]:
    """Build a browser-compatible MP4 command without materializing frames in Python."""
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start_seconds > 0:
        command.extend(["-ss", f"{start_seconds:.9f}"])
    command.extend(["-i", source_path])
    if duration_seconds > 0:
        command.extend(["-t", f"{duration_seconds:.9f}"])
    command.extend(["-map", "0:v:0", "-map", "0:a:0?"])
    if video_encoder is None:
        command.extend(["-c:v", "copy"])
    else:
        command.extend(video_encoder_args(video_encoder, fast=True))
    if copy_audio:
        command.extend(["-c:a", "copy"])
    else:
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    command.extend(
        ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-y", output_path]
    )
    return command

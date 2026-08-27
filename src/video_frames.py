from __future__ import annotations

import torch


def _is_components_backed(video) -> bool:
    """Identify ComfyUI's in-memory VIDEO without importing implementation internals."""
    return any(
        base.__name__ == "VideoFromComponents"
        and base.__module__.startswith("comfy_api.")
        for base in type(video).__mro__
    )


def _validate_frames(frames) -> torch.Tensor:
    if not isinstance(frames, torch.Tensor) or frames.ndim != 4:
        raise ValueError("VIDEO frames must use ComfyUI IMAGE layout [B, H, W, C].")
    if frames.shape[0] == 0:
        raise ValueError("Video contains no frames.")
    return frames


class AliceLabVideoFirstLastFrame:
    """Extract the boundary frames from a ComfyUI VIDEO."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"video": ("VIDEO",)}}

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("first_frame", "last_frame")
    FUNCTION = "extract"
    CATEGORY = "ALICE_Lab/Video"
    DESCRIPTION = "Extract the first and last frames of a VIDEO as IMAGE outputs."

    def extract(self, video):
        if _is_components_backed(video):
            frames = _validate_frames(video.get_components().images)
            # Slices preserve the IMAGE batch dimension and source storage.
            return frames[:1], frames[-1:]

        try:
            duration = float(video.get_duration())
            frame_rate = float(video.get_frame_rate())
        except (AttributeError, TypeError, ValueError, ZeroDivisionError) as error:
            raise ValueError("Input is not a valid ComfyUI VIDEO.") from error
        if duration <= 0 or frame_rate <= 0:
            raise ValueError("Video contains no frames.")

        # File-backed VIDEO components materialize every frame. Decode only a
        # small boundary window at each end through ComfyUI's trim API instead.
        # Two nominal frames (at least 50 ms) tolerate timestamp rounding and
        # variable-rate sources without making memory use scale with duration.
        window = min(duration, max(2.0 / frame_rate, 0.05))
        first_video = video.as_trimmed(
            start_time=0.0, duration=window, strict_duration=False
        )
        last_video = video.as_trimmed(
            start_time=max(0.0, duration - window),
            duration=window,
            strict_duration=False,
        )
        if first_video is None or last_video is None:
            raise ValueError("Video contains no frames.")

        first_frames = _validate_frames(first_video.get_components().images)
        last_frames = _validate_frames(last_video.get_components().images)
        return first_frames[:1], last_frames[-1:]

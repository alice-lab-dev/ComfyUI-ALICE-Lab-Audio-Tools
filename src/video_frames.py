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


def _find_last_frame_offset(video, duration: float, frame_rate: float) -> float:
    """Return the last frame timestamp relative to the active VIDEO range.

    ComfyUI's ``get_duration()`` can reflect the container/audio duration, which
    may extend past the final video frame. Inspect timestamps through the
    streaming source so only transient decoder frames are used; IMAGE creation
    remains delegated to ComfyUI below.
    """
    try:
        import av

        source = video.get_stream_source()
        active_start, _ = video.get_active_trim_window()
    except (ImportError, AttributeError, TypeError, ValueError) as error:
        raise ValueError("Input is not a stream-backed ComfyUI VIDEO.") from error

    active_start = float(active_start)
    requested_end = active_start + duration

    try:
        with av.open(source, mode="r") as container:
            if not container.streams.video:
                raise ValueError("Video contains no frames.")
            stream = container.streams.video[0]
            time_base = float(stream.time_base)
            if time_base <= 0:
                raise ValueError("Video contains no frames.")

            # Prefer the video track endpoint over the container endpoint. An
            # audio track can legitimately continue after the last image.
            stream_duration = getattr(stream, "duration", None)
            if stream_duration is not None:
                stream_start = getattr(stream, "start_time", None) or 0
                stream_end = float((stream_start + stream_duration) * stream.time_base)
                requested_end = min(requested_end, stream_end)

            if requested_end <= active_start:
                raise ValueError("Video contains no frames.")

            end_pts = int(requested_end / time_base)
            available_duration = requested_end - active_start
            # A normal seek lands on the preceding keyframe. Exponential
            # lookback is only needed for sparse timestamps or incomplete
            # stream metadata, and still retains only one integer timestamp.
            lookback = min(available_duration, max(2.0, 48.0 / frame_rate))
            last_pts = None
            while True:
                seek_time = max(active_start, requested_end - lookback)
                container.seek(
                    int(seek_time / time_base),
                    stream=stream,
                    backward=True,
                    any_frame=False,
                )
                for frame in container.decode(stream):
                    if frame.pts is None:
                        continue
                    if frame.pts >= end_pts:
                        break
                    frame_time = float(frame.pts * stream.time_base)
                    if frame_time >= active_start:
                        last_pts = frame.pts

                if last_pts is not None or lookback >= available_duration:
                    break
                lookback = min(available_duration, lookback * 4.0)

            if last_pts is None:
                raise ValueError("Video contains no frames.")
            return max(0.0, float(last_pts * stream.time_base) - active_start)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("Could not locate the final VIDEO frame.") from error


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
        last_frame_offset = _find_last_frame_offset(video, duration, frame_rate)
        last_video = video.as_trimmed(
            start_time=last_frame_offset,
            duration=window,
            strict_duration=False,
        )
        if first_video is None or last_video is None:
            raise ValueError("Video contains no frames.")

        first_frames = _validate_frames(first_video.get_components().images)
        last_frames = _validate_frames(last_video.get_components().images)
        return first_frames[:1], last_frames[-1:]

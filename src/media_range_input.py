from __future__ import annotations

import math

import torch


def validate_static_range(start_seconds, end_seconds) -> bool | str:
    """Validate literal range values while allowing linked values to resolve later."""
    if start_seconds is None or end_seconds is None:
        return True
    if start_seconds < 0 or end_seconds <= start_seconds:
        return "End time must be later than start time"
    return True


def range_ui_payload(source: str, start_seconds: float, end_seconds: float) -> dict:
    """Describe the executed range so linked values can update the browser UI."""
    return {
        "source": str(source),
        "start": float(start_seconds),
        "end": float(end_seconds),
    }


def trim_video_to_logical_duration(video, duration: float):
    """Keep packet-boundary mux drift out of a returned ComfyUI VIDEO range."""
    trimmed = video.as_trimmed(
        start_time=0.0,
        duration=float(duration),
        strict_duration=False,
    )
    if trimmed is None:
        raise ValueError("Replaced video range could not be created")
    return trimmed


def audio_duration(audio: dict) -> float:
    """Return the duration of a non-empty ComfyUI AUDIO value."""
    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate", 0))
    if waveform is None or waveform.ndim not in (2, 3) or waveform.shape[-1] == 0:
        raise ValueError("Media Range (Input) received empty audio")
    if sample_rate <= 0:
        raise ValueError("Media Range (Input) received an invalid sample rate")
    return waveform.shape[-1] / sample_rate


def normalize_range(start_seconds: float, end_seconds: float, duration: float) -> tuple[float, float]:
    """Clamp a range to its source; reset stale ranges after an input change."""
    if duration <= 0:
        raise ValueError("Media Range (Input) received media with no duration")
    requested_start = max(0.0, float(start_seconds))
    requested_end = float(end_seconds)
    if requested_end <= 0:
        return 0.0, duration
    if requested_end <= requested_start:
        raise ValueError("End time must be later than start time")
    # The browser cannot learn that an upstream AUDIO/VIDEO changed until this
    # node runs. If the old A-B range is wholly beyond the new, shorter input,
    # refresh successfully with the new source selected in full.
    if requested_start >= duration:
        return 0.0, duration
    start = min(requested_start, duration)
    end = min(requested_end, duration)
    if end <= start:
        raise ValueError("End time must be later than start time")
    return start, end


def normalize_local_range(
    a_seconds: float,
    b_seconds: float,
    duration: float,
) -> tuple[float, float]:
    """Keep a serialized local A-B selection inside a new input window."""
    if duration <= 0:
        raise ValueError("Media Range (Input) received an empty input range")
    a = max(0.0, float(a_seconds))
    b = float(b_seconds)
    if b <= 0:
        return 0.0, duration
    if a >= duration:
        return 0.0, duration
    b = min(duration, b)
    if b <= a:
        return 0.0, duration
    return a, b


def trim_audio(audio: dict, start_seconds: float, end_seconds: float) -> tuple[dict, float, float, float]:
    """Slice AUDIO without decoding or changing its sample rate and metadata."""
    duration = audio_duration(audio)
    start, end = normalize_range(start_seconds, end_seconds, duration)
    waveform = audio["waveform"]
    sample_rate = int(audio["sample_rate"])
    start_sample = min(waveform.shape[-1], round(start * sample_rate))
    end_sample = min(waveform.shape[-1], round(end * sample_rate))
    if end_sample <= start_sample:
        raise ValueError("The selected audio range contains no samples")
    result = dict(audio)
    result["waveform"] = waveform[..., start_sample:end_sample]
    # Report the sample-accurate interval actually returned.
    actual_start = start_sample / sample_rate
    actual_end = end_sample / sample_rate
    return result, actual_start, actual_end, duration


def slice_video_components(
    components,
    start_seconds: float,
    end_seconds: float,
    duration: float,
) -> dict:
    """Slice an in-memory VIDEO before encoding it.

    The returned frame window covers the requested interval. ``frame_offset``
    records the sub-frame trim needed to retain the exact logical A position
    after the smaller component VIDEO is encoded.
    """
    start, end = normalize_range(start_seconds, end_seconds, duration)
    images = components.images
    if images is None or images.ndim != 4 or images.shape[0] == 0:
        raise ValueError("Media Range (Input) received VIDEO with no frames")
    frame_rate = float(components.frame_rate)
    if frame_rate <= 0:
        raise ValueError("Media Range (Input) received an invalid frame rate")

    frame_count = images.shape[0]
    first_frame = min(frame_count - 1, max(0, math.floor(start * frame_rate)))
    final_frame = min(frame_count, max(first_frame + 1, math.ceil(end * frame_rate)))
    frame_start = first_frame / frame_rate

    audio = getattr(components, "audio", None)
    selected_audio = None
    if audio is not None:
        selected_audio, _, _, _ = trim_audio(audio, frame_start, final_frame / frame_rate)

    alpha = getattr(components, "alpha", None)
    return {
        "images": images[first_frame:final_frame],
        "audio": selected_audio,
        "frame_rate": components.frame_rate,
        "metadata": getattr(components, "metadata", None),
        "alpha": alpha[first_frame:final_frame] if alpha is not None else None,
        "start": start,
        "end": end,
        "frame_offset": start - frame_start,
    }

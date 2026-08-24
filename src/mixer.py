from __future__ import annotations

import json
import math
from typing import Any

import torch
import torch.nn.functional as functional


TRACK_COUNT = 8
TRACK_COLORS = (
    "#67c5e8", "#6bd39a", "#ffb45e", "#c69cff",
    "#ff7474", "#63d7d1", "#e8d267", "#ed8fd1",
)


def _track_defaults(index: int) -> dict[str, Any]:
    return {
        "name": f"Track {index + 1}",
        "color": TRACK_COLORS[index % len(TRACK_COLORS)],
        "gain_db": 0.0,
        "mute": False,
        "solo": False,
        "offset": 0.0,
        "source_start": 0.0,
        "timeline_duration": None,
        "fade_in": 0.0,
        "fade_out": 0.0,
    }


def _sanitize_clip(value: dict[str, Any], fallback_source: int) -> dict[str, Any]:
    timeline_duration = value.get("timeline_duration")
    return {
        "id": str(value.get("id") or f"clip-{fallback_source}"),
        "source_index": max(0, min(TRACK_COUNT - 1, int(value.get("source_index", fallback_source)))),
        "gain_db": max(-100.0, min(24.0, float(value.get("gain_db", 0.0)))),
        "offset": max(-86400.0, min(86400.0, float(value.get("offset", 0.0)))),
        "source_start": max(-86400.0, min(86400.0, float(value.get("source_start", 0.0)))),
        "timeline_duration": (
            None
            if timeline_duration is None
            else max(0.001, min(86400.0, float(timeline_duration)))
        ),
        "fade_in": max(0.0, float(value.get("fade_in", 0.0))),
        "fade_out": max(0.0, float(value.get("fade_out", 0.0))),
    }


def parse_track_settings(value: str) -> list[dict[str, Any]]:
    """Parse serialized UI settings while retaining safe defaults."""
    try:
        supplied = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        supplied = []
    if not isinstance(supplied, list):
        supplied = []
    settings = []
    for index in range(TRACK_COUNT):
        item = _track_defaults(index)
        if index < len(supplied) and isinstance(supplied[index], dict):
            item.update(supplied[index])
        if "clips" in item:
            clips = item["clips"] if isinstance(item["clips"], list) else []
            item["clips"] = [
                _sanitize_clip(clip, index)
                for clip in clips
                if isinstance(clip, dict)
            ]
        item["gain_db"] = max(-100.0, min(24.0, float(item["gain_db"])))
        item["offset"] = max(-86400.0, min(86400.0, float(item["offset"])))
        item["source_start"] = max(-86400.0, min(86400.0, float(item["source_start"])))
        timeline_duration = item.get("timeline_duration")
        if timeline_duration is None:
            item["timeline_duration"] = None
        else:
            item["timeline_duration"] = max(0.001, min(86400.0, float(timeline_duration)))
        item["fade_in"] = max(0.0, float(item["fade_in"]))
        item["fade_out"] = max(0.0, float(item["fade_out"]))
        item["mute"] = bool(item["mute"])
        item["solo"] = bool(item["solo"])
        # A track cannot be muted and soloed at once. Prefer mute when loading
        # settings saved by versions that allowed this ambiguous state.
        if item["mute"] and item["solo"]:
            item["solo"] = False
        settings.append(item)
    return settings


def reset_track_values(settings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reset edits and rebuild one source clip per connected mixer input."""
    for item in settings:
        item["gain_db"] = 0.0
        item["offset"] = 0.0
        item["source_start"] = 0.0
        item["timeline_duration"] = None
        item["fade_in"] = 0.0
        item["fade_out"] = 0.0
        # An explicit clips list is the edit buffer used by Copy/Paste. Remove
        # it entirely so the next render reconstructs the default clip from
        # the audio currently connected to this lane.
        item.pop("clips", None)
    return settings


def _standardize(waveform: torch.Tensor, source_rate: int, target_rate: int) -> torch.Tensor:
    """Convert ComfyUI audio to stereo at the mix sample rate."""
    if waveform.ndim == 2:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 3:
        raise ValueError("Audio waveform must have [batch, channels, samples] shape")
    waveform = waveform.to(dtype=torch.float32)
    channels = waveform.shape[1]
    if channels == 1:
        waveform = waveform.repeat(1, 2, 1)
    elif channels > 2:
        waveform = waveform[:, :2, :]
    if source_rate != target_rate and waveform.shape[-1] > 0:
        length = max(1, round(waveform.shape[-1] * target_rate / source_rate))
        waveform = functional.interpolate(waveform, size=length, mode="linear", align_corners=False)
    return waveform


def _apply_fades(waveform: torch.Tensor, sample_rate: int, fade_in: float, fade_out: float) -> torch.Tensor:
    """Apply independent linear fades, clamped to the available clip length."""
    length = waveform.shape[-1]
    result = waveform.clone()
    fade_in_samples = min(length, round(fade_in * sample_rate))
    # Keep the two ramps from crossing; the frontend applies the same limit
    # while its handles are dragged.
    fade_out_samples = min(length - fade_in_samples, round(fade_out * sample_rate))
    if fade_in_samples > 0:
        ramp = torch.linspace(0.0, 1.0, fade_in_samples, device=result.device, dtype=result.dtype)
        result[..., :fade_in_samples] *= ramp
    if fade_out_samples > 0:
        ramp = torch.linspace(1.0, 0.0, fade_out_samples, device=result.device, dtype=result.dtype)
        result[..., -fade_out_samples:] *= ramp
    return result


def _waveform_peaks(waveform: torch.Tensor, count: int = 600) -> list[float]:
    """Create a compact peak envelope suitable for the frontend timeline."""
    mono = waveform.detach().abs().amax(dim=1).amax(dim=0).cpu()
    if mono.numel() == 0:
        return []
    buckets = min(count, mono.numel())
    padded = math.ceil(mono.numel() / buckets) * buckets
    if padded != mono.numel():
        mono = functional.pad(mono, (0, padded - mono.numel()))
    return mono.reshape(buckets, -1).amax(dim=1).clamp(0, 1).tolist()


def _build_timeline_clip(
    source: torch.Tensor,
    sample_rate: int,
    source_start: float,
    timeline_duration: float | None,
) -> tuple[torch.Tensor, float, float]:
    """Build a non-destructive clip with silence outside the source bounds."""
    source_duration = source.shape[-1] / sample_rate
    duration = source_duration if timeline_duration is None else timeline_duration
    duration = max(1 / sample_rate, float(duration))
    clip_samples = max(1, round(duration * sample_rate))
    clip = source.new_zeros((*source.shape[:-1], clip_samples))

    source_start_sample = round(source_start * sample_rate)
    source_end_sample = source_start_sample + clip_samples
    copy_start = max(0, source_start_sample)
    copy_end = min(source.shape[-1], source_end_sample)
    if copy_end > copy_start:
        destination_start = copy_start - source_start_sample
        destination_end = destination_start + copy_end - copy_start
        clip[..., destination_start:destination_end] = source[..., copy_start:copy_end]
    return clip, clip_samples / sample_rate, source_duration


def mix_audio_tracks(
    tracks: list[dict[str, Any]],
    settings: list[dict[str, Any]],
    master_db: float,
    prevent_clipping: bool,
) -> tuple[dict[str, Any], list[dict[str, Any] | None], dict[str, Any]]:
    """Render the master mix, processed track outputs, and timeline metadata."""
    if not tracks:
        raise ValueError("Connect at least one AUDIO input")
    sample_rate = max(int(track["audio"]["sample_rate"]) for track in tracks)
    sources = {track["index"]: track["audio"] for track in tracks}

    def lane_clips(index: int) -> list[dict[str, Any]]:
        config = settings[index]
        if "clips" in config:
            return [clip for clip in config["clips"] if clip["source_index"] in sources]
        if index not in sources:
            return []
        return [_sanitize_clip({
            "id": f"source-{index}",
            "source_index": index,
            "gain_db": config["gain_db"],
            "offset": config["offset"],
            "source_start": config["source_start"],
            "timeline_duration": config["timeline_duration"],
            "fade_in": config["fade_in"],
            "fade_out": config["fade_out"],
        }, index)]

    clips_by_lane = [lane_clips(index) for index in range(TRACK_COUNT)]
    any_solo = any(settings[index]["solo"] and clips_by_lane[index] for index in range(TRACK_COUNT))
    prepared = []
    total_samples = 1
    standardized_sources = {
        index: _standardize(audio["waveform"], int(audio["sample_rate"]), sample_rate)
        for index, audio in sources.items()
    }
    for lane_index, clips in enumerate(clips_by_lane):
        lane = settings[lane_index]
        enabled = not lane["mute"] and (not any_solo or lane["solo"])
        for clip in clips:
            source_waveform = standardized_sources[clip["source_index"]]
            source_waveform = source_waveform * (10.0 ** (clip["gain_db"] / 20.0))
            source_peaks = _waveform_peaks(source_waveform)
            waveform, display_duration, source_duration = _build_timeline_clip(
                source_waveform,
                sample_rate,
                clip["source_start"],
                clip["timeline_duration"],
            )
            waveform = _apply_fades(waveform, sample_rate, clip["fade_in"], clip["fade_out"])
            display_peaks = _waveform_peaks(waveform)
            offset_samples = round(clip["offset"] * sample_rate)
            timeline_cut_samples = max(0, -offset_samples)
            destination_start = max(0, offset_samples)
            waveform = waveform[..., timeline_cut_samples:]
            total_samples = max(total_samples, destination_start + waveform.shape[-1])
            prepared.append((
                lane_index,
                clip,
                waveform,
                destination_start,
                enabled,
                display_duration,
                display_peaks,
                source_duration,
                source_peaks,
            ))

    batch = max((item[2].shape[0] for item in prepared), default=standardized_sources[tracks[0]["index"]].shape[0])
    device = next(iter(standardized_sources.values())).device
    mixed = torch.zeros((batch, 2, total_samples), dtype=torch.float32, device=device)
    individual: list[dict[str, Any] | None] = [None] * TRACK_COUNT
    lane_outputs: list[torch.Tensor | None] = [None] * TRACK_COUNT
    timeline_clips: list[list[dict[str, Any]]] = [[] for _ in range(TRACK_COUNT)]
    for (
        lane_index,
        clip,
        waveform,
        start,
        enabled,
        display_duration,
        display_peaks,
        source_duration,
        source_peaks,
    ) in prepared:
        waveform = waveform.to(device)
        if waveform.shape[0] == 1 and batch > 1:
            waveform = waveform.expand(batch, -1, -1)
        elif waveform.shape[0] != batch:
            raise ValueError("All audio batches must have the same size or a batch size of one")
        if enabled:
            mixed[..., start:start + waveform.shape[-1]] += waveform
            if lane_outputs[lane_index] is None:
                lane_outputs[lane_index] = torch.zeros_like(mixed)
            lane_outputs[lane_index][..., start:start + waveform.shape[-1]] += waveform
        timeline_clips[lane_index].append({
            "id": clip["id"],
            "source_index": clip["source_index"],
            "duration": display_duration,
            "offset": clip["offset"],
            "gain_db": clip["gain_db"],
            "fade_in": clip["fade_in"],
            "fade_out": clip["fade_out"],
            "source_duration": source_duration,
            "source_start": clip["source_start"],
            "source_end": min(
                source_duration,
                max(0.0, clip["source_start"] + display_duration),
            ),
            "timeline_duration": display_duration,
            "enabled": enabled,
            "peaks": display_peaks,
            "source_peaks": source_peaks,
        })

    timeline = []
    for index in range(TRACK_COUNT):
        config = settings[index]
        enabled = not config["mute"] and (not any_solo or config["solo"])
        if lane_outputs[index] is not None:
            individual[index] = {
                "waveform": lane_outputs[index],
                "sample_rate": sample_rate,
                "alice_lab_audio_waveform_color": config["color"],
                "alice_lab_audio_tools_track_name": config["name"],
            }
        lane_payload = {
            "index": index,
            "enabled": enabled,
            "clips": timeline_clips[index],
        }
        # Retain the original single-clip payload fields for older frontends
        # and workflows while exposing the complete clips list to the new UI.
        if len(timeline_clips[index]) == 1:
            lane_payload.update(timeline_clips[index][0])
        timeline.append(lane_payload)

    mixed *= 10.0 ** (max(-100.0, min(24.0, master_db)) / 20.0)
    peak_before_limit = float(mixed.abs().max().item())
    if prevent_clipping and peak_before_limit > 1.0:
        mixed /= peak_before_limit
    payload = {
        "sample_rate": sample_rate,
        "duration": mixed.shape[-1] / sample_rate,
        "peak": peak_before_limit,
        "clipping_prevented": bool(prevent_clipping and peak_before_limit > 1.0),
        "tracks": timeline,
        "mix_peaks": _waveform_peaks(mixed),
    }
    return {"waveform": mixed, "sample_rate": sample_rate}, individual, payload


class AliceLabAudioMixer:
    """Mix up to eight standard ComfyUI AUDIO inputs on a shared timeline."""

    @classmethod
    def INPUT_TYPES(cls):
        optional = {f"audio_{index}": ("AUDIO",) for index in range(1, TRACK_COUNT + 1)}
        return {
            "required": {
                "master_db": ("FLOAT", {"default": 0.0, "min": -100.0, "max": 24.0, "step": 0.1}),
                "prevent_clipping": ("BOOLEAN", {"default": True}),
                "track_settings": ("STRING", {"default": "[]"}),
                "reset_before_run": ("BOOLEAN", {"default": False}),
            },
            "optional": optional,
        }

    # Keep the master output first so existing workflow links remain valid.
    RETURN_TYPES = ("AUDIO",) * (TRACK_COUNT + 1)
    RETURN_NAMES = ("mixed_audio",) + tuple(f"track_{index}" for index in range(1, TRACK_COUNT + 1))
    FUNCTION = "mix"
    CATEGORY = "ALICE_Lab/Audio"
    DESCRIPTION = "Mix multiple audio tracks with gain, mute, solo, offset, and fades."

    @classmethod
    def IS_CHANGED(cls, reset_before_run: bool = False, **kwargs):
        # The connected AUDIO source can be replaced while the serialized edit
        # settings remain identical. Always rebuild the lightweight Mixer
        # result for a queued run so neither audio nor UI metadata is stale.
        return float("nan")

    def mix(
        self,
        master_db: float,
        prevent_clipping: bool,
        track_settings: str,
        reset_before_run: bool = False,
        **kwargs,
    ):
        settings = parse_track_settings(track_settings)
        if reset_before_run:
            reset_track_values(settings)
        tracks = [
            {"index": index - 1, "audio": kwargs[f"audio_{index}"]}
            for index in range(1, TRACK_COUNT + 1)
            if kwargs.get(f"audio_{index}") is not None
        ]
        audio, individual, payload = mix_audio_tracks(
            tracks,
            settings,
            float(master_db),
            bool(prevent_clipping),
        )
        # ExecutionBlocker silently skips an Audio Out connected to a muted,
        # non-solo, or missing track without disturbing the graph wiring.
        from comfy_execution.graph_utils import ExecutionBlocker

        outputs = tuple(
            track_audio if track_audio is not None else ExecutionBlocker(None)
            for track_audio in individual
        )
        return {
            "ui": {"alice_lab_audio_tools_mixer": [json.dumps(payload)]},
            "result": (audio, *outputs),
        }

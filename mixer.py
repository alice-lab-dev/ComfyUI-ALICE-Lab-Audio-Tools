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
        "fade_in": 0.0,
        "fade_out": 0.0,
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
        item["gain_db"] = max(-100.0, min(24.0, float(item["gain_db"])))
        item["offset"] = max(-86400.0, min(86400.0, float(item["offset"])))
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
    """Reset per-track numeric controls while preserving identity and switches."""
    for item in settings:
        item["gain_db"] = 0.0
        item["offset"] = 0.0
        item["fade_in"] = 0.0
        item["fade_out"] = 0.0
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
    any_solo = any(settings[track["index"]]["solo"] for track in tracks)
    prepared = []
    total_samples = 1
    for track in tracks:
        config = settings[track["index"]]
        waveform = _standardize(track["audio"]["waveform"], int(track["audio"]["sample_rate"]), sample_rate)
        waveform = _apply_fades(waveform, sample_rate, config["fade_in"], config["fade_out"])
        waveform *= 10.0 ** (config["gain_db"] / 20.0)
        display_duration = waveform.shape[-1] / sample_rate
        display_peaks = _waveform_peaks(waveform)
        offset_samples = round(config["offset"] * sample_rate)
        source_start = max(0, -offset_samples)
        destination_start = max(0, offset_samples)
        waveform = waveform[..., source_start:]
        enabled = not config["mute"] and (not any_solo or config["solo"])
        total_samples = max(total_samples, destination_start + waveform.shape[-1])
        prepared.append((track, config, waveform, destination_start, enabled, display_duration, display_peaks))

    batch = max(item[2].shape[0] for item in prepared)
    device = prepared[0][2].device
    mixed = torch.zeros((batch, 2, total_samples), dtype=torch.float32, device=device)
    individual: list[dict[str, Any] | None] = [None] * TRACK_COUNT
    timeline = []
    for track, config, waveform, start, enabled, display_duration, display_peaks in prepared:
        waveform = waveform.to(device)
        if waveform.shape[0] == 1 and batch > 1:
            waveform = waveform.expand(batch, -1, -1)
        elif waveform.shape[0] != batch:
            raise ValueError("All audio batches must have the same size or a batch size of one")
        if enabled:
            mixed[..., start:start + waveform.shape[-1]] += waveform
            track_output = torch.zeros_like(mixed)
            track_output[..., start:start + waveform.shape[-1]] = waveform
            individual[track["index"]] = {
                "waveform": track_output,
                "sample_rate": sample_rate,
                "alice_lab_audio_waveform_color": config["color"],
                "alice_lab_audio_tools_track_name": config["name"],
            }
        timeline.append({
            "index": track["index"],
            "duration": display_duration,
            "offset": config["offset"],
            "enabled": enabled,
            "peaks": display_peaks,
        })

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

from __future__ import annotations

import json
import math
import io
import uuid
import wave
import threading
from collections import OrderedDict

import torch
import torch.nn.functional as functional

from .mixer import _waveform_peaks


# Interactive re-analysis needs access to the original sample data after Run.
# Keep only a few recent comparisons; tensors are shared with node outputs and
# old sessions are released automatically instead of growing without bound.
_COMPARE_SESSIONS: OrderedDict[str, dict[str, object]] = OrderedDict()
_MAX_COMPARE_SESSIONS = 4
_MAX_COMPARE_SESSION_BYTES = 512 * 1024 * 1024
_COMPARE_SESSION_LOCK = threading.Lock()


def _prepare_audio(audio: dict, target_rate: int) -> torch.Tensor:
    """Return the first batch as stereo float audio at ``target_rate``."""
    waveform = audio.get("waveform")
    source_rate = int(audio.get("sample_rate", 0))
    if waveform is None or waveform.ndim not in (2, 3) or waveform.shape[-1] == 0:
        raise ValueError("Compare Audio received empty audio")
    if source_rate <= 0:
        raise ValueError("Compare Audio received an invalid sample rate")
    if waveform.ndim == 3:
        waveform = waveform[0]
    waveform = waveform.detach().to(dtype=torch.float32, device="cpu")
    if waveform.shape[0] == 1:
        waveform = waveform.repeat(2, 1)
    elif waveform.shape[0] > 2:
        waveform = waveform[:2]
    if source_rate != target_rate:
        length = max(1, round(waveform.shape[-1] * target_rate / source_rate))
        waveform = functional.interpolate(
            waveform.unsqueeze(0), size=length, mode="linear", align_corners=False
        )[0]
    return waveform


def _normalised_correlation(a: torch.Tensor, b: torch.Tensor) -> float:
    """Return a polarity-sensitive Pearson-style correlation in [-1, 1]."""
    if a.numel() < 2 or b.numel() < 2:
        return 0.0
    count = min(a.numel(), b.numel())
    a = a[:count] - a[:count].mean()
    b = b[:count] - b[:count].mean()
    denominator = torch.linalg.vector_norm(a) * torch.linalg.vector_norm(b)
    if not torch.isfinite(denominator) or denominator <= 1e-12:
        return 0.0
    return float(torch.clamp(torch.dot(a, b) / denominator, -1.0, 1.0).item())


def _analysis_envelope(waveform: torch.Tensor, sample_rate: int) -> tuple[torch.Tensor, float]:
    """Create a bounded low-rate amplitude envelope for alignment searches."""
    mono = waveform.mean(dim=0).abs()
    duration = mono.numel() / sample_rate
    # Long sources stay inexpensive while short selected ranges retain detail.
    analysis_rate = min(200.0, 12000.0 / max(duration, 0.001))
    points = max(8, min(12000, round(duration * analysis_rate)))
    envelope = functional.adaptive_avg_pool1d(mono.view(1, 1, -1), points).flatten()
    return envelope, points / max(duration, 1e-9)


def _find_delay(
    a: torch.Tensor,
    b: torch.Tensor,
    sample_rate: int,
    max_shift_seconds: float,
) -> tuple[int, float]:
    """Find the delay to apply to B; a positive value moves B to the right."""
    envelope_a, rate_a = _analysis_envelope(a, sample_rate)
    envelope_b, rate_b = _analysis_envelope(b, sample_rate)
    analysis_rate = min(rate_a, rate_b)
    common_points = min(envelope_a.numel(), envelope_b.numel())
    if envelope_a.numel() != common_points:
        envelope_a = functional.interpolate(
            envelope_a.view(1, 1, -1), size=common_points, mode="linear", align_corners=False
        ).flatten()
    if envelope_b.numel() != common_points:
        envelope_b = functional.interpolate(
            envelope_b.view(1, 1, -1), size=common_points, mode="linear", align_corners=False
        ).flatten()
    max_lag = min(round(max_shift_seconds * analysis_rate), max(0, common_points - 8))
    best_lag = 0
    best_score = -2.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            left, right = envelope_a[lag:], envelope_b[: common_points - lag]
        else:
            left, right = envelope_a[: common_points + lag], envelope_b[-lag:]
        score = _normalised_correlation(left, right)
        if score > best_score:
            best_score, best_lag = score, lag
    delay_samples = round(best_lag / max(analysis_rate, 1e-9) * sample_rate)
    return delay_samples, max(0.0, best_score)


def _aligned_overlap(a: torch.Tensor, b: torch.Tensor, delay: int) -> tuple[torch.Tensor, torch.Tensor]:
    """Trim A and delayed B to their shared timeline."""
    if delay >= 0:
        overlap = min(a.shape[-1] - delay, b.shape[-1])
        aligned_a, aligned_b = a[:, delay : delay + overlap], b[:, :overlap]
    else:
        advance = -delay
        overlap = min(a.shape[-1], b.shape[-1] - advance)
        aligned_a, aligned_b = a[:, :overlap], b[:, advance : advance + overlap]
    if overlap <= 0:
        raise ValueError("The selected audio ranges have no overlap after alignment")
    return aligned_a, aligned_b


def _signed_envelope(waveform: torch.Tensor, count: int = 3000) -> list[list[float]]:
    """Reduce a waveform to signed min/max pairs for overlay rendering."""
    mono = waveform.detach().mean(dim=0).cpu()
    count = max(1, min(count, mono.numel()))
    pairs: list[list[float]] = []
    for index in range(count):
        start = index * mono.numel() // count
        end = max(start + 1, (index + 1) * mono.numel() // count)
        bucket = mono[start:end]
        pairs.append([float(bucket.min().item()), float(bucket.max().item())])
    return pairs


def _register_compare_session(**session: object) -> str:
    """Store one comparison for browser-driven detailed range analysis."""
    identifier = uuid.uuid4().hex
    session["storage_bytes"] = sum(
        value.numel() * value.element_size()
        for key, value in session.items()
        if key in {"raw_a", "raw_b"} and isinstance(value, torch.Tensor)
    )
    with _COMPARE_SESSION_LOCK:
        _COMPARE_SESSIONS[identifier] = session
        total_bytes = sum(int(item.get("storage_bytes", 0)) for item in _COMPARE_SESSIONS.values())
        while len(_COMPARE_SESSIONS) > 1 and (
            len(_COMPARE_SESSIONS) > _MAX_COMPARE_SESSIONS
            or total_bytes > _MAX_COMPARE_SESSION_BYTES
        ):
            _, removed = _COMPARE_SESSIONS.popitem(last=False)
            total_bytes -= int(removed.get("storage_bytes", 0))
    return identifier


def get_compare_session(identifier: str) -> dict[str, object]:
    """Return a recent comparison and refresh its LRU position."""
    with _COMPARE_SESSION_LOCK:
        try:
            session = _COMPARE_SESSIONS.pop(identifier)
        except KeyError as error:
            raise ValueError("Audio comparison data expired; run the node again") from error
        _COMPARE_SESSIONS[identifier] = session
    return session


def _session_pair(session: dict[str, object], view: str) -> tuple[torch.Tensor, torch.Tensor]:
    """Select either uncorrected inputs or the delay-compensated overlap."""
    if view == "raw":
        a = session["raw_a"]
        b = session["raw_b"]
        count = min(a.shape[-1], b.shape[-1])
        return a[..., :count], b[..., :count]
    if view != "aligned":
        raise ValueError("Comparison view must be raw or aligned")
    return session["aligned_a"], session["aligned_b"]


def _range_slice(
    a: torch.Tensor,
    b: torch.Tensor,
    sample_rate: int,
    start_seconds: float,
    end_seconds: float,
) -> tuple[torch.Tensor, torch.Tensor, float, float]:
    """Clamp a requested time range and return its exact sample slice."""
    total_samples = min(a.shape[-1], b.shape[-1])
    if total_samples <= 0:
        raise ValueError("The comparison contains no audio samples")
    start_sample = max(
        0,
        min(total_samples - 1, math.floor(float(start_seconds) * sample_rate)),
    )
    end_sample = max(
        start_sample + 1,
        min(total_samples, math.ceil(float(end_seconds) * sample_rate)),
    )
    # Return sample-derived times rather than the original floating-point
    # request. At extreme zoom this guarantees that metadata and waveforms
    # describe the same non-empty interval.
    start = start_sample / sample_rate
    end = end_sample / sample_rate
    return a[..., start_sample:end_sample], b[..., start_sample:end_sample], start, end


def analyse_compare_region(
    identifier: str,
    view: str,
    start_seconds: float,
    end_seconds: float,
    points: int,
) -> dict[str, object]:
    """Recompute detailed waveforms and comparison metrics for one viewport."""
    session = get_compare_session(identifier)
    sample_rate = int(session["sample_rate"])
    a, b = _session_pair(session, view)
    a, b, start, end = _range_slice(a, b, sample_rate, start_seconds, end_seconds)
    points = max(256, min(int(points), 12000))
    difference = a - b
    waveform_correlation = _normalised_correlation(a.mean(dim=0), b.mean(dim=0))
    waveform_similarity = abs(waveform_correlation)
    similarity = waveform_similarity
    overlay_a = _signed_envelope(a, points)
    overlay_b = _signed_envelope(b, points)
    difference_envelope = _signed_envelope(difference, points)
    actual_points = len(overlay_a)
    return {
        "start": start,
        "end": end,
        "duration": min(a.shape[-1], b.shape[-1]) / sample_rate,
        "points": actual_points,
        "overlay_a": overlay_a,
        "overlay_b": overlay_b,
        "difference": difference_envelope,
        "waveform_correlation": waveform_correlation,
        "waveform_similarity": waveform_similarity,
        "similarity": similarity,
    }


def compare_audio_wav(
    identifier: str,
    view: str,
    track: str,
    start_seconds: float,
    end_seconds: float,
) -> bytes:
    """Encode a selected comparison range as browser-compatible PCM WAV."""
    session = get_compare_session(identifier)
    sample_rate = int(session["sample_rate"])
    a, b = _session_pair(session, view)
    a, b, _, _ = _range_slice(a, b, sample_rate, start_seconds, end_seconds)
    selected = {"a": a, "b": b, "difference": a - b}.get(track)
    if selected is None:
        raise ValueError("Audio track must be a, b, or difference")
    # Interactive playback is intentionally range-oriented. This cap prevents
    # an accidental full-day selection from generating an enormous response.
    selected = selected[..., : sample_rate * 600]
    pcm = (
        selected.transpose(0, 1)
        .contiguous()
        .clamp(-1, 1)
        .mul(32767)
        .to(torch.int16)
        .numpy()
        .tobytes()
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(selected.shape[0])
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


class AliceLabCompareAudio:
    """Align two audio selections and expose visual and numerical differences."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio_1": ("AUDIO",),
                "audio_2": ("AUDIO",),
                "auto_align": ("BOOLEAN", {"default": True}),
                "max_shift_seconds": (
                    "FLOAT",
                    {"default": 2.0, "min": 0.0, "max": 30.0, "step": 0.01},
                ),
            }
        }

    # Keep the first five slots in their established order so existing
    # workflow links remain attached when the combined output is added.
    RETURN_TYPES = ("AUDIO", "AUDIO", "AUDIO", "FLOAT", "FLOAT", "AUDIO")
    RETURN_NAMES = (
        "Audio 1 only",
        "Audio 2 only",
        "1−2 difference",
        "similarity",
        "audio_2_delay_seconds",
        "1+2 overlay",
    )
    OUTPUT_TOOLTIPS = (
        "Audio 1 after optional time alignment.",
        "Audio 2 after optional time alignment.",
        "Sample-by-sample Audio 1 minus Audio 2.",
        "Overall similarity as a scalar from 0.0 to 1.0.",
        "Signed number of seconds applied to align Audio 2; negative advances Audio 2.",
        "Aligned Audio 1 and Audio 2 mixed together at equal gain.",
    )
    FUNCTION = "compare"
    CATEGORY = "ALICE_Lab/Audio"
    DESCRIPTION = "Align and compare two selected audio ranges using alignment and waveform metrics."
    OUTPUT_NODE = True

    def compare(self, audio_1, audio_2, auto_align=True, max_shift_seconds=2.0):
        target_rate = max(int(audio_1.get("sample_rate", 0)), int(audio_2.get("sample_rate", 0)))
        if target_rate <= 0:
            raise ValueError("Compare Audio received an invalid sample rate")
        a = _prepare_audio(audio_1, target_rate)
        b = _prepare_audio(audio_2, target_rate)
        delay, alignment_score = (0, 0.0)
        if auto_align and max_shift_seconds > 0:
            delay, alignment_score = _find_delay(a, b, target_rate, float(max_shift_seconds))
        aligned_a, aligned_b = _aligned_overlap(a, b, delay)
        mono_a, mono_b = aligned_a.mean(dim=0), aligned_b.mean(dim=0)
        waveform_correlation = _normalised_correlation(mono_a, mono_b)
        # Polarity inversion is meaningful diagnostically, but the sounds can
        # still be the same source, so similarity uses its absolute magnitude.
        waveform_similarity = abs(waveform_correlation)
        if auto_align:
            similarity = 0.65 * alignment_score + 0.35 * waveform_similarity
        else:
            # With alignment disabled, report only the waveform comparison;
            # do not present that value as an alignment score.
            alignment_score = 0.0
            similarity = waveform_similarity
        similarity = max(0.0, min(1.0, similarity))
        difference = aligned_a - aligned_b
        overlay = (aligned_a + aligned_b) * 0.5
        analysis_id = _register_compare_session(
            raw_a=a,
            raw_b=b,
            aligned_a=aligned_a,
            aligned_b=aligned_b,
            sample_rate=target_rate,
        )
        payload = {
            "analysis_id": analysis_id,
            "duration": aligned_a.shape[-1] / target_rate,
            "raw_duration": min(a.shape[-1], b.shape[-1]) / target_rate,
            "sample_rate": target_rate,
            "delay_seconds": delay / target_rate,
            "auto_align": bool(auto_align),
            "alignment_score": alignment_score,
            "waveform_correlation": waveform_correlation,
            "waveform_similarity": waveform_similarity,
            "similarity": similarity,
            "overlay_a": _signed_envelope(aligned_a),
            "overlay_b": _signed_envelope(aligned_b),
            "difference": _waveform_peaks(difference.unsqueeze(0), count=3000),
        }
        result_a = {"waveform": aligned_a.unsqueeze(0), "sample_rate": target_rate}
        result_b = {"waveform": aligned_b.unsqueeze(0), "sample_rate": target_rate}
        result_difference = {
            "waveform": difference.unsqueeze(0),
            "sample_rate": target_rate,
            "alice_lab_audio_tools_track_name": "Audio 1 − Audio 2 difference",
            "alice_lab_audio_waveform_color": "#ffd166",
        }
        result_overlay = {
            "waveform": overlay.unsqueeze(0),
            "sample_rate": target_rate,
            "alice_lab_audio_tools_track_name": "1+2 overlay",
            "alice_lab_audio_waveform_color": "#8bd7ef",
        }
        return {
            "ui": {"alice_lab_audio_compare": [json.dumps(payload)]},
            "result": (
                result_a,
                result_b,
                result_difference,
                similarity,
                delay / target_rate,
                result_overlay,
            ),
        }

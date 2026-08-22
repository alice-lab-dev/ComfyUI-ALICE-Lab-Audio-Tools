from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import torch


PACKAGE = "alice_lab_audio_tools_compare_test"
ROOT = Path(__file__).parents[2]
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_load("mixer")
compare_module = _load("audio_compare")


def _audio(waveform: torch.Tensor, sample_rate: int = 1000) -> dict:
    return {"waveform": waveform.view(1, 1, -1), "sample_rate": sample_rate}


def test_compare_uses_numbered_audio_socket_names() -> None:
    inputs = compare_module.AliceLabCompareAudio.INPUT_TYPES()["required"]

    assert "audio_1" in inputs
    assert "audio_2" in inputs
    assert "audio_a" not in inputs
    assert "spectrum_min_db" not in inputs
    assert "spectrum_max_db" not in inputs
    assert compare_module.AliceLabCompareAudio.RETURN_NAMES[:3] == (
        "Audio 1 only",
        "Audio 2 only",
        "1−2 difference",
    )
    assert compare_module.AliceLabCompareAudio.RETURN_NAMES[4] == "audio_2_delay_seconds"
    assert compare_module.AliceLabCompareAudio.RETURN_NAMES[5] == "1+2 overlay"


def test_compare_aligns_a_delayed_second_signal() -> None:
    generator = torch.Generator().manual_seed(7)
    source = torch.randn(4000, generator=generator) * 0.2
    delayed = torch.cat((torch.zeros(150), source[:-150]))

    result = compare_module.AliceLabCompareAudio().compare(
        _audio(source), _audio(delayed), auto_align=True, max_shift_seconds=0.5
    )

    aligned_a, aligned_b, difference, similarity, delay, overlay = result["result"]
    # B already starts 150 ms late, so aligning it requires a -150 ms shift.
    assert abs(delay + 0.15) < 0.015
    assert similarity > 0.95
    assert torch.max(torch.abs(difference["waveform"])) < 1e-5
    assert aligned_a["waveform"].shape == aligned_b["waveform"].shape
    assert torch.allclose(
        overlay["waveform"],
        (aligned_a["waveform"] + aligned_b["waveform"]) * 0.5,
    )
    assert overlay["alice_lab_audio_tools_track_name"] == "1+2 overlay"
    payload = json.loads(result["ui"]["alice_lab_audio_compare"][0])
    assert similarity == payload["similarity"]
    assert delay == payload["delay_seconds"]
    assert "spectral_similarity" not in payload


def test_compare_reports_low_waveform_similarity_for_unrelated_audio() -> None:
    generator = torch.Generator().manual_seed(11)
    a = torch.randn(3000, generator=generator)
    b = torch.randn(3000, generator=generator)

    result = compare_module.AliceLabCompareAudio().compare(
        _audio(a), _audio(b), auto_align=False, max_shift_seconds=0
    )

    assert result["result"][3] < 0.65
    assert "alice_lab_audio_compare" in result["ui"]


def test_compare_does_not_shift_audio_when_auto_align_is_disabled() -> None:
    generator = torch.Generator().manual_seed(7)
    source = torch.randn(4000, generator=generator) * 0.2
    delayed = torch.cat((torch.zeros(150), source[:-150]))

    result = compare_module.AliceLabCompareAudio().compare(
        _audio(source),
        _audio(delayed),
        auto_align=False,
        max_shift_seconds=0.5,
    )

    difference, similarity, delay = result["result"][2:5]
    payload = json.loads(result["ui"]["alice_lab_audio_compare"][0])
    assert delay == 0.0
    assert torch.max(torch.abs(difference["waveform"])) > 0.1
    assert payload["auto_align"] is False
    assert payload["alignment_score"] == 0.0
    assert similarity == payload["waveform_similarity"]


def test_interactive_region_reanalysis_returns_requested_waveform_detail() -> None:
    time = torch.arange(0, 3, 1 / 1000)
    signal = 0.4 * torch.sin(2 * torch.pi * 7 * time)
    result = compare_module.AliceLabCompareAudio().compare(
        _audio(signal), _audio(signal * 0.8), auto_align=False, max_shift_seconds=0
    )
    payload = json.loads(result["ui"]["alice_lab_audio_compare"][0])

    detail = compare_module.analyse_compare_region(
        payload["analysis_id"], "aligned", 0.5, 1.0, 700
    )

    # A 500-sample interval cannot contain more than 500 real detail points.
    assert len(detail["overlay_a"]) == 500
    assert len(detail["overlay_b"]) == 500
    assert len(detail["difference"]) == 500
    assert "spectrum" not in detail
    assert "spectral_similarity" not in detail
    assert detail["start"] == 0.5
    assert detail["end"] == 1.0


def test_interactive_playback_encodes_selected_range_as_wav() -> None:
    signal = torch.linspace(-0.5, 0.5, 2000)
    result = compare_module.AliceLabCompareAudio().compare(
        _audio(signal), _audio(signal), auto_align=False, max_shift_seconds=0
    )
    payload = json.loads(result["ui"]["alice_lab_audio_compare"][0])

    content = compare_module.compare_audio_wav(
        payload["analysis_id"], "raw", "a", 0.25, 0.75
    )

    assert content[:4] == b"RIFF"
    assert content[8:12] == b"WAVE"


def test_extreme_end_zoom_still_returns_one_real_sample() -> None:
    signal = torch.linspace(-0.5, 0.5, 1000)
    result = compare_module.AliceLabCompareAudio().compare(
        _audio(signal), _audio(signal), auto_align=False, max_shift_seconds=0
    )
    payload = json.loads(result["ui"]["alice_lab_audio_compare"][0])

    detail = compare_module.analyse_compare_region(
        payload["analysis_id"], "aligned", 1.0, 1.0, 1200
    )

    assert detail["end"] > detail["start"]
    assert len(detail["overlay_a"]) == 1

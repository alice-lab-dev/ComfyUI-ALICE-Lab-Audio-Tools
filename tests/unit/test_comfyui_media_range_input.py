from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch


_MODULE_SPEC = importlib.util.spec_from_file_location(
    "alice_test_media_range_input",
    Path(__file__).parents[2] / "src" / "media_range_input.py",
)
assert _MODULE_SPEC and _MODULE_SPEC.loader
_MODULE = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(_MODULE)


def test_audio_input_range_is_sample_accurate_and_preserves_metadata() -> None:
    audio = {
        "waveform": torch.arange(20, dtype=torch.float32).reshape(1, 2, 10),
        "sample_rate": 10,
        "custom": "kept",
    }

    selected, start, end, total = _MODULE.trim_audio(audio, 0.2, 0.7)

    assert (start, end, total) == (0.2, 0.7, 1.0)
    assert selected["custom"] == "kept"
    assert selected["waveform"].shape == (1, 2, 5)
    assert torch.equal(selected["waveform"], audio["waveform"][..., 2:7])


def test_zero_end_selects_the_complete_input_on_first_run() -> None:
    audio = {
        "waveform": torch.ones((1, 1, 8)),
        "sample_rate": 4,
    }

    selected, start, end, total = _MODULE.trim_audio(audio, 0.0, 0.0)

    assert (start, end, total) == (0.0, 2.0, 2.0)
    assert torch.equal(selected["waveform"], audio["waveform"])


def test_media_input_rejects_an_empty_or_inverted_range() -> None:
    with pytest.raises(ValueError, match="later than start"):
        _MODULE.normalize_range(1.0, 1.0, 2.0)


def test_range_resets_to_full_when_a_new_input_is_shorter() -> None:
    assert _MODULE.normalize_range(7.0, 8.5, 1.5) == (0.0, 1.5)


def test_range_clamps_only_the_end_when_it_still_overlaps_input() -> None:
    assert _MODULE.normalize_range(1.0, 3.0, 2.0) == (1.0, 2.0)


def test_local_ab_is_preserved_inside_a_changed_input_window() -> None:
    assert _MODULE.normalize_local_range(0.2, 1.4, 1.72) == (0.2, 1.4)


def test_local_ab_end_is_clamped_to_a_shorter_input_window() -> None:
    assert _MODULE.normalize_local_range(0.2, 1.4, 1.0) == (0.2, 1.0)


def test_local_ab_resets_when_start_is_outside_the_input_window() -> None:
    assert _MODULE.normalize_local_range(1.2, 1.4, 1.0) == (0.0, 1.0)


def test_zero_local_b_selects_the_complete_input_window() -> None:
    assert _MODULE.normalize_local_range(0.4, 0.0, 1.72) == (0.0, 1.72)


@pytest.mark.parametrize(
    ("start", "end"),
    [(None, 10.0), (0.0, None), (None, None)],
)
def test_static_validation_defers_linked_range_values(start, end) -> None:
    assert _MODULE.validate_static_range(start, end) is True


def test_static_validation_still_rejects_inverted_literal_range() -> None:
    assert (
        _MODULE.validate_static_range(2.0, 1.0)
        == "End time must be later than start time"
    )


def test_range_ui_payload_preserves_executed_values() -> None:
    assert _MODULE.range_ui_payload("sample.mp4", 1.25, 4.5) == {
        "source": "sample.mp4",
        "start": 1.25,
        "end": 4.5,
    }


def test_video_mux_drift_is_hidden_behind_the_requested_logical_duration() -> None:
    calls = []
    trimmed = object()
    video = SimpleNamespace(
        as_trimmed=lambda **kwargs: calls.append(kwargs) or trimmed
    )

    result = _MODULE.trim_video_to_logical_duration(video, 24.903)

    assert result is trimmed
    assert calls == [
        {
            "start_time": 0.0,
            "duration": 24.903,
            "strict_duration": False,
        }
    ]


def test_component_video_is_sliced_before_encoding() -> None:
    images = torch.arange(10 * 2 * 2 * 3, dtype=torch.float32).reshape(10, 2, 2, 3)
    audio = {
        "waveform": torch.arange(40, dtype=torch.float32).reshape(1, 2, 20),
        "sample_rate": 20,
    }
    components = SimpleNamespace(
        images=images,
        audio=audio,
        frame_rate=10,
        metadata={"kept": True},
        alpha=None,
    )

    sliced = _MODULE.slice_video_components(components, 0.2, 0.7, 1.0)

    assert sliced["start"] == 0.2
    assert sliced["end"] == 0.7
    assert sliced["frame_offset"] == 0.0
    assert torch.equal(sliced["images"], images[2:7])
    assert torch.equal(sliced["audio"]["waveform"], audio["waveform"][..., 4:14])
    assert sliced["metadata"] == {"kept": True}


def test_component_slice_covers_subframe_boundaries() -> None:
    images = torch.zeros((10, 2, 2, 3))
    components = SimpleNamespace(
        images=images,
        audio=None,
        frame_rate=10,
        metadata=None,
        alpha=None,
    )

    sliced = _MODULE.slice_video_components(components, 0.25, 0.65, 1.0)

    assert sliced["images"].shape[0] == 5
    assert sliced["frame_offset"] == pytest.approx(0.05)

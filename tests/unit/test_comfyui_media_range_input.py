from __future__ import annotations

import importlib.util
from pathlib import Path

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

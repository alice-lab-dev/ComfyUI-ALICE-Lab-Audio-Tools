from __future__ import annotations
import json
import torch
import importlib.util
import sys
import types
from pathlib import Path

PACKAGE = "alice_lab_audio_tools_spectrogram_test"
ROOT = Path(__file__).parents[2] / "src"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)

def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

spectrogram_module = _load("audio_spectrogram")

def _audio(waveform: torch.Tensor, sample_rate: int = 44100) -> dict:
    return {"waveform": waveform, "sample_rate": sample_rate}

def test_spectrogram_generates_image_and_ui_payload() -> None:
    time = torch.arange(0, 1, 1 / 44100)
    waveform = torch.sin(2 * torch.pi * 440 * time).unsqueeze(0)
    
    node = spectrogram_module.AliceLabSpectrogram()
    result = node.generate_spectrogram(
        audio=_audio(waveform),
        spectrum_min_db=-100.0,
        spectrum_max_db=0.0,
        start_seconds=0.0,
        end_seconds=1.0,
    )
    
    # Check outputs
    assert len(result) == 2
    assert "ui" in result
    assert "alice_lab_audio_spectrogram" in result["ui"]
    
    # Check image tensor
    images = result["result"][0] # output tensor
    assert isinstance(images, torch.Tensor)
    assert images.shape == (1, 520, 900, 3)
    assert torch.all((images >= 0.0) & (images <= 1.0))
    
    # Check UI payload
    payload = json.loads(result["ui"]["alice_lab_audio_spectrogram"][0])
    assert payload["duration"] == 1.0
    assert payload["total_duration"] == 1.0
    assert payload["start_seconds"] == 0.0
    assert payload["end_seconds"] == 1.0
    assert payload["sample_rate"] == 44100
    assert "spectrum" in payload
    assert "matrix" in payload["spectrum"]
    
def test_spectrogram_respects_time_range() -> None:
    time = torch.arange(0, 2, 1 / 44100)
    waveform = torch.sin(2 * torch.pi * 440 * time).unsqueeze(0)
    
    node = spectrogram_module.AliceLabSpectrogram()
    
    # Crop to 0.5s to 1.5s
    result = node.generate_spectrogram(
        audio=_audio(waveform),
        spectrum_min_db=-100.0,
        spectrum_max_db=0.0,
        start_seconds=0.5,
        end_seconds=1.5,
    )
    
    payload = json.loads(result["ui"]["alice_lab_audio_spectrogram"][0])
    assert payload["duration"] == 1.0  # length of selected range
    assert payload["start_seconds"] == 0.5
    assert payload["end_seconds"] == 1.5
    assert payload["total_duration"] == 2.0


def test_spectrogram_zero_end_reports_the_complete_audio_range() -> None:
    sample_rate = 1000
    waveform = torch.ones((1, 2500))

    result = spectrogram_module.AliceLabSpectrogram().generate_spectrogram(
        audio=_audio(waveform, sample_rate),
        start_seconds=0.0,
        end_seconds=0.0,
    )

    payload = json.loads(result["ui"]["alice_lab_audio_spectrogram"][0])
    assert payload["total_duration"] == 2.5
    assert payload["start_seconds"] == 0.0
    assert payload["end_seconds"] == 2.5
    
def test_spectrogram_handles_invalid_db_range() -> None:
    time = torch.arange(0, 1, 1 / 44100)
    waveform = torch.zeros(1, 44100)
    
    node = spectrogram_module.AliceLabSpectrogram()
    
    try:
        node.generate_spectrogram(
            audio=_audio(waveform),
            spectrum_min_db=0.0,
            spectrum_max_db=-100.0,
            start_seconds=0.0,
            end_seconds=1.0,
        )
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "must be less than" in str(e)


def test_spectrogram_peak_dbfs() -> None:
    """A bin-centered amplitude-1 sine should peak near 0 dBFS."""
    sample_rate = 44100
    n_fft = 2048
    frequency = 50 * sample_rate / n_fft
    time = torch.arange(0, 1, 1 / sample_rate)
    waveform = torch.cos(2 * torch.pi * frequency * time).unsqueeze(0)

    result = spectrogram_module._spectrogram(
        waveform, columns=100, bins=1025, min_db=-100.0, max_db=10.0
    )
    middle_column = len(result["matrix"][0]) // 2
    peak_dbfs = max(row[middle_column] for row in result["matrix"])

    assert -2.0 <= peak_dbfs <= 0.5

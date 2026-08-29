from __future__ import annotations

import importlib.util
import sys
import types
import wave
from pathlib import Path

import pytest
import torch


def _load_module(temp_directory: Path, monkeypatch):
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_temp_directory = lambda: str(temp_directory)
    folder_paths.get_user_directory = lambda: str(temp_directory / "user")
    folder_paths.get_system_user_directory = lambda name: str(
        temp_directory / "user" / f"__{name}"
    )
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)
    monkeypatch.delitem(sys.modules, "src.cache_store", raising=False)

    spec = importlib.util.spec_from_file_location(
        "alice_test_irodori_ref_config",
        Path(__file__).parents[2] / "src" / "irodori_ref_config.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_media_range_module():
    spec = importlib.util.spec_from_file_location(
        "alice_test_irodori_media_range_input",
        Path(__file__).parents[2] / "src" / "media_range_input.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_node_uses_irodori_custom_type_without_importing_irodori(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)
    node = module.AliceLabAudioToIrodoriRefConfig

    assert node.INPUT_TYPES()["required"]["audio"] == ("AUDIO",)
    assert node.RETURN_TYPES == ("IRODORI_REF_CONFIG",)
    assert node.RETURN_NAMES == ("irodori_ref_config",)


@pytest.mark.parametrize(
    "waveform",
    [
        torch.linspace(-1.0, 1.0, 16).reshape(1, 1, 16),
        torch.stack(
            [torch.linspace(-1.0, 1.0, 16), torch.linspace(1.0, -1.0, 16)]
        ).unsqueeze(0),
        torch.linspace(-1.0, 1.0, 16, dtype=torch.float64).reshape(1, 16),
    ],
)
def test_writes_mono_pcm16_with_original_rate_and_length(
    waveform, tmp_path, monkeypatch
) -> None:
    module = _load_module(tmp_path, monkeypatch)
    config = module.audio_to_irodori_ref_config(
        {"waveform": waveform, "sample_rate": 8000}
    )

    with wave.open(config["ref_wav"], "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getframerate() == 8000
        assert source.getnframes() == 16
        assert source.getnframes() / source.getframerate() == pytest.approx(0.002)


def test_config_matches_irodori_reference_audio_normalization_options(
    tmp_path, monkeypatch
) -> None:
    module = _load_module(tmp_path, monkeypatch)
    audio = {"waveform": torch.ones((1, 2, 100)), "sample_rate": 1000}

    disabled = module.audio_to_irodori_ref_config(audio, False, 30.0)
    enabled = module.audio_to_irodori_ref_config(audio, True, 12.5)

    assert disabled == {
        "ref_wav": disabled["ref_wav"],
        "ref_latent": None,
        "no_ref": False,
        "ref_normalize_db": None,
        "ref_ensure_max": False,
        "max_ref_seconds": 30.0,
    }
    assert enabled == {
        "ref_wav": disabled["ref_wav"],
        "ref_latent": None,
        "no_ref": False,
        "ref_normalize_db": -16.0,
        "ref_ensure_max": True,
        "max_ref_seconds": 12.5,
    }


def test_same_audio_reuses_one_content_addressed_wave(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)
    audio = {"waveform": torch.rand((1, 2, 320)), "sample_rate": 16000}

    first = module.audio_to_irodori_ref_config(audio)
    second = module.audio_to_irodori_ref_config(audio, True, 10.0)

    assert first["ref_wav"] == second["ref_wav"]
    cache = (
        tmp_path
        / "user"
        / "__alice_lab_audio_tools"
        / "cache"
        / "media"
        / "irodori_ref"
    )
    assert list(cache.glob("*.wav")) == [Path(first["ref_wav"])]


def test_accepts_media_range_input_audio_without_changing_selected_length(
    tmp_path, monkeypatch
) -> None:
    module = _load_module(tmp_path, monkeypatch)
    media_range = _load_media_range_module()
    source = {
        "waveform": torch.rand((1, 2, 1000)),
        "sample_rate": 1000,
        "custom": "preserved by Media Range",
    }

    selected, start, end, total = media_range.trim_audio(source, 0.2, 0.7)
    config = module.audio_to_irodori_ref_config(selected)

    assert (start, end, total) == (0.2, 0.7, 1.0)
    with wave.open(config["ref_wav"], "rb") as reference:
        assert reference.getnframes() == 500
        assert reference.getframerate() == 1000


def test_rejects_invalid_audio(tmp_path, monkeypatch) -> None:
    module = _load_module(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="empty audio"):
        module.audio_to_irodori_ref_config(
            {"waveform": torch.empty((1, 1, 0)), "sample_rate": 44100}
        )
    with pytest.raises(ValueError, match="invalid sample rate"):
        module.audio_to_irodori_ref_config(
            {"waveform": torch.ones((1, 1, 8)), "sample_rate": 0}
        )

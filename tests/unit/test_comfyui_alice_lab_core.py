from __future__ import annotations

from array import array
import ast
import importlib.util
from pathlib import Path

import torch


_MIXER_SPEC = importlib.util.spec_from_file_location(
    "alice_test_mixer",
    Path(__file__).parents[2] / "src" / "mixer.py",
)
assert _MIXER_SPEC and _MIXER_SPEC.loader
_MIXER = importlib.util.module_from_spec(_MIXER_SPEC)
_MIXER_SPEC.loader.exec_module(_MIXER)
mix_audio_tracks = _MIXER.mix_audio_tracks
parse_track_settings = _MIXER.parse_track_settings
reset_track_values = _MIXER.reset_track_values


def test_node_display_names_are_search_oriented_and_keep_node_identifiers() -> None:
    nodes_source = Path(__file__).parents[2] / "src" / "nodes.py"
    module = ast.parse(nodes_source.read_text(encoding="utf-8"))
    display_mapping = next(
        ast.literal_eval(statement.value)
        for statement in module.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "NODE_DISPLAY_NAME_MAPPINGS"
            for target in statement.targets
        )
    )
    class_mapping_keys = next(
        {ast.literal_eval(key) for key in statement.value.keys}
        for statement in module.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "NODE_CLASS_MAPPINGS"
            for target in statement.targets
        )
    )

    assert display_mapping == {
        "AliceLabMediaRange": "Load Media Range (Upload)",
        "AliceLabMediaRangePath": "Load Media Range (Path)",
        "AliceLabMediaRangeInput": "Media Range (Input)",
        "AliceLabAudioMixer": "Audio Mixer",
        "AliceLabOutputWaveform": "Output Waveform",
        "AliceLabCompareAudio": "Compare Audio",
        "AliceLabOutputFloat": "Output Float",
        "AliceLabReplaceVideoAudio": "Replace Video Audio",
        "AliceLabOutputFFmpeg": "Preview Video",
        "AliceLabSpectrogram": "Audio Spectrogram",
    }
    assert class_mapping_keys == set(display_mapping) == {
        "AliceLabMediaRange",
        "AliceLabMediaRangePath",
        "AliceLabMediaRangeInput",
        "AliceLabAudioMixer",
        "AliceLabOutputWaveform",
        "AliceLabCompareAudio",
        "AliceLabOutputFloat",
        "AliceLabReplaceVideoAudio",
        "AliceLabOutputFFmpeg",
        "AliceLabSpectrogram",
    }


def test_waveform_bucket_shape() -> None:
    samples = array("h", [0, 1000, -2000, 3000, -4000, 5000])
    point_count = 3
    peaks = []
    for index in range(point_count):
        start = index * len(samples) // point_count
        end = max(start + 1, (index + 1) * len(samples) // point_count)
        peaks.append(max(abs(value) for value in samples[start:end]) / 32768.0)
    assert peaks == [1000 / 32768.0, 3000 / 32768.0, 5000 / 32768.0]


def test_mixer_includes_every_connected_track_when_mute_and_solo_are_off() -> None:
    tracks = [
        {
            "index": index,
            "audio": {
                "waveform": torch.full((1, 2, 4), value),
                "sample_rate": 4,
            },
        }
        for index, value in enumerate((0.1, 0.2, 0.3))
    ]

    audio, individual, payload = mix_audio_tracks(
        tracks,
        parse_track_settings("[]"),
        master_db=0.0,
        prevent_clipping=False,
    )

    assert torch.allclose(audio["waveform"], torch.full((1, 2, 4), 0.6))
    assert all(track is not None for track in individual[:3])
    assert all(track is None for track in individual[3:])
    assert [track["enabled"] for track in payload["tracks"]] == [True, True, True]


def test_mixer_normalizes_mute_and_solo_on_the_same_track() -> None:
    settings = parse_track_settings('[{"mute": true, "solo": true}]')

    assert settings[0]["mute"] is True
    assert settings[0]["solo"] is False


def test_mixer_can_reset_track_values_before_run() -> None:
    settings = parse_track_settings(
        '[{"name": "Lead", "color": "#123456", "gain_db": -4, "mute": true, '
        '"offset": 1.5, "fade_in": 0.2, "fade_out": 0.3}]'
    )

    reset_track_values(settings)

    assert settings[0]["gain_db"] == 0.0
    assert settings[0]["offset"] == 0.0
    assert settings[0]["fade_in"] == 0.0
    assert settings[0]["fade_out"] == 0.0
    assert settings[0]["name"] == "Lead"
    assert settings[0]["color"] == "#123456"
    assert settings[0]["mute"] is True


def test_mixer_timeline_retains_requested_negative_offset() -> None:
    tracks = [
        {
            "index": 0,
            "audio": {
                "waveform": torch.arange(8, dtype=torch.float32).reshape(1, 1, 8),
                "sample_rate": 4,
            },
        },
    ]
    settings = parse_track_settings('[{"offset": -0.5}]')

    mixed, _, payload = mix_audio_tracks(
        tracks,
        settings,
        master_db=0.0,
        prevent_clipping=False,
    )

    assert mixed["waveform"].shape[-1] == 6
    assert payload["tracks"][0]["offset"] == -0.5
    assert payload["tracks"][0]["duration"] == 2.0


def test_individual_outputs_apply_track_processing_and_mute() -> None:
    tracks = [
        {
            "index": 0,
            "audio": {
                "waveform": torch.ones((1, 1, 4)),
                "sample_rate": 4,
            },
        },
        {
            "index": 1,
            "audio": {
                "waveform": torch.ones((1, 2, 4)),
                "sample_rate": 4,
            },
        },
    ]
    settings = parse_track_settings(
        '[{"gain_db": -6.0206, "offset": 0.5, "fade_in": 0.5}, {"mute": true}]'
    )

    mixed, individual, payload = mix_audio_tracks(
        tracks,
        settings,
        master_db=0.0,
        prevent_clipping=False,
    )

    expected = torch.tensor([0.0, 0.0, 0.0, 0.5, 0.5, 0.5]).reshape(1, 1, 6).repeat(1, 2, 1)
    assert torch.allclose(mixed["waveform"], expected, atol=1e-5)
    assert individual[0] is not None
    assert torch.allclose(individual[0]["waveform"], expected, atol=1e-5)
    assert individual[0]["alice_lab_audio_waveform_color"] == "#67c5e8"
    assert individual[0]["alice_lab_audio_tools_track_name"] == "Track 1"
    assert individual[1] is None
    assert [track["enabled"] for track in payload["tracks"]] == [True, False]
    assert payload["tracks"][0]["offset"] == 0.5

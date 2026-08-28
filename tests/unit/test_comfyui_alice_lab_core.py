from __future__ import annotations

from array import array
import ast
import importlib.util
import json
import math
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
        "AliceLabAudioToIrodoriRefConfig": "Audio to Irodori Ref Config",
        "AliceLabTranscriptRangeSelector": "Transcript Range Selector",
        "AliceLabVideoFirstLastFrame": "Video First / Last Frame",
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
        "AliceLabAudioToIrodoriRefConfig",
        "AliceLabTranscriptRangeSelector",
        "AliceLabVideoFirstLastFrame",
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
    assert [track["enabled"] for track in payload["tracks"][:3]] == [True, True, True]


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
    assert settings[0]["source_start"] == 0.0
    assert settings[0]["timeline_duration"] is None
    assert settings[0]["fade_in"] == 0.0
    assert settings[0]["fade_out"] == 0.0
    assert settings[0]["name"] == "Lead"
    assert settings[0]["color"] == "#123456"
    assert settings[0]["mute"] is True


def test_mixer_reset_discards_explicit_clip_buffer() -> None:
    settings = parse_track_settings(json.dumps([{
        "clips": [
            {"id": "original", "source_index": 0, "offset": 2},
            {"id": "copy", "source_index": 0, "offset": 10},
        ],
    }]))

    reset_track_values(settings)

    assert "clips" not in settings[0]


def test_mixer_uses_current_audio_when_an_input_is_replaced() -> None:
    settings = parse_track_settings("[]")
    first = [{
        "index": 0,
        "audio": {"waveform": torch.ones((1, 1, 3)), "sample_rate": 1},
    }]
    replacement = [{
        "index": 0,
        "audio": {"waveform": torch.full((1, 1, 3), 0.25), "sample_rate": 1},
    }]

    first_mix, _, _ = mix_audio_tracks(first, settings, 0.0, False)
    replacement_mix, _, _ = mix_audio_tracks(replacement, settings, 0.0, False)

    assert torch.allclose(first_mix["waveform"], torch.ones((1, 2, 3)))
    assert torch.allclose(replacement_mix["waveform"], torch.full((1, 2, 3), 0.25))


def test_mixer_runs_are_not_cacheable() -> None:
    assert math.isnan(_MIXER.AliceLabAudioMixer.IS_CHANGED(reset_before_run=False))
    assert math.isnan(_MIXER.AliceLabAudioMixer.IS_CHANGED(reset_before_run=True))


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
    assert [track["enabled"] for track in payload["tracks"][:2]] == [True, False]
    assert payload["tracks"][0]["offset"] == 0.5


def test_mixer_clip_duration_can_trim_restore_and_extend_with_silence() -> None:
    original = torch.arange(1, 6, dtype=torch.float32).reshape(1, 1, 5)
    tracks = [{"index": 0, "audio": {"waveform": original, "sample_rate": 1}}]

    def render(duration: float) -> tuple[torch.Tensor, dict]:
        settings = parse_track_settings(json.dumps([{"timeline_duration": duration}]))
        mixed, _, payload = mix_audio_tracks(tracks, settings, 0.0, False)
        return mixed["waveform"][0, 0], payload

    three_seconds, _ = render(3)
    assert torch.equal(three_seconds, torch.tensor([1.0, 2.0, 3.0]))

    restored, _ = render(5)
    assert torch.equal(restored, torch.tensor([1.0, 2.0, 3.0, 4.0, 5.0]))

    eight_seconds, payload = render(8)
    assert torch.equal(eight_seconds, torch.tensor([1.0, 2.0, 3.0, 4.0, 5.0, 0.0, 0.0, 0.0]))
    assert payload["duration"] == 8.0
    assert payload["tracks"][0]["source_duration"] == 5.0
    assert payload["tracks"][0]["timeline_duration"] == 8.0
    assert payload["tracks"][0]["source_end"] == 5.0

    ten_seconds, _ = render(10)
    assert torch.equal(ten_seconds[:5], original[0, 0])
    assert torch.count_nonzero(ten_seconds[5:]) == 0

    four_seconds, _ = render(4)
    assert torch.equal(four_seconds, torch.tensor([1.0, 2.0, 3.0, 4.0]))

    eight_again, _ = render(8)
    assert torch.equal(eight_again[:5], original[0, 0])
    assert torch.count_nonzero(eight_again[5:]) == 0
    assert torch.equal(original, torch.arange(1, 6, dtype=torch.float32).reshape(1, 1, 5))


def test_mixer_left_edge_recovers_source_and_can_add_leading_silence() -> None:
    original = torch.arange(1, 6, dtype=torch.float32).reshape(1, 1, 5)
    tracks = [{"index": 0, "audio": {"waveform": original, "sample_rate": 1}}]

    trimmed = parse_track_settings(
        '[{"offset": 2, "source_start": 2, "timeline_duration": 3}]'
    )
    mixed, _, _ = mix_audio_tracks(tracks, trimmed, 0.0, False)
    assert torch.equal(mixed["waveform"][0, 0], torch.tensor([0.0, 0.0, 3.0, 4.0, 5.0]))

    restored = parse_track_settings(
        '[{"offset": 0, "source_start": 0, "timeline_duration": 5}]'
    )
    mixed, _, _ = mix_audio_tracks(tracks, restored, 0.0, False)
    assert torch.equal(mixed["waveform"][0, 0], original[0, 0])

    leading_silence = parse_track_settings(
        '[{"offset": 1, "source_start": -2, "timeline_duration": 7}]'
    )
    mixed, _, payload = mix_audio_tracks(tracks, leading_silence, 0.0, False)
    assert torch.equal(
        mixed["waveform"][0, 0],
        torch.tensor([0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0]),
    )
    assert payload["tracks"][0]["source_start"] == -2.0
    assert payload["tracks"][0]["source_end"] == 5.0


def test_mixer_silent_extension_preserves_other_track_positions() -> None:
    tracks = [
        {"index": 0, "audio": {"waveform": torch.ones((1, 1, 5)), "sample_rate": 1}},
        {"index": 1, "audio": {"waveform": torch.full((1, 1, 2), 2.0), "sample_rate": 1}},
    ]
    settings = parse_track_settings(
        '[{"timeline_duration": 8}, {"offset": 6}]'
    )

    mixed, individual, payload = mix_audio_tracks(tracks, settings, 0.0, False)

    assert torch.equal(
        mixed["waveform"][0, 0],
        torch.tensor([1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 2.0, 2.0]),
    )
    assert payload["duration"] == 8.0
    assert individual[0]["waveform"].shape[-1] == 8
    assert torch.count_nonzero(individual[0]["waveform"][..., 5:]) == 0


def test_mixer_renders_copied_clips_on_the_same_and_different_tracks() -> None:
    original = torch.arange(1, 6, dtype=torch.float32).reshape(1, 1, 5)
    tracks = [{"index": 0, "audio": {"waveform": original, "sample_rate": 1}}]
    settings = parse_track_settings(json.dumps([
        {"clips": [
            {"id": "original", "source_index": 0, "offset": 0},
            {"id": "same-track-copy", "source_index": 0, "offset": 10},
        ]},
        {"clips": [
            {"id": "other-track-copy", "source_index": 0, "offset": 8},
        ]},
    ]))

    mixed, individual, payload = mix_audio_tracks(tracks, settings, 0.0, False)

    assert mixed["waveform"].shape[-1] == 15
    assert torch.equal(mixed["waveform"][0, 0, :5], original[0, 0])
    assert torch.equal(mixed["waveform"][0, 0, 10:13], torch.tensor([4.0, 6.0, 8.0]))
    assert individual[0] is not None
    assert individual[1] is not None
    assert torch.equal(individual[1]["waveform"][0, 0, 8:13], original[0, 0])
    assert [clip["id"] for clip in payload["tracks"][0]["clips"]] == [
        "original", "same-track-copy"
    ]
    assert payload["tracks"][1]["clips"][0]["source_index"] == 0


def test_mixer_copies_extended_clip_state_and_keeps_edits_independent() -> None:
    original = torch.arange(1, 6, dtype=torch.float32).reshape(1, 1, 5)
    tracks = [{"index": 0, "audio": {"waveform": original, "sample_rate": 1}}]
    settings = parse_track_settings(json.dumps([
        {"clips": [
            {
                "id": "extended",
                "source_index": 0,
                "offset": 0,
                "timeline_duration": 8,
                "gain_db": -6,
                "fade_in": 1,
            },
            {
                "id": "shortened-copy",
                "source_index": 0,
                "offset": 8,
                "timeline_duration": 3,
                "gain_db": -6,
                "fade_in": 1,
            },
        ]},
    ]))

    mixed, _, payload = mix_audio_tracks(tracks, settings, 0.0, False)

    assert mixed["waveform"].shape[-1] == 11
    assert torch.count_nonzero(mixed["waveform"][..., 5:8]) == 0
    assert [clip["timeline_duration"] for clip in payload["tracks"][0]["clips"]] == [8.0, 3.0]
    assert [clip["gain_db"] for clip in payload["tracks"][0]["clips"]] == [-6.0, -6.0]
    assert torch.equal(original, torch.arange(1, 6, dtype=torch.float32).reshape(1, 1, 5))


def test_mixer_explicitly_deleted_lane_produces_no_clip_audio() -> None:
    tracks = [{
        "index": 0,
        "audio": {"waveform": torch.ones((1, 1, 5)), "sample_rate": 1},
    }]
    settings = parse_track_settings('[{"clips": []}]')

    mixed, individual, payload = mix_audio_tracks(tracks, settings, 0.0, False)

    assert mixed["waveform"].shape[-1] == 1
    assert torch.count_nonzero(mixed["waveform"]) == 0
    assert individual[0] is None
    assert payload["tracks"][0]["clips"] == []

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from transcript_range import (
    AliceLabTranscriptRangeSelector,
    TranscriptFormatError,
    end_index_for_start,
    normalize_transcript,
    select_transcript_range,
)


@pytest.mark.parametrize(
    ("mode", "start", "current_end", "count", "expected"),
    [
        ("Same segment", 2, 4, 6, 2),
        ("Keep current", 2, 4, 6, 4),
        ("Keep current", 4, 2, 6, 4),
        ("Next segment", 2, 4, 6, 3),
        ("Next segment", 5, 1, 6, 5),
    ],
)
def test_end_follow_modes(mode, start, current_end, count, expected):
    assert end_index_for_start(mode, start, current_end, count) == expected


def test_multiple_segments_returns_selected_bounds():
    transcript = json.dumps([
        {"text": "hello", "start": 1.2, "end": 2.4},
        {"text": "world", "start": 3.1, "end": 5.7},
    ])
    start, end, _ = select_transcript_range(transcript, 0, 1)
    assert start == pytest.approx(1.2)
    assert end == pytest.approx(5.7)


def test_same_segment_is_allowed():
    transcript = json.dumps([
        {"text": "same", "start": 10.2, "end": 12.4},
    ])
    start, end, _ = select_transcript_range(transcript, 0, 0)
    assert start == pytest.approx(10.2)
    assert end == pytest.approx(12.4)


def test_japanese_unicode_is_preserved():
    original = "今日はテストをします。記号→✓\n次の行"
    transcript = json.dumps(
        [{"text": original, "start": 0.25, "end": 2.0}],
        ensure_ascii=False,
    )
    segments = normalize_transcript(transcript)
    assert segments[0]["text"] == original


@pytest.mark.parametrize(
    "value",
    [
        {"segments": [{"text": "direct", "start": 1.0, "end": 2.0}]},
        {"result": {"segments": [{"text": "result", "start": 2.0, "end": 3.0}]}},
        {
            "transcription": {
                "segments": [
                    {"text": "transcription", "start": 3.0, "end": 4.0}
                ]
            }
        },
    ],
)
def test_supported_json_segment_wrappers(value):
    segments = normalize_transcript(json.dumps(value))

    assert len(segments) == 1
    assert segments[0]["end"] - segments[0]["start"] == pytest.approx(1.0)


def test_empty_transcript_has_meaningful_error():
    with pytest.raises(TranscriptFormatError, match="Transcript contains no segments"):
        normalize_transcript("   ")


def test_missing_timestamp_has_meaningful_error():
    transcript = json.dumps([{"text": "bad", "start": 1.0}])
    with pytest.raises(
        TranscriptFormatError,
        match="missing start/end timestamp",
    ):
        normalize_transcript(transcript)


def test_end_segment_before_start_segment_is_rejected():
    transcript = json.dumps([
        {"text": "a", "start": 1.0, "end": 2.0},
        {"text": "b", "start": 3.0, "end": 4.0},
    ])
    with pytest.raises(
        TranscriptFormatError,
        match="End segment must not precede start segment",
    ):
        select_transcript_range(transcript, 1, 0)


def test_long_transcript_normalizes_without_mutating_source():
    source = [
        {"text": f"segment {index}", "start": index * 1.5, "end": index * 1.5 + 1.0}
        for index in range(10000)
    ]
    transcript = json.dumps(source)
    segments = normalize_transcript(transcript)
    assert len(segments) == 10000
    assert source[9999]["text"] == "segment 9999"
    assert segments[9999]["end"] == pytest.approx(14999.5)


def test_srt_text_is_supported_for_aifsh_srt_to_string_workflow():
    transcript = """1
00:00:01,240 --> 00:00:02,850
こんにちは

2
00:00:03,100 --> 00:00:05,720
今日はテストをします
"""
    start, end, segments = select_transcript_range(transcript, 0, 1)
    assert start == pytest.approx(1.24)
    assert end == pytest.approx(5.72)
    assert segments[0]["text"] == "こんにちは"
    assert segments[1]["text"] == "今日はテストをします"


def test_webvtt_timestamp_without_hours_is_supported():
    transcript = """WEBVTT

00:01.000 --> 00:02.500
hello
"""
    start, end, segments = select_transcript_range(transcript, 0, 0)
    assert start == pytest.approx(1.0)
    assert end == pytest.approx(2.5)
    assert segments[0]["text"] == "hello"


def test_webvtt_with_hours_and_cue_settings_is_supported():
    transcript = """WEBVTT

cue-1
01:02:03.125 --> 01:02:05.500 align:start position:10%
hello world
"""
    start, end, segments = select_transcript_range(transcript, 0, 0)
    assert start == pytest.approx(3723.125)
    assert end == pytest.approx(3725.5)
    assert segments[0]["text"] == "hello world"


def test_out_of_range_index_is_rejected():
    transcript = json.dumps([
        {"text": "a", "start": 1.0, "end": 2.0},
    ])
    with pytest.raises(TranscriptFormatError, match="out of range"):
        select_transcript_range(transcript, 0, 4)


def test_node_clamps_stale_indices_after_transcript_becomes_shorter():
    node = AliceLabTranscriptRangeSelector()
    transcript = json.dumps(
        [{"text": "only segment", "start": 0.25, "end": 2.5}]
    )

    output = node.select(transcript, 7, 9)
    payload = json.loads(output["ui"]["alice_lab_transcript_range"][0])

    assert output["result"] == (0.25, 2.5)
    assert payload["start_segment"] == 0
    assert payload["end_segment"] == 0


def test_node_keeps_end_at_or_after_clamped_start():
    node = AliceLabTranscriptRangeSelector()
    transcript = json.dumps(
        [
            {"text": "first", "start": 0.0, "end": 1.0},
            {"text": "last", "start": 1.0, "end": 2.0},
        ]
    )

    output = node.select(transcript, 8, 0)
    payload = json.loads(output["ui"]["alice_lab_transcript_range"][0])

    assert output["result"] == (1.0, 2.0)
    assert payload["start_segment"] == 1
    assert payload["end_segment"] == 1


def test_node_resets_selection_when_transcript_changes():
    node = AliceLabTranscriptRangeSelector()
    unique_id = "42"
    original = json.dumps(
        [
            {"text": "first", "start": 0.0, "end": 1.0},
            {"text": "second", "start": 1.0, "end": 2.0},
        ]
    )
    initial = node.select(
        original,
        0,
        0,
        unique_id=unique_id,
        extra_pnginfo={"workflow": {"nodes": [{"id": 42, "properties": {}}]}},
    )
    initial_payload = json.loads(initial["ui"]["alice_lab_transcript_range"][0])
    stored_workflow = {
        "workflow": {
            "nodes": [
                {
                    "id": 42,
                    "properties": {
                        "alice_transcript_fingerprint": initial_payload[
                            "transcript_fingerprint"
                        ]
                    },
                }
            ]
        }
    }

    unchanged = node.select(
        original,
        0,
        1,
        unique_id=unique_id,
        extra_pnginfo=stored_workflow,
    )
    assert unchanged["result"] == (0.0, 2.0)

    replacement = json.dumps(
        [
            {"text": "new first", "start": 3.0, "end": 4.0},
            {"text": "new second", "start": 4.0, "end": 5.0},
        ]
    )
    changed = node.select(
        replacement,
        1,
        1,
        unique_id=unique_id,
        extra_pnginfo=stored_workflow,
    )
    changed_payload = json.loads(changed["ui"]["alice_lab_transcript_range"][0])

    assert changed["result"] == (3.0, 4.0)
    assert changed_payload["start_segment"] == 0
    assert changed_payload["end_segment"] == 0
    assert (
        changed_payload["transcript_fingerprint"]
        != initial_payload["transcript_fingerprint"]
    )


def test_node_contract_and_ui_payload():
    node = AliceLabTranscriptRangeSelector()
    transcript = json.dumps(
        [
            {"text": "first", "start": 1.0, "end": 2.0},
            {"text": "last", "start": 3.0, "end": 5.0},
        ]
    )

    output = node.select(transcript, 0, 1)
    payload = json.loads(output["ui"]["alice_lab_transcript_range"][0])

    assert node.RETURN_TYPES == ("FLOAT", "FLOAT")
    assert node.RETURN_NAMES == ("start_seconds", "end_seconds")
    assert node.FUNCTION == "select"
    assert node.CATEGORY == "ALICE_Lab/Media"
    assert node.INPUT_TYPES()["required"]["end_mode"][1]["default"] == "Same segment"
    assert output["result"] == (1.0, 5.0)
    assert payload["start_segment"] == 0
    assert payload["end_segment"] == 1
    assert [segment["text"] for segment in payload["segments"]] == [
        "first",
        "last",
    ]

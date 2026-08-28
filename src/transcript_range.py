from __future__ import annotations

import json
import math
import re
from typing import Any


class TranscriptFormatError(ValueError):
    """Raised when transcript data cannot be normalized safely."""


def _as_finite_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TranscriptFormatError(f"Transcript segment {field} timestamp must be numeric.")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as error:
        raise TranscriptFormatError(
            f"Transcript segment {field} timestamp must be numeric."
        ) from error
    if not math.isfinite(seconds):
        raise TranscriptFormatError(
            f"Transcript segment {field} timestamp must be finite."
        )
    return seconds


def _normalize_segment(segment: Any) -> dict[str, Any]:
    if not isinstance(segment, dict):
        raise TranscriptFormatError("Transcript segment must be an object.")

    if "start" not in segment or "end" not in segment:
        raise TranscriptFormatError(
            "Transcript segment is missing start/end timestamp."
        )

    start = _as_finite_seconds(segment["start"], "start")
    end = _as_finite_seconds(segment["end"], "end")

    if start < 0:
        raise TranscriptFormatError(
            "Transcript segment start timestamp must be >= 0."
        )
    if end <= start:
        raise TranscriptFormatError(
            "Transcript segment end timestamp must be later than start timestamp."
        )

    text = segment.get("text", "")
    if text is None:
        text = ""
    elif not isinstance(text, str):
        text = str(text)

    return {"text": text, "start": start, "end": end}


def _segments_from_json(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise TranscriptFormatError(
            "JSON transcript must be a segment array or an object containing segments."
        )

    if isinstance(value.get("segments"), list):
        return value["segments"]

    result = value.get("result")
    if isinstance(result, dict) and isinstance(result.get("segments"), list):
        return result["segments"]

    transcription = value.get("transcription")
    if isinstance(transcription, dict) and isinstance(
        transcription.get("segments"), list
    ):
        return transcription["segments"]

    raise TranscriptFormatError(
        "JSON transcript does not contain a supported segments array."
    )


_SRT_BLOCK_SPLIT = re.compile(r"\r?\n[ \t]*\r?\n+")
_TIMESTAMP = r"(?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{1,3}"
_SRT_TIME = re.compile(
    rf"^\s*(?P<start>{_TIMESTAMP})\s*-->\s*"
    rf"(?P<end>{_TIMESTAMP})(?:\s+.*)?$"
)


def _parse_timestamp(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        hours = 0
        minutes, rest = parts
    elif len(parts) == 3:
        hours, minutes, rest = parts
    else:
        raise TranscriptFormatError(f"Invalid transcript timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _parse_srt_or_vtt(text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    normalized_text = text.replace("\ufeff", "").strip()

    if normalized_text.startswith("WEBVTT"):
        normalized_text = normalized_text[6:].lstrip("\r\n ")

    for block in _SRT_BLOCK_SPLIT.split(normalized_text):
        lines = block.splitlines()
        if not lines:
            continue

        time_index = None
        match = None
        # Supports an optional cue identifier/index immediately before timing.
        for index, line in enumerate(lines[:3]):
            match = _SRT_TIME.match(line)
            if match:
                time_index = index
                break

        if match is None or time_index is None:
            continue

        body = "\n".join(lines[time_index + 1 :])
        segments.append(
            {
                "text": body,
                "start": _parse_timestamp(match.group("start")),
                "end": _parse_timestamp(match.group("end")),
            }
        )

    if not segments:
        raise TranscriptFormatError(
            "Transcript is neither supported JSON nor timestamped SRT/VTT text."
        )
    return segments


def normalize_transcript(transcript: str) -> list[dict[str, Any]]:
    if not isinstance(transcript, str):
        raise TranscriptFormatError("Transcript input must be a STRING.")

    text = transcript.strip()
    if not text:
        raise TranscriptFormatError("Transcript contains no segments.")

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        raw_segments = _parse_srt_or_vtt(transcript)
    else:
        raw_segments = _segments_from_json(decoded)

    if not raw_segments:
        raise TranscriptFormatError("Transcript contains no segments.")

    return [_normalize_segment(segment) for segment in raw_segments]


def select_transcript_range(
    transcript: str,
    start_segment: int,
    end_segment: int,
) -> tuple[float, float, list[dict[str, Any]]]:
    segments = normalize_transcript(transcript)

    try:
        start_index = int(start_segment)
        end_index = int(end_segment)
    except (TypeError, ValueError) as error:
        raise TranscriptFormatError("Transcript segment index must be an integer.") from error

    if start_index < 0 or start_index >= len(segments):
        raise TranscriptFormatError("Start segment index is out of range.")
    if end_index < 0 or end_index >= len(segments):
        raise TranscriptFormatError("End segment index is out of range.")
    if end_index < start_index:
        raise TranscriptFormatError(
            "End segment must not precede start segment."
        )

    start_seconds = segments[start_index]["start"]
    end_seconds = segments[end_index]["end"]

    if start_seconds < 0 or end_seconds <= start_seconds:
        raise TranscriptFormatError("Selected transcript range is invalid.")

    return float(start_seconds), float(end_seconds), segments


class AliceLabTranscriptRangeSelector:
    """Select an A-B range from timestamped transcript text."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "transcript": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": (
                            "Timestamped transcript as JSON segments or SRT/VTT text. "
                            "Connect a STRING output from an STT adapter when available."
                        ),
                    },
                ),
                "start_segment": (
                    "INT",
                    {"default": 0, "min": 0, "max": 1000000, "step": 1},
                ),
                "end_segment": (
                    "INT",
                    {"default": 0, "min": 0, "max": 1000000, "step": 1},
                ),
            }
        }

    RETURN_TYPES = ("FLOAT", "FLOAT")
    RETURN_NAMES = ("start_seconds", "end_seconds")
    FUNCTION = "select"
    CATEGORY = "ALICE_Lab/Media"
    DESCRIPTION = (
        "Select start and end dialogue segments from timestamped transcript data."
    )

    def select(self, transcript: str, start_segment: int, end_segment: int):
        # Runtime validation belongs here. When transcript is connected from an
        # upstream STRING node, ComfyUI cannot provide its runtime value during
        # static pre-validation.
        start_seconds, end_seconds, segments = select_transcript_range(
            transcript, start_segment, end_segment
        )
        payload = {
            "segments": segments,
            "start_segment": int(start_segment),
            "end_segment": int(end_segment),
        }
        return {
            "ui": {
                "alice_lab_transcript_range": [
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                ]
            },
            "result": (start_seconds, end_seconds),
        }

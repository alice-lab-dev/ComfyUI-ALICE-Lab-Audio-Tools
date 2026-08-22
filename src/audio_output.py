from __future__ import annotations

import json

from comfy_api.latest import UI

from .mixer import _waveform_peaks


class AliceLabOutputWaveform:
    """Preview one standard ComfyUI AUDIO value and pass it through unchanged."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "waveform_color": ("STRING", {"default": "auto"}),
            }
        }

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    FUNCTION = "preview"
    CATEGORY = "ALICE_Lab/Audio"
    DESCRIPTION = "Preview an audio waveform inside the node and pass the audio through."
    OUTPUT_NODE = True

    def preview(self, audio, waveform_color="auto"):
        waveform = audio.get("waveform")
        sample_rate = int(audio.get("sample_rate", 0))
        if waveform is None or waveform.ndim not in (2, 3) or waveform.shape[-1] == 0:
            raise ValueError("Output Waveform received empty audio")
        if sample_rate <= 0:
            raise ValueError("Output Waveform received an invalid sample rate")
        preview_waveform = waveform.unsqueeze(0) if waveform.ndim == 2 else waveform
        payload = {
            "duration": waveform.shape[-1] / sample_rate,
            "sample_rate": sample_rate,
            "channels": waveform.shape[-2],
            "peak": float(waveform.detach().abs().max().item()),
            # Audio Out can be widened substantially in the graph.  Keep a
            # denser envelope than the mixer needs so enlarged previews retain
            # useful time detail instead of stretching the same few peaks.
            "peaks": _waveform_peaks(preview_waveform, count=3000),
            "waveform_color": (
                audio.get("alice_lab_audio_waveform_color", "#67c5e8")
                if waveform_color == "auto"
                else waveform_color
            ),
            "track_name": str(audio.get("alice_lab_audio_tools_track_name", "")).strip(),
        }
        # ComfyUI's helper writes a temporary FLAC and returns a /view-compatible
        # descriptor. The original tensor remains the node's pass-through output.
        ui = UI.PreviewAudio(audio).as_dict()
        ui["alice_lab_audio_out"] = [json.dumps(payload)]
        return {"ui": ui, "result": (audio,)}

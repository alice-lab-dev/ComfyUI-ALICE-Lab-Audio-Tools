from __future__ import annotations

import hashlib
import uuid
import wave
from pathlib import Path

import torch

try:
    from .cache_store import cache_path
except ImportError:  # Standalone module loading used by the unit tests.
    from src.cache_store import cache_path


_CACHE_FORMAT = b"alice-lab-irodori-ref-mono-pcm16-v1\0"


def _mono_pcm16(audio: dict) -> tuple[bytes, int]:
    """Convert the first standard ComfyUI AUDIO batch to mono PCM16 bytes."""
    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate", 0))
    if not isinstance(waveform, torch.Tensor) or waveform.ndim not in (2, 3):
        raise ValueError(
            "Audio to Irodori Ref Config expects waveform shape "
            "[channels, samples] or [batch, channels, samples]"
        )
    if waveform.shape[-2] == 0 or waveform.shape[-1] == 0:
        raise ValueError("Audio to Irodori Ref Config received empty audio")
    if sample_rate <= 0:
        raise ValueError("Audio to Irodori Ref Config received an invalid sample rate")

    if waveform.ndim == 3:
        if waveform.shape[0] == 0:
            raise ValueError("Audio to Irodori Ref Config received an empty audio batch")
        waveform = waveform[0]

    # Irodori-TTS averages channels before codec encoding. Do the same here so
    # the cache is small and the reference file has one unambiguous channel.
    mono = waveform.detach().to(dtype=torch.float32, device="cpu").mean(dim=0)
    mono = torch.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0)
    pcm = (
        mono.clamp(-1.0, 1.0)
        .mul(32767.0)
        .round()
        .to(torch.int16)
        .contiguous()
        .numpy()
        .tobytes()
    )
    return pcm, sample_rate


def _cache_path(pcm: bytes, sample_rate: int) -> Path:
    digest = hashlib.sha256()
    digest.update(_CACHE_FORMAT)
    digest.update(str(sample_rate).encode("ascii"))
    digest.update(b"\0")
    digest.update(pcm)
    return cache_path("media", digest.hexdigest(), ".wav", namespace="irodori_ref")


def _write_cached_wave(path: Path, pcm: bytes, sample_rate: int) -> None:
    """Atomically materialize a content-addressed WAV if it is not cached."""
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def audio_to_irodori_ref_config(
    audio: dict,
    normalize_ref_audio: bool = False,
    max_ref_seconds: float = 30.0,
) -> dict:
    pcm, sample_rate = _mono_pcm16(audio)
    path = _cache_path(pcm, sample_rate)
    _write_cached_wave(path, pcm, sample_rate)
    return {
        "ref_wav": str(path),
        "ref_latent": None,
        "no_ref": False,
        "ref_normalize_db": -16.0 if normalize_ref_audio else None,
        "ref_ensure_max": bool(normalize_ref_audio),
        "max_ref_seconds": float(max_ref_seconds),
    }


class AliceLabAudioToIrodoriRefConfig:
    """Expose a standard ComfyUI AUDIO value as an Irodori reference config."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "normalize_ref_audio": ("BOOLEAN", {"default": False}),
                "max_ref_seconds": (
                    "FLOAT",
                    {
                        "default": 30.0,
                        "min": 1.0,
                        "max": 120.0,
                        "step": 1.0,
                    },
                ),
            }
        }

    # This string intentionally matches comfy-Irodori-TTS's
    # io.Custom("IRODORI_REF_CONFIG") without importing that package.
    RETURN_TYPES = ("IRODORI_REF_CONFIG",)
    RETURN_NAMES = ("irodori_ref_config",)
    FUNCTION = "convert"
    CATEGORY = "ALICE_Lab/Audio"
    DESCRIPTION = "Convert ComfyUI AUDIO to an Irodori-TTS reference audio config."

    def convert(
        self,
        audio: dict,
        normalize_ref_audio: bool = False,
        max_ref_seconds: float = 30.0,
    ):
        return (
            audio_to_irodori_ref_config(
                audio,
                normalize_ref_audio=normalize_ref_audio,
                max_ref_seconds=max_ref_seconds,
            ),
        )

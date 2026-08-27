from __future__ import annotations

import importlib.util
from pathlib import Path
from fractions import Fraction
from types import SimpleNamespace

import pytest
import torch


_MODULE_SPEC = importlib.util.spec_from_file_location(
    "alice_test_video_frames",
    Path(__file__).parents[2] / "src" / "video_frames.py",
)
assert _MODULE_SPEC and _MODULE_SPEC.loader
_MODULE = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(_MODULE)
AliceLabVideoFirstLastFrame = _MODULE.AliceLabVideoFirstLastFrame


class VideoFromComponents:
    def __init__(self, images: torch.Tensor):
        self._components = SimpleNamespace(images=images)

    def get_components(self):
        return self._components


VideoFromComponents.__module__ = "comfy_api.latest._input_impl.video_types"


class _StreamingVideo:
    def __init__(
        self,
        images: torch.Tensor,
        frame_rate: Fraction = Fraction(24, 1),
        duration: float = 120.0,
    ):
        self.images = images
        self.frame_rate = frame_rate
        self.duration = duration
        self.trim_calls: list[tuple[float, float, bool]] = []

    def get_duration(self):
        return self.duration

    def get_frame_rate(self):
        return self.frame_rate

    def get_components(self):
        raise AssertionError("The complete file-backed VIDEO must not be materialized")

    def as_trimmed(self, start_time, duration, strict_duration):
        self.trim_calls.append((start_time, duration, strict_duration))
        if start_time == 0:
            selected = self.images[:2]
        else:
            selected = self.images[-2:]
        return SimpleNamespace(
            get_components=lambda: SimpleNamespace(images=selected)
        )


def test_extracts_first_and_last_frames_without_copying_source_storage() -> None:
    frames = torch.arange(5 * 2 * 3 * 3, dtype=torch.float32).reshape(5, 2, 3, 3)

    first, last = AliceLabVideoFirstLastFrame().extract(VideoFromComponents(frames))

    assert torch.equal(first, frames[0:1])
    assert torch.equal(last, frames[4:5])
    assert first.untyped_storage().data_ptr() == frames.untyped_storage().data_ptr()
    assert last.untyped_storage().data_ptr() == frames.untyped_storage().data_ptr()


def test_one_frame_video_returns_that_frame_for_both_outputs() -> None:
    frames = torch.rand((1, 4, 5, 3), dtype=torch.float32)

    first, last = AliceLabVideoFirstLastFrame().extract(VideoFromComponents(frames))

    assert torch.equal(first, frames)
    assert torch.equal(last, frames)


def test_empty_video_raises_meaningful_error() -> None:
    frames = torch.empty((0, 4, 5, 3), dtype=torch.float32)

    with pytest.raises(ValueError, match=r"^Video contains no frames\.$"):
        AliceLabVideoFirstLastFrame().extract(VideoFromComponents(frames))


def test_outputs_preserve_comfyui_image_contract() -> None:
    frames = torch.linspace(0.0, 1.0, 4 * 6 * 7 * 3, dtype=torch.float32).reshape(
        4, 6, 7, 3
    )

    first, last = AliceLabVideoFirstLastFrame().extract(VideoFromComponents(frames))

    assert first.shape == last.shape == (1, 6, 7, 3)
    assert first.dtype == last.dtype == frames.dtype
    assert first.device == last.device == frames.device
    assert 0.0 <= first.min() <= first.max() <= 1.0
    assert 0.0 <= last.min() <= last.max() <= 1.0


def test_rejects_non_image_component_layout() -> None:
    frames = torch.empty((4, 6, 7), dtype=torch.float32)

    with pytest.raises(ValueError, match=r"\[B, H, W, C\]"):
        AliceLabVideoFirstLastFrame().extract(VideoFromComponents(frames))


def test_file_backed_video_decodes_only_short_boundary_windows() -> None:
    frames = torch.arange(8 * 2 * 3 * 3, dtype=torch.float32).reshape(8, 2, 3, 3)
    video = _StreamingVideo(frames, duration=120.0)

    first, last = AliceLabVideoFirstLastFrame().extract(video)

    assert torch.equal(first, frames[:1])
    assert torch.equal(last, frames[-1:])
    assert len(video.trim_calls) == 2
    assert video.trim_calls[0][0] == 0.0
    assert video.trim_calls[1][0] > 119.0
    assert all(duration < 0.1 for _, duration, _ in video.trim_calls)
    assert all(strict is False for _, _, strict in video.trim_calls)


def test_node_contract() -> None:
    node = AliceLabVideoFirstLastFrame

    assert node.INPUT_TYPES() == {"required": {"video": ("VIDEO",)}}
    assert node.RETURN_TYPES == ("IMAGE", "IMAGE")
    assert node.RETURN_NAMES == ("first_frame", "last_frame")
    assert node.FUNCTION == "extract"
    assert node.CATEGORY == "ALICE_Lab/Video"

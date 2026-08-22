import json
import math

import torch
import torch.nn.functional as functional


_FONT_5X7 = {
    " ": ("00000",) * 7,
    "(": ("00100", "01000", "10000", "10000", "10000", "01000", "00100"),
    ")": ("00100", "00010", "00001", "00001", "00001", "00010", "00100"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


def _text_mask(text: str, scale: int = 1) -> torch.Tensor:
    """Create a dependency-free bitmap-font mask for chart labels."""
    glyphs = []
    for character in text.upper():
        rows = _FONT_5X7.get(character, _FONT_5X7[" "])
        glyph = torch.tensor(
            [[value == "1" for value in row] for row in rows], dtype=torch.bool
        )
        glyphs.append(glyph)
        glyphs.append(torch.zeros((7, 1), dtype=torch.bool))
    mask = torch.cat(glyphs[:-1] or [torch.zeros((7, 1), dtype=torch.bool)], dim=1)
    return mask.repeat_interleave(scale, dim=0).repeat_interleave(scale, dim=1)


def _draw_text(
    image: torch.Tensor,
    text: str,
    x: int,
    y: int,
    *,
    scale: int = 1,
    color: tuple[float, float, float] = (0.08, 0.10, 0.13),
    align: str = "left",
    vertical: bool = False,
) -> None:
    mask = _text_mask(text, scale)
    if vertical:
        mask = torch.rot90(mask, 1, (0, 1))
    if align == "center":
        x -= mask.shape[1] // 2
    elif align == "right":
        x -= mask.shape[1]
    y0, x0 = max(0, y), max(0, x)
    y1 = min(image.shape[0], y + mask.shape[0])
    x1 = min(image.shape[1], x + mask.shape[1])
    if y1 <= y0 or x1 <= x0:
        return
    visible = mask[y0 - y : y1 - y, x0 - x : x1 - x]
    region = image[y0:y1, x0:x1]
    region[visible] = torch.tensor(color, dtype=image.dtype)


def _format_axis_value(value: float, span: float) -> str:
    if span < 1:
        return f"{value:.2f}"
    if span < 20:
        return f"{value:.1f}"
    return f"{value:.0f}"


def _spectrogram(
    waveform: torch.Tensor,
    columns: int,
    bins: int = 96,
    min_db: float = -100.0,
    max_db: float = 0.0,
) -> dict[str, object]:
    """Return a compact downsampled decibel spectrogram containing raw dBFS values."""
    if min_db >= max_db:
        raise ValueError(
            f"spectrum_min_db ({min_db}) must be less than spectrum_max_db ({max_db})"
        )

    mono = waveform.mean(dim=0)
    if mono.numel() > 1_500_000:
        mono = functional.interpolate(
            mono.view(1, 1, -1), size=1_500_000, mode="linear", align_corners=False
        ).flatten()
    if mono.numel() < 64:
        mono = functional.pad(mono, (0, 64 - mono.numel()))
    n_fft = min(
        2048,
        max(64, 2 ** int(math.floor(math.log2(max(64, mono.numel()))))),
    )
    hop = min(n_fft, max(1, mono.numel() // max(1, columns)))

    window = torch.hann_window(n_fft)
    spectrum = torch.stft(
        mono,
        n_fft=n_fft,
        hop_length=hop,
        window=window,
        return_complex=True,
        center=True,
    ).abs()

    # A bin-centered amplitude-1 sine reaches window.sum() / 2 in a
    # one-sided STFT, which is the 0 dBFS reference used by the UI.
    reference = float(window.sum()) / 2.0
    spectrum_db = 20 * torch.log10(spectrum.clamp_min(1e-9) / reference)
    spectrum_db = torch.clamp(spectrum_db, min=min_db, max=max_db)
    spectrum_db = functional.interpolate(
        spectrum_db.view(1, 1, *spectrum_db.shape),
        size=(bins, max(1, columns)),
        mode="bilinear",
        align_corners=False,
    )[0, 0]

    matrix = [[round(float(value), 1) for value in row] for row in spectrum_db]
    return {"matrix": matrix, "min_db": min_db, "max_db": max_db}


def _hue2rgb(p, q, t):
    """Convert a single HSL hue component to an RGB channel value."""
    if t < 0: t += 1
    if t > 1: t -= 1
    if t < 1/6: return p + (q - p) * 6 * t
    if t < 1/2: return q
    if t < 2/3: return p + (q - p) * (2/3 - t) * 6
    return p


class AliceLabSpectrogram:
    """Standalone audio spectrogram analyzer and visualizer."""

    CATEGORY = "ALICE_Lab/Audio"
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "generate_spectrogram"
    DESCRIPTION = "Analyze and visualize the spectrogram of an audio signal, with interactive A-B range selection."
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "spectrum_min_db": ("INT", {"default": -100, "min": -144, "max": 0, "step": 1}),
                "spectrum_max_db": ("INT", {"default": 0, "min": -144, "max": 12, "step": 1}),
                "start_seconds": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.001}),
                "end_seconds": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.001}),
            }
        }

    def _generate_colormap(self, steps=256) -> torch.Tensor:
        """Generate a lookup table for HSL-to-RGB conversion to match the frontend."""
        colormap = torch.zeros((steps, 3), dtype=torch.float32)
        for i in range(steps):
            ratio = i / (steps - 1)
            h = (1.0 - ratio) * (240 / 360.0)
            s = 0.7
            l = ratio
            if s == 0:
                r = g = b = l
            else:
                q = l * (1 + s) if l < 0.5 else l + s - l * s
                p = 2 * l - q
                r = _hue2rgb(p, q, h + 1/3)
                g = _hue2rgb(p, q, h)
                b = _hue2rgb(p, q, h - 1/3)
            colormap[i] = torch.tensor([r, g, b])
        return colormap

    def _render_chart(
        self,
        spectrum_rgb: torch.Tensor,
        *,
        start_seconds: float,
        end_seconds: float,
        sample_rate: int,
        min_db: float,
        max_db: float,
    ) -> torch.Tensor:
        """Render a complete chart suitable for ComfyUI Preview Image."""
        chart_height, chart_width = 520, 900
        plot_top, plot_left = 54, 82
        plot_height, plot_width = 390, 680
        plot_bottom = plot_top + plot_height
        plot_right = plot_left + plot_width
        chart = torch.full((chart_height, chart_width, 3), 0.96, dtype=torch.float32)

        resized = functional.interpolate(
            spectrum_rgb.permute(2, 0, 1).unsqueeze(0),
            size=(plot_height, plot_width),
            mode="bilinear",
            align_corners=False,
        )[0].permute(1, 2, 0)
        chart[plot_top:plot_bottom, plot_left:plot_right] = resized

        grid_color = torch.tensor((0.42, 0.46, 0.50), dtype=chart.dtype)
        axis_color = torch.tensor((0.06, 0.08, 0.10), dtype=chart.dtype)
        for index in range(5):
            y = plot_top + round(index * (plot_height - 1) / 4)
            chart[y : y + 1, plot_left:plot_right] = grid_color
        for index in range(6):
            x = plot_left + round(index * (plot_width - 1) / 5)
            chart[plot_top:plot_bottom, x : x + 1] = grid_color
        chart[plot_top:plot_bottom, plot_left : plot_left + 2] = axis_color
        chart[plot_top:plot_bottom, plot_right - 2 : plot_right] = axis_color
        chart[plot_top : plot_top + 2, plot_left:plot_right] = axis_color
        chart[plot_bottom - 2 : plot_bottom, plot_left:plot_right] = axis_color

        _draw_text(chart, "AUDIO SPECTROGRAM", chart_width // 2, 18, scale=2, align="center")
        _draw_text(chart, "TIME (S)", (plot_left + plot_right) // 2, 486, align="center")
        _draw_text(
            chart,
            "FREQUENCY (HZ)",
            24,
            (plot_top + plot_bottom) // 2 - 42,
            align="center",
            vertical=True,
        )

        duration = max(0.0, end_seconds - start_seconds)
        for index in range(6):
            ratio = index / 5
            x = plot_left + round(ratio * (plot_width - 1))
            label = _format_axis_value(start_seconds + ratio * duration, duration)
            _draw_text(chart, label, x, plot_bottom + 12, align="center")

        nyquist = sample_rate / 2
        for index in range(5):
            ratio = index / 4
            y = plot_bottom - 1 - round(ratio * (plot_height - 1))
            _draw_text(chart, f"{round(ratio * nyquist):d}", plot_left - 10, y - 3, align="right")

        colorbar_left = plot_right + 42
        colorbar_width = 22
        colormap = self._generate_colormap(plot_height)
        chart[plot_top:plot_bottom, colorbar_left : colorbar_left + colorbar_width] = (
            torch.flip(colormap, dims=[0]).unsqueeze(1).expand(-1, colorbar_width, -1)
        )
        chart[plot_top:plot_bottom, colorbar_left : colorbar_left + 1] = axis_color
        chart[plot_top:plot_bottom, colorbar_left + colorbar_width - 1 : colorbar_left + colorbar_width] = axis_color
        chart[plot_top : plot_top + 1, colorbar_left : colorbar_left + colorbar_width] = axis_color
        chart[plot_bottom - 1 : plot_bottom, colorbar_left : colorbar_left + colorbar_width] = axis_color
        _draw_text(chart, "DBFS", colorbar_left + colorbar_width // 2, 34, align="center")
        for index in range(5):
            ratio = index / 4
            value = max_db - ratio * (max_db - min_db)
            y = plot_top + round(ratio * (plot_height - 1))
            _draw_text(chart, f"{value:+.0f}", colorbar_left + colorbar_width + 10, y - 3)

        return torch.clamp(chart, 0.0, 1.0)

    def generate_spectrogram(
        self, audio: dict, spectrum_min_db: int = -100, spectrum_max_db: int = 0, start_seconds: float = 0.0, end_seconds: float = 0.0
    ):
        sample_rate = int(audio.get("sample_rate", 44100))
        waveform = audio.get("waveform", torch.zeros((1, 1, 1024)))

        # Squeeze batch if present
        if waveform.ndim == 3:
            waveform = waveform.squeeze(0)

        total_duration = waveform.shape[-1] / sample_rate

        # Apply time slicing
        start_sample = int(start_seconds * sample_rate)
        end_sample = int(end_seconds * sample_rate) if end_seconds > 0 else waveform.shape[-1]
        start_sample = max(0, min(start_sample, waveform.shape[-1]))
        end_sample = max(start_sample + 1, min(end_sample, waveform.shape[-1]))

        sliced_waveform = waveform[..., start_sample:end_sample]
        actual_duration = (end_sample - start_sample) / sample_rate

        # Get raw dBFS matrix using existing logic (min 800 columns for UI and IMAGE)
        ui_columns = min(end_sample - start_sample, 800)
        spec_data = _spectrogram(sliced_waveform, ui_columns, bins=96, min_db=spectrum_min_db, max_db=spectrum_max_db)
        matrix = spec_data["matrix"]

        # Prepare IMAGE tensor
        # matrix is [bins][columns] of float dB values
        # We need to map these dB values to colors using the colormap
        matrix_tensor = torch.tensor(matrix, dtype=torch.float32)
        # Normalize between 0 and 1
        normalized = (matrix_tensor - spectrum_min_db) / max(1e-6, (spectrum_max_db - spectrum_min_db))
        normalized = torch.clamp(normalized, 0.0, 1.0)

        # Convert to indices (0 to 255)
        indices = (normalized * 255).long()

        colormap = self._generate_colormap(256).to(indices.device)
        image_rgb = colormap[indices] # Shape: (bins, columns, 3)

        # The frontend spectrogram draws frequencies from bottom to top, so we need to flip vertically
        image_rgb = torch.flip(image_rgb, dims=[0])

        image_out = self._render_chart(
            image_rgb,
            start_seconds=start_sample / sample_rate,
            end_seconds=end_sample / sample_rate,
            sample_rate=sample_rate,
            min_db=spectrum_min_db,
            max_db=spectrum_max_db,
        ).unsqueeze(0)

        payload = {
            "duration": actual_duration,
            "total_duration": total_duration,
            "start_seconds": start_sample / sample_rate,
            "end_seconds": end_sample / sample_rate,
            "sample_rate": sample_rate,
            "spectrum_min_db": spectrum_min_db,
            "spectrum_max_db": spectrum_max_db,
            "spectrum": spec_data,
        }

        return {
            "ui": {"alice_lab_audio_spectrogram": [json.dumps(payload)]},
            "result": (image_out,)
        }

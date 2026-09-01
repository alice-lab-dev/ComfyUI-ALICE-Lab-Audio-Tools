# ComfyUI ALICE Lab Audio Tools

<table>
  <thead><tr><th>English</th><th><a href="README_jp.md">日本語</a></th></tr></thead>
</table>

ComfyUI ALICE Lab Audio Tools is a collection of custom nodes for selecting media ranges, editing and comparing audio, visualizing waveforms and spectrograms, and replacing audio in video workflows.

> **Status:** This is an alpha release. Node interfaces and UI behavior may still change.

<p align="center">
  <a href="docs/images/workflow-overview.png">
    <img src="docs/images/workflow-overview.png" alt="ComfyUI ALICE Lab Audio Tools workflow overview" width="100%">
  </a>
</p>

<p align="center"><sub>Example workflow using ALICE Lab Audio Tools. Click the image to view it at full size.</sub></p>

## Features

| Category | Node | Purpose |
| --- | --- | --- |
| Media | `Load Media Range (Upload)` | Load media from your local device, select an A-B range while viewing its waveform, and output the selected range as `AUDIO` and `VIDEO`. |
| Media | `Load Media Range (Path)` | Open media directly from an absolute path, select an A-B range while viewing its waveform, and output the selected range as `AUDIO` and `VIDEO`. |
| Media | `Media Range (URL)` | Read only a selected interval from a direct FFmpeg-compatible HTTP(S) media URL and output it as `AUDIO` and `VIDEO`. |
| Media | `Media Range (Input)` | Preview upstream `AUDIO` or `VIDEO`, select an A-B range from its waveform, and pass the selected range downstream. |
| Media | `Transcript Range Selector` | Select start and end dialogue segments from timestamped transcript data and output their time range. |
| Audio | `Audio Mixer` | Arrange and mix up to eight tracks with waveform previews, clip resizing, and per-clip copy, paste, cut, delete, and duplicate operations across tracks. |
| Audio | `Compare Audio` | Compare two waveforms, automatically correct their time difference, and output aligned audio, difference audio, similarity, and delay. |
| Audio | `Audio Spectrogram` | Inspect the frequency content of audio as a dBFS spectrogram and output the graph as an `IMAGE`. |
| Audio | `Audio to Irodori Ref Config` | Convert `AUDIO` to the `IRODORI_REF_CONFIG` used by comfy-Irodori-TTS reference audio input. |
| Audio | `Output Waveform` | Play `AUDIO`, inspect its waveform and basic audio information, and pass it downstream unchanged. |
| Utils | `Output Float` | Display a connected `FLOAT` result with a label and selected precision, then pass it downstream unchanged. |
| Utils | `ALICE Lab Cache Manager` | Inspect or safely clear ALICE Lab transcript, thumbnail, media, and metadata caches. |
| Video | `Replace Video Audio` | Keep the video image and replace its soundtrack with processed `AUDIO`. |
| Video | `Preview Video` | Preview `VIDEO` in the node, download it with a selected filename, and pass it downstream unchanged. |
| Video | `Video First / Last Frame` | Extract the first and last frames of a `VIDEO` as `IMAGE` outputs. |

Nodes appear under these Add Node categories:

```text
ALICE_Lab
├── Audio
├── Media
├── Video
└── Utils
```

## Requirements

- ComfyUI with the current `AUDIO` type and `comfy_api.latest` video APIs.
- PyTorch, normally supplied by ComfyUI.
- `ffmpeg` and `ffprobe`, both executable by the ComfyUI process.
- A modern browser supported by the ComfyUI frontend.

`ffmpeg` and `ffprobe` are searched in the process `PATH`. On macOS, the nodes also check the common Homebrew locations `/opt/homebrew/bin` and `/usr/local/bin` because GUI-launched applications may not inherit the shell `PATH`.

No model, inference engine, CUDA runtime, FFmpeg binary, media sample, or desktop application is bundled with this project.

## Installation

1. Clone or extract this repository directly under `ComfyUI/custom_nodes`.
2. Keep the repository entry point at the custom-node root; do not add another nested package directory.
3. Make sure `ffmpeg` and `ffprobe` are visible to the same process that starts ComfyUI.
4. Restart ComfyUI.
5. Confirm that the `ALICE_Lab` categories appear in the Add Node menu.

Expected layout:

```text
ComfyUI/
└── custom_nodes/
    └── ComfyUI-ALICE-Lab-Audio-Tools/
        ├── __init__.py
        ├── src/
        │   ├── nodes.py
        │   ├── audio_compare.py
        │   └── audio_spectrogram.py
        └── web/
```

There are currently no additional pip dependencies beyond the libraries supplied by ComfyUI. FFmpeg is an external system dependency.

## Quick Start

Example workflow JSON files are available in [`docs/examples/`](docs/examples/).

### Select and process part of a video

```text
Load Media Range
  ├── audio ──> audio processing or Audio Mixer ──> Replace Video Audio.audio
  └── video ──────────────────────────────────────> Replace Video Audio.video

Replace Video Audio.video ──> Preview Video
```

<p align="center">
  <a href="docs/images/select_and_process_part_of_video.png">
    <img src="docs/images/select_and_process_part_of_video.png" alt="Select and process part of a video workflow" width="100%">
  </a>
</p>

### Compare two audio results

```text
audio result 1 ──> Compare Audio.audio_1
audio result 2 ──> Compare Audio.audio_2

Compare Audio.similarity              ──> Output Float
Compare Audio.audio_2_delay_seconds   ──> Output Float
Compare Audio.1−2 difference          ──> Output Waveform
```

<p align="center">
  <a href="docs/images/compare_two_audio _results.png">
    <img src="docs/images/compare_two_audio _results.png" alt="Compare two audio results workflow" width="100%">
  </a>
</p>

### Create a spectrogram image

```text
AUDIO ──> Audio Spectrogram ──> IMAGE
```

<p align="center">
  <a href="docs/images/create_spectrogram _mage.png">
    <img src="docs/images/create_spectrogram _mage.png" alt="Create a spectrogram image workflow" width="100%">
  </a>
</p>

### Use a selected range as Irodori-TTS reference audio

```text
Media Range (Input).audio
  ──> Audio to Irodori Ref Config.audio
  ──> IrodoriTTS Sampler.ref_config
```

This integration requires [comfy-Irodori-TTS](https://github.com/jupo-ai/comfy-Irodori-TTS) for the receiving sampler. ALICE Lab Audio Tools still loads normally when it is not installed.

## Node Reference

### Transcript Range Selector

Selects start and end transcript segments from timestamped transcription data and outputs the corresponding `start_seconds` and `end_seconds`.

The node accepts a `STRING` containing either a JSON segment array/object or timestamped SRT/WebVTT text. Internally each entry is normalized to `text`, `start`, and `end`.

Because transcript data is often produced at execution time, the workflow is human-in-the-loop: run the upstream STT once, choose Start and End from the populated selectors, then run the selector and downstream nodes again.

`End mode` controls how End follows a Start change: `Same segment` selects one dialogue segment, `Keep current` preserves End unless it would precede Start, and `Next segment` includes the following segment when available. The default is `Same segment`.

Connect the two `FLOAT` outputs to the `start_seconds` and `end_seconds` inputs of a Media Range node. For [AIFSH/ComfyUI-WhisperX](https://github.com/AIFSH/ComfyUI-WhisperX), route its `SRT` output through its included `SRTToString` node first.

### Load Media Range (Upload)

Loads media from your local device. You can select an A-B range while viewing the waveform, then output the selected range downstream as `AUDIO` and `VIDEO`.

<p align="center">
  <a href="docs/images/load-media-range-upload.png">
    <img src="docs/images/load-media-range-upload.png" alt="Load Media Range Upload node" width="720">
  </a>
</p>

Uploaded media must be 100 MB or smaller under ComfyUI's default upload limit. If ComfyUI is started with a different upload-size setting, that configured limit is used instead. Uploaded files are stored under `ComfyUI/input`.

Inputs:

- `media`: an uploaded media file.
- `start_seconds`: range start, in seconds.
- `end_seconds`: range end, in seconds.

Outputs, in order:

- `audio`: selected 44.1 kHz stereo `AUDIO`.
- `start_seconds`: actual selected start.
- `end_seconds`: actual selected end.
- `duration_seconds`: selected duration.
- `video`: trimmed `VIDEO` when the source contains video; otherwise no video value.

Use the Path variant when the media exceeds the upload limit or when you do not want to copy it into `ComfyUI/input`.

### Load Media Range (Path)

Opens media directly from an absolute path without uploading or copying it. You can select an A-B range while viewing the waveform, then output the selected range as `AUDIO` and `VIDEO`.

`media_path` must be an absolute path to a supported file. You can type or choose the path in the widget, or connect a path from another node's `STRING` output. A connected `STRING` takes precedence while leaving the saved widget value available as a fallback. When that effective path changes, unlinked A-B controls reset to the complete new file; explicitly linked `start_seconds` / `end_seconds` remain authoritative. The range controls and outputs are otherwise the same as the Upload variant.

The source is not copied into `ComfyUI/input`. The ComfyUI server process must have permission to read it.

Use this node only on a trusted ComfyUI server. Do not expose unrestricted server-side paths to untrusted users.

### Media Range (Input)

Loads exactly one upstream `AUDIO` or `VIDEO` value. `start_seconds` and `end_seconds` define a coarse input window on the upstream timeline. The waveform, preview, and A-B controls then use a local timeline beginning at zero inside that window.

This node can also be used to preview upstream output.

Accepts exactly one upstream input:

- `audio`: preserves the input sample rate and channels while trimming samples directly.
- `video`: trims the video and, when present, extracts its audio as 44.1 kHz stereo.

Run the node once to load the coarse window. The initial `end_seconds = 0` uses the complete upstream input, while the initial local `B = 0` uses the complete coarse window. Local A-B values are preserved across input-window changes when valid and are clamped or reset only when they no longer fit. The outputs report the final selection at `start_seconds + A` through `start_seconds + B` on the upstream input timeline.

For an audio-only input, the `video` output is unavailable. For a video without an audio stream, the `audio` output is unavailable.

### Media Range (URL)

Accepts a direct `http://` or `https://` media URL that FFmpeg can open. Both a typed URL and a connected `STRING` output are supported. The node does not resolve YouTube page URLs, run yt-dlp, bypass authentication, or handle DRM; use another node to resolve a site-specific page into a direct media stream URL first.

On each execution, FFprobe reads metadata. FFmpeg writes only the requested `start_seconds`–`end_seconds` interval into the ALICE Lab cache, and an identical URL and interval reuse that local file. The complete remote media is not downloaded or expanded into Python frame tensors. The returned video uses ComfyUI's standard `VIDEO` type, and the selected audio is 44.1 kHz stereo `AUDIO`.

For video, `video_encoder` accepts `auto`, `nvenc`, `videotoolbox`, or `cpu`. `auto` tests a real one-frame encode, prefers VideoToolbox on macOS and NVENC elsewhere, then falls back to CPU `libx264`. The selection also applies when a browser preview needs re-encoding. An unavailable explicitly selected hardware encoder reports an error instead of silently switching to CPU.

The preview and waveform use the local selected-interval temp file, so UI interaction does not repeatedly access the remote URL. If A-B is moved outside the currently loaded interval, run the workflow again before previewing it. Signed query strings are passed to FFmpeg as one subprocess argument and are omitted from the displayed URL label and error details.

Direct stream URLs can expire. Resolve the URL again if the node reports an expired stream, HTTP 403/404, DNS, timeout, disconnect, or unsupported-media error. Initial support expects one URL containing both video and audio, or an audio-only URL; separate video and audio URLs are not combined by this node.

The ComfyUI server performs the URL request. Use this node only on a trusted server and with media URLs you trust; it intentionally does not impose a site allowlist.

### Media Range controls

- Drag A or B to adjust a boundary.
- Click the waveform to move the playback position.
- Use `A−`, `A+`, `B−`, and `B+` for 10 ms adjustments.
- Play or loop the selected range.
- Left-drag inside the A-B selection to move the complete selection horizontally.
- Zoom to A-B, show the complete source, zoom around the pointer with the mouse wheel, and pan with right-drag.
- Change `Wave height` to adjust only the visual waveform scale.
- Read adaptive time ticks along the horizontal axis; their interval changes with zoom.

When zoomed, the node requests a more detailed signed min/max waveform for the visible range.

### Audio to Irodori Ref Config

Accepts standard ComfyUI `AUDIO` plus the same `normalize_ref_audio` and `max_ref_seconds` options as IrodoriTTS Reference Audio. It outputs `IRODORI_REF_CONFIG` for direct connection to IrodoriTTS Sampler.

<p align="center">
  <a href="docs/images/rodori_ref_config.png">
    <img src="docs/images/rodori_ref_config.png" alt="Audio to Irodori Ref Config node" width="360">
  </a>
</p>

The first audio batch is averaged to mono and written at its original sample rate as a content-addressed PCM16 WAV in the persistent ALICE Lab cache. Rerunning identical audio reuses the same file. Currently, this can be used with the IrodoriTTS Sampler from comfy-Irodori-TTS.

### ALICE Lab Cache Manager

ALICE Lab caches are stored persistently under `ComfyUI/user/__alice_lab_audio_tools/cache/` in four categories: `transcripts`, `thumbnails`, `media`, and `metadata`. They survive ComfyUI restarts, unlike files under `ComfyUI/temp`. Media Range waveform data, browser preview proxies, URL intervals, and Irodori reference WAV files use this shared cache layout.

Use `inspect` to report file counts and byte sizes. To delete cached data, select `clear`, choose one category or `all`, and enable `confirm_clear`. Deletion is restricted to the four category directories and does not follow symbolic links outside the ALICE Lab cache root.

### Audio Mixer

Displays up to eight audio tracks on a shared timeline. You can arrange them while viewing their waveforms, adjust each track, and output both the completed mix and the processed individual tracks.

<p align="center">
  <a href="docs/images/audio-mixer.png">
    <img src="docs/images/audio-mixer.png" alt="Audio Mixer node" width="760">
  </a>
</p>

Accepts up to eight optional inputs, `audio_1` through `audio_8`.

Controls:

- Per-track name and waveform color.
- Per-clip gain from -100 dB to +24 dB, position, fade in, and fade out.
- Mute and solo; one track cannot be both muted and soloed.
- Master gain from -100 dB to +24 dB.
- Optional peak-based clipping protection.
- `reset_before_run` clears clip edits and copied/pasted clips, then resets gain, position, and fades.
- Shared timeline with adaptive time ticks, mouse-wheel zoom, right-drag panning, and `Show All` to fit every positioned waveform, including waveforms moved outside the previous view.

Click a clip to select it and drag it to change its timeline position. Drag either edge to shorten or extend the clip; trimmed audio can be restored, and areas beyond the available source audio are added as silence. Drag the top handles to edit linear fades. Double-click the `dB`, `Pos`, `Fade In`, or `Fade Out` label to reset that value to zero. The waveform and mixed output follow the edited clip length.

Right-click a clip for `Copy`, `Cut`, `Duplicate`, or `Delete`. After copying or cutting, right-click the destination time on the same or another track and choose `Paste`. Duplicate places a new editable clip directly after the source clip.

A positive position delays a clip. A negative position removes samples that fall before the shared timeline starts.

Outputs:

- `mixed_audio`.
- `track_1` through `track_8`, with track gain, position, and fades applied.

Muted, inactive-solo, missing-track outputs are blocked instead of emitting silent audio. Clipping protection applies uniform peak normalization; it is not a limiter, compressor, or LUFS normalizer.

### Compare Audio

Displays two waveforms for comparison. It can automatically correct their time difference and output aligned audio, difference audio, an overlay, similarity, and the detected delay.

<p align="center">
  <a href="docs/images/compare-audio.png">
    <img src="docs/images/compare-audio.png" alt="Compare Audio node" width="720">
  </a>
</p>

Inputs:

- `audio_1` and `audio_2`.
- `auto_align`: enables time alignment.
- `max_shift_seconds`: search limit from 0 to 30 seconds; default 2 seconds.

Outputs:

- `Audio 1 only`.
- `Audio 2 only`.
- `1−2 difference`.
- `similarity`, from 0.0 to 1.0.
- `audio_2_delay_seconds`; a negative value means Audio 2 was advanced.
- `1+2 overlay`, mixed at equal gain.

The node displays aligned or pre-alignment waveforms, adaptive time ticks, selection controls, playback for each output, alignment and waveform metrics, delay, zoom, pan, loop, and fixed-display controls. With Fixed display enabled, `All (stacked)` shows Audio 1, Audio 2, and the 1−2 difference vertically on the same time axis.

With auto-alignment enabled, similarity is `0.65 × alignment score + 0.35 × absolute waveform correlation`. With it disabled, similarity is the absolute waveform correlation. `Similarity`, `Alignment`, `Waveform`, and `Delay` show the complete Run result and remain fixed while zooming. `Visible Waveform` shows the waveform correlation for the visible time range and changes with zoom or pan. This is a signal comparison metric, not speech recognition, speaker identification, perceptual quality assessment, or proof that two recordings are identical.

Similarity metrics are displayed as percentages with six decimal places. Delay is displayed in seconds with four decimal places.

Interactive playback is limited to 600 seconds per request. Detailed comparison data is stored in a bounded in-memory session; run the node again if that session expires.

### Audio Spectrogram

Displays the frequency content of `AUDIO` as a dBFS spectrogram and outputs the displayed graph as an `IMAGE` for downstream use.

<p align="center">
  <a href="docs/images/audio-spectrogram.png">
    <img src="docs/images/audio-spectrogram.png" alt="Audio Spectrogram node" width="520">
  </a>
</p>

Inputs:

- `audio`.
- `spectrum_min_db`: -144 to 0 dBFS; default -100.
- `spectrum_max_db`: -144 to +12 dBFS; default 0.
- `start_seconds` and `end_seconds`; an end of 0 analyzes the complete input.

The node displays an interactive dBFS spectrogram. Hover to inspect dBFS, time, and frequency. Drag a range to update the time widgets, then run again to analyze that selection.

Its `IMAGE` output is a 900 × 520 RGB chart with a title, time axis, frequency axis, and dBFS color bar.

### Output Waveform

Plays an `AUDIO` value so you can confirm its sound and waveform. The waveform includes adaptive time ticks. The node also displays duration, sample rate, channel count, and peak dBFS, then returns the unchanged `audio` output.

<p align="center">
  <a href="docs/images/output-waveform.png">
    <img src="docs/images/output-waveform.png" alt="Output Waveform node" width="760">
  </a>
</p>

`waveform_color = auto` uses color metadata already attached to the input `AUDIO`, including metadata produced by Audio Mixer. A color may also be specified manually.

### Output Float

Displays a connected `FLOAT` result with a custom label and 0 to 12 decimal places, making values such as similarity and delay easy to check. It then returns the same value.

<p align="center">
  <a href="docs/images/output-float.png">
    <img src="docs/images/output-float.png" alt="Output Float node" width="430">
  </a>
</p>

### Replace Video Audio

Keeps the image from the connected `VIDEO` and replaces its soundtrack with the connected `AUDIO`. The result is output as a new `VIDEO` for preview or further processing.

- The first audio batch is converted to PCM16 for FFmpeg input.
- Video is stream-copied when possible.
- Audio is encoded as AAC at 192 kbps.
- Short audio is padded with silence; long audio is trimmed to the video duration.
- If video stream copy fails, the node retries with the H.264 encoder selected by `video_encoder`: `auto`, `nvenc`, `videotoolbox`, or `cpu`.
- If no video is connected, downstream video execution is blocked safely.

<p align="center">
  <a href="docs/images/replace_video_audio.png">
    <img src="docs/images/replace_video_audio.png" alt="Replace Video Audio node" width="333">
  </a>
</p>

### Preview Video

Displays a connected `VIDEO` directly in the node for playback and confirmation. It can be downloaded with the name entered in `filename`, and the unchanged `video` is also passed downstream.

`video_encoder` accepts `auto`, `nvenc`, `videotoolbox`, or `cpu`. An untrimmed file-backed `VIDEO` is stream-copied when MP4-compatible. A trimmed file-backed `VIDEO`, including the result of Replace Video Audio with a logical duration, is sent directly to FFmpeg and encoded with the selected encoder instead of ComfyUI's CPU-only PyAV H.264 path. The node status shows the actual path used: `copy`, `nvenc`, `videotoolbox`, `cpu`, or `comfy` for tensor-backed video.

<p align="center">
  <a href="docs/images/preview-video.png">
    <img src="docs/images/preview-video.png" alt="Preview Video node" width="500">
  </a>
</p>

The Save button downloads the temporary preview through the browser. It does not write directly to an arbitrary server-side output directory. Hardware-encoded previews use speed-oriented settings because this node is intended for responsive review rather than final mastering output.

### Video First / Last Frame

Extracts the first and last frames of a `VIDEO` as ComfyUI `IMAGE` outputs. It can obtain the start and end images from a video segment produced by Media Range.

## Supported Media Extensions

```text
aac  aiff  avi  flac  m2ts  m4a  m4v  mkv  mov
mp3  mp4   mpg  mpeg  ogg   opus ts   wav  webm  wma
```

Extension support does not guarantee codec support. The installed FFmpeg build must be able to decode the file.

## Platform Notes

### Windows

Make both `ffmpeg.exe` and `ffprobe.exe` visible to the ComfyUI process. Verify drive-letter paths, long paths, and non-ASCII filenames in your environment.

### macOS

Apple Silicon media loading has been exercised with Homebrew FFmpeg/ffprobe 8.1.1. The code includes `/opt/homebrew/bin` and `/usr/local/bin` fallbacks for GUI-launched ComfyUI processes. Other ComfyUI, frontend, browser, and FFmpeg combinations still require verification.

### Ubuntu / Linux

On Ubuntu and other Linux distributions, install FFmpeg through the system package manager or another trusted source, and make both commands visible in the environment used to launch ComfyUI.

A complete release compatibility matrix has not yet been finalized. Compatibility depends on the ComfyUI video API, frontend version, browser, FFmpeg build, codecs, and display scaling.

## Troubleshooting

### Nodes do not appear

- Confirm that `__init__.py` is at the custom-node repository root.
- Check the ComfyUI terminal for import errors.
- Confirm that your ComfyUI build provides `comfy_api.latest` and the current video APIs.
- Restart ComfyUI after installation.

### The UI width or scaling looks wrong

After a ComfyUI or frontend update, perform a hard refresh so old and new frontend assets are not mixed. On Windows and Linux browsers, use `Ctrl+Shift+R`. On macOS browsers, use the browser's equivalent cache-bypassing reload.

### FFmpeg or ffprobe is not found

- Run both commands from the environment used to start ComfyUI.
- If ComfyUI is launched from a GUI, verify the process environment rather than only the interactive shell.
- On macOS, check `/opt/homebrew/bin` and `/usr/local/bin`.
- Restart ComfyUI after changing `PATH`.

### A large upload is rejected

Use `Load Media Range (Path)` or change ComfyUI's upload-size configuration. The Path variant does not upload or copy the source file.

### Comparison data expired

Run `Compare Audio` again. Interactive analysis sessions are intentionally bounded to limit memory use.

## Known Limitations

- Media support is filtered by extension and then limited by the installed FFmpeg codecs.
- File-based Media Range output audio is always 44.1 kHz stereo. Direct `AUDIO` input to Media Range (Input) preserves its original format.
- Audio to Irodori Ref Config uses the first audio batch and averages all of its channels to mono.
- Compare Audio uses only the first audio batch and at most two channels.
- Automatic alignment uses amplitude-envelope correlation and may select an unintended offset for silence, unrelated sources, repetitive content, or delays outside the search range.
- Audio Mixer supports at most eight inputs.
- Replace Video Audio produces MP4. A stream-copy fallback requires at least one usable selected H.264 encoder; `cpu` specifically requires `libx264`.
- Preview Video downloads a temporary browser preview; it is not a server-side save node.

## Disclaimer

This project is provided as-is, without warranty of any kind.

Use of this software is at your own risk. The authors and contributors are not responsible for any data loss, damage, legal issues, or other consequences resulting from its use.

Users are responsible for ensuring that any audio, video, or other media processed with this software is used in accordance with applicable laws, licenses, copyrights, and terms of service.

## License

This project is licensed under the Apache License 2.0.
See the [LICENSE](LICENSE) file for the full license terms and the [NOTICE](NOTICE) file for copyright and attribution information.

## Author

ALICE Lab

## Articles and Workflows

Development notes, experiments, usage examples, and practical workflows for ALICE Lab Audio Tools are published on note.

[ALICE Lab Audio Tools Development Log on note](https://note.com/mydearnana/m/m84330804a3d4)

## Support

If you find these tools useful and would like to support ongoing development, testing, and maintenance, you can support ALICE Lab here:

[Buy Me a Coffee](https://buymeacoffee.com/alicelabdev)

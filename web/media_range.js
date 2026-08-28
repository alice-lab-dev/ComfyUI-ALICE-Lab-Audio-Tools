import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { drawTimeAxis } from "./time_axis.js";

const WAVEFORM_LEFT_PADDING = 46;
const WAVEFORM_RIGHT_PADDING = 18;

function formatTime(seconds) {
    const value = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const secs = value % 60;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${secs.toFixed(3).padStart(6, "0")}`;
}

function parseTime(text) {
    const parts = String(text).trim().split(":");
    if (!parts.length || parts.length > 3 || parts.some((part) => part.trim() === "")) return null;
    const values = parts.map(Number);
    if (values.some((value) => !Number.isFinite(value) || value < 0)) return null;
    let seconds = 0;
    for (const value of values) seconds = seconds * 60 + value;
    return seconds;
}

function chainCallback(target, key, callback) {
    // Preserve callbacks installed by ComfyUI or other extensions when
    // ALICE observes the same lifecycle event.
    const original = target[key];
    target[key] = function (...args) {
        const result = original?.apply(this, args);
        callback.apply(this, args);
        return result;
    };
}

app.registerExtension({
    name: "ALICE_Lab.MediaRange",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!["AliceLabMediaRange", "AliceLabMediaRangePath", "AliceLabMediaRangeInput"].includes(nodeData.name)) return;

        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            const node = this;
            const isPathNode = nodeData.name === "AliceLabMediaRangePath";
            const isInputNode = nodeData.name === "AliceLabMediaRangeInput";
            let mediaWidget = isInputNode
                ? null
                : node.widgets.find((widget) => widget.name === (isPathNode ? "media_path" : "media"));
            if (isPathNode && app.widgets?.VHSPATH && mediaWidget?.type !== "VHS.PATH") {
                // Use VHS's actual path widget rather than relying on extension
                // registration order to upgrade ALICE's STRING widget.
                const widgetIndex = node.widgets.indexOf(mediaWidget);
                const previousValue = mediaWidget.value;
                node.widgets.splice(widgetIndex, 1);
                const replacement = app.widgets.VHSPATH(node, "media_path", [
                    "VHSPATH",
                    nodeData.input.required.media_path[1],
                ]);
                node.widgets.pop();
                node.widgets.splice(widgetIndex, 0, replacement);
                replacement.value = previousValue;
                mediaWidget = replacement;
            }
            const startWidget = node.widgets.find((widget) => widget.name === "start_seconds");
            const endWidget = node.widgets.find((widget) => widget.name === "end_seconds");
            // All interaction state is scoped to this node instance so several
            // media-range nodes can coexist in one workflow independently.
            let duration = 0;
            let peaks = [];
            let waveformStart = 0;
            let waveformEnd = 0;
            let waveformScale = 1;
            let normalization = 1;
            let viewStart = 0;
            let viewEnd = 0;
            let activeMarker = null;
            let selectedMarker = null;
            let pointerDown = null;
            let markerMoved = false;
            let panning = false;
            let movingSelection = false;
            let selectionDragCandidate = false;
            let panOriginX = 0;
            let panOriginStart = 0;
            let selectionOriginTime = 0;
            let selectionOriginStart = 0;
            let selectionOriginEnd = 0;
            let selectionOriginX = 0;
            let rangePlayback = false;
            let loopPlayback = false;
            let loadSerial = 0;
            let waveformSerial = 0;
            let waveformTimer = null;

            const root = document.createElement("div");
            root.style.cssText = "display:flex;flex-direction:column;gap:6px;padding:4px;background:#15191f;color:#dce3ea;overflow-x:hidden;overflow-y:auto;box-sizing:border-box";
            const video = document.createElement("video");
            video.controls = true;
            video.preload = "metadata";
            video.style.cssText = "width:100%;max-height:260px;background:#080a0d;display:none";
            const audio = document.createElement("audio");
            audio.controls = true;
            audio.preload = "metadata";
            audio.style.cssText = "width:100%;height:40px;flex:none;display:none";
            let activeMedia = video;
            let sourceHasVideo = false;
            let sourceHasAudio = false;
            let inputPayload = null;
            let sourceDisplayName = "";
            let inputHasLoaded = false;
            let executedRange = null;
            const canvas = document.createElement("canvas");
            canvas.height = 180;
            canvas.style.cssText = "width:100%;height:180px;min-height:120px;flex:none;background:#101419;border:1px solid #39424e;cursor:pointer;touch-action:none;box-sizing:border-box";
            const rangeHeader = document.createElement("div");
            rangeHeader.style.cssText = "display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:8px;background:#242830;border:1px solid #39424e;border-radius:6px;font:12px sans-serif";
            const controls = document.createElement("div");
            controls.style.cssText = "display:flex;align-items:center;flex-wrap:wrap;gap:8px;font:12px sans-serif";
            const playButton = document.createElement("button");
            playButton.textContent = "▶";
            playButton.title = "Play A–B";
            playButton.setAttribute("aria-label", "Play A–B");
            playButton.style.cssText = "display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;width:34px;min-width:34px;height:22px;padding:0;font:12px/1 sans-serif;text-align:center;overflow:hidden";
            const loopButton = document.createElement("button");
            loopButton.textContent = "↻";
            loopButton.title = "Loop: OFF";
            loopButton.setAttribute("aria-label", "Loop: OFF");
            loopButton.style.cssText = "display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;width:34px;min-width:34px;height:22px;padding:0;font:12px/1 sans-serif;text-align:center;overflow:hidden";
            const mediaLabel = document.createElement("strong");
            mediaLabel.style.cssText = "flex:1;min-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
            const startInput = document.createElement("input");
            const endInput = document.createElement("input");
            for (const input of [startInput, endInput]) {
                input.type = "text";
                input.style.cssText = "width:105px;padding:4px 6px;background:#171a20;color:#e7edf3;border:1px solid #48515e;border-radius:4px;font:12px monospace";
            }
            const startMinus = document.createElement("button");
            const startPlus = document.createElement("button");
            const endMinus = document.createElement("button");
            const endPlus = document.createElement("button");
            startMinus.textContent = "A−"; startPlus.textContent = "A+";
            endMinus.textContent = "B−"; endPlus.textContent = "B+";
            const zoomButton = document.createElement("button");
            zoomButton.textContent = "Zoom A–B";
            const allButton = document.createElement("button");
            allButton.textContent = "Show All";
            const addButton = document.createElement("button");
            addButton.textContent = isInputNode ? "Run to refresh" : isPathNode ? "Load Path" : "Add Media";
            addButton.style.display = isInputNode ? "none" : "";
            const scaleLabel = document.createElement("label");
            scaleLabel.textContent = "Wave height";
            const scaleInput = document.createElement("input");
            scaleInput.type = "range";
            scaleInput.min = "0.25";
            scaleInput.max = "4";
            scaleInput.step = "0.05";
            scaleInput.value = "1";
            scaleInput.style.width = "110px";
            scaleLabel.append(scaleInput);
            const fileInput = document.createElement("input");
            fileInput.type = "file";
            fileInput.accept = "audio/*,video/*,.mkv,.m2ts,.ts,.flac,.wav,.opus";
            fileInput.style.display = "none";
            const label = document.createElement("span");
            label.style.flex = "1";
            label.style.minWidth = "250px";
            rangeHeader.append(playButton, mediaLabel, document.createTextNode("A"), startInput, startMinus, startPlus, document.createTextNode("B"), endInput, endMinus, endPlus, loopButton);
            controls.append(zoomButton, allButton, addButton, scaleLabel, label);
            root.append(video, audio, rangeHeader, canvas, controls, fileInput);

            const widget = node.addDOMWidget("alice_lab_audio_tools_range", "ALICE_LAB_MEDIA_RANGE", root, {
                serialize: false,
                hideOnZoom: true,
            });
            let panelHeight = 245;
            let fitPending = false;
            function minimumPanelHeight() {
                return sourceHasVideo ? 500 : 285;
            }
            function layoutToNodeHeight() {
                // Controls retain their natural height; preview and waveform
                // share whatever vertical space remains in the DOM panel.
                const hasVideo = sourceHasVideo;
                const audioHeight = !hasVideo && audio.style.display !== "none" ? 40 : 0;
                const targetHeight = panelHeight;
                const headerHeight = Math.max(44, rangeHeader.scrollHeight || 44);
                const controlsHeight = Math.max(34, controls.scrollHeight || 34);
                const fixedHeight = headerHeight + controlsHeight + audioHeight + 30;
                const flexibleHeight = Math.max(hasVideo ? 220 : 120, targetHeight - fixedHeight);
                const videoHeight = hasVideo ? Math.max(100, flexibleHeight * 0.52) : 0;
                const waveformHeight = Math.max(120, flexibleHeight - videoHeight);
                root.style.height = `${targetHeight}px`;
                video.style.height = `${videoHeight}px`;
                video.style.maxHeight = video.style.height;
                canvas.style.height = `${waveformHeight}px`;
                draw();
            }
            widget.computeSize = (width) => {
                // Report only the true minimum to ComfyUI.  Reporting the
                // expanded display height here prevents the node shrinking.
                return [width, minimumPanelHeight()];
            };

            // Fill only the height already assigned to the node.  Measuring the
            // non-DOM overhead first avoids the resize feedback loop that occurs
            // when the widget and node repeatedly enlarge one another.
            function fitPanelToNode() {
                fitPending = false;
                root.style.width = `${Math.max(120, node.size[0] - 20)}px`;
                const minimum = minimumPanelHeight();
                const computedHeight = Number(node.computeSize?.()[1]) || minimum;
                const chromeHeight = Math.max(0, computedHeight - minimum);
                const availableHeight = Math.max(minimum, node.size[1] - chromeHeight);
                if (Math.abs(availableHeight - panelHeight) < 1) return;
                panelHeight = availableHeight;
                layoutToNodeHeight();
                node.graph?.setDirtyCanvas(true, true);
            }

            function schedulePanelFit() {
                if (fitPending) return;
                fitPending = true;
                requestAnimationFrame(fitPanelToNode);
            }

            chainCallback(node, "onResize", schedulePanelFit);
            chainCallback(node, "onConfigure", schedulePanelFit);
            node.setSize([Math.max(node.size[0], 680), Math.max(node.size[1], 610)]);
            schedulePanelFit();

            function syncLabel() {
                // Separate spans keep A, B, and duration aligned with the marker
                // colors used in the waveform.
                const selectionDuration = (endWidget.value - startWidget.value).toFixed(3);
                label.replaceChildren();
                for (const [text, color] of [
                    [`A ${formatTime(startWidget.value)}`, "#6bd39a"],
                    [`B ${formatTime(endWidget.value)}`, "#ff7474"],
                    [`duration ${selectionDuration}s`, "#79c8f2"],
                ]) {
                    const part = document.createElement("span");
                    part.textContent = text;
                    part.style.cssText = `color:${color};white-space:nowrap;margin-right:10px;font-weight:600`;
                    label.append(part);
                }
                if (document.activeElement !== startInput) startInput.value = formatTime(startWidget.value);
                if (document.activeElement !== endInput) endInput.value = formatTime(endWidget.value);
                const sourceName = isInputNode ? sourceDisplayName : mediaWidget?.value;
                mediaLabel.textContent = sourceName || (isInputNode ? "Run to load connected input" : "No media");
                mediaLabel.title = sourceName || "";
            }

            function draw() {
                // Scale the backing bitmap for crisp HiDPI rendering while
                // continuing to draw with CSS-pixel coordinates.
                // DOM widgets can be CSS-transformed with the ComfyUI canvas.
                // Draw in the untransformed CSS coordinate space so a redraw
                // does not apply the node zoom to the bitmap a second time.
                const rect = {
                    width: canvas.clientWidth,
                    height: canvas.clientHeight,
                };
                const scale = window.devicePixelRatio || 1;
                canvas.width = Math.max(1, Math.round(rect.width * scale));
                const height = Math.max(120, rect.height || 180);
                canvas.height = Math.round(height * scale);
                const ctx = canvas.getContext("2d");
                ctx.setTransform(scale, 0, 0, scale, 0, 0);
                const width = rect.width;
                const plotLeft = WAVEFORM_LEFT_PADDING;
                const plotRight = Math.max(plotLeft + 1, width - WAVEFORM_RIGHT_PADDING);
                const plotWidth = plotRight - plotLeft;
                ctx.fillStyle = "#101419";
                ctx.fillRect(0, 0, width, height);
                ctx.font = "9px sans-serif";
                ctx.textAlign = "right";
                ctx.textBaseline = "middle";
                for (const [value, text] of [[1, "+1 rel"], [0.5, "+0.5"], [0, "0"], [-0.5, "−0.5"], [-1, "−1 rel"]]) {
                    const y = height / 2 - value * height * 0.34;
                    ctx.strokeStyle = value === 0 ? "#48515c" : "#29313a";
                    ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(plotRight, y); ctx.stroke();
                    ctx.fillStyle = "#8795a3";
                    ctx.fillText(text, plotLeft - 4, y);
                }
                ctx.textAlign = "left";
                ctx.textBaseline = "alphabetic";
                if (peaks.length) {
                    ctx.strokeStyle = "#67c5e8";
                    ctx.beginPath();
                    for (let x = plotLeft; x < plotRight; x++) {
                        const time = viewStart + (x - plotLeft) / plotWidth * Math.max(0.001, viewEnd - viewStart);
                        const ratio = (time - waveformStart) / Math.max(1e-9, waveformEnd - waveformStart);
                        const index = Math.max(0, Math.min(peaks.length - 1, Math.floor(ratio * peaks.length)));
                        const value = peaks[index];
                        const pair = Array.isArray(value) ? value : [-Number(value || 0), Number(value || 0)];
                        const low = Math.max(-1, Math.min(1, Number(pair[0]) * normalization * waveformScale));
                        const high = Math.max(-1, Math.min(1, Number(pair[1]) * normalization * waveformScale));
                        ctx.moveTo(x + 0.5, height / 2 - high * (height * 0.34));
                        ctx.lineTo(x + 0.5, height / 2 - low * (height * 0.34));
                    }
                    ctx.stroke();
                }
                if (duration > 0) {
                    drawTimeAxis(ctx, {
                        left: plotLeft,
                        right: plotRight,
                        top: 0,
                        bottom: height,
                        start: viewStart,
                        end: viewEnd,
                    });
                }
                if (duration > 0) {
                    const span = Math.max(0.001, viewEnd - viewStart);
                    const sx = plotLeft + (startWidget.value - viewStart) / span * plotWidth;
                    const ex = plotLeft + (endWidget.value - viewStart) / span * plotWidth;
                    const bandTop = 20;
                    const bandBottom = height - 28;
                    const bandLeft = Math.max(plotLeft, sx);
                    const bandRight = Math.min(plotRight, ex);
                    ctx.fillStyle = "rgba(76,184,229,.20)";
                    ctx.fillRect(bandLeft, bandTop, Math.max(0, bandRight - bandLeft), bandBottom - bandTop);
                    ctx.strokeStyle = "#55bde8"; ctx.lineWidth = 2;
                    ctx.strokeRect(bandLeft, bandTop, Math.max(0, bandRight - bandLeft), bandBottom - bandTop);
                    for (const [x, color, text, marker] of [[sx, "#42d392", "A", "start"], [ex, "#ff6b6b", "B", "end"]]) {
                        if (x < 0 || x > width) continue;
                        // Keep the marker time exact, but keep the complete
                        // eight-pixel drag handle inside the plot at either end.
                        // In particular, B used to lose its right half when an
                        // A-B selection was moved to the media endpoint.
                        const markerHalfWidth = 4;
                        const markerX = Math.max(
                            plotLeft + markerHalfWidth,
                            Math.min(plotRight - markerHalfWidth, x),
                        );
                        const axisX = Math.max(plotLeft, Math.min(plotRight, x));
                        ctx.strokeStyle = color; ctx.lineWidth = 2;
                        if (selectedMarker === marker) ctx.lineWidth = 4;
                        ctx.beginPath(); ctx.moveTo(axisX, 0); ctx.lineTo(axisX, height); ctx.stroke();
                        ctx.fillStyle = color; ctx.font = "bold 12px sans-serif";
                        const markerLabelX = marker === "end" ? axisX - 12 : axisX + 4;
                        ctx.fillText(text, Math.max(2, Math.min(width - 14, markerLabelX)), 15);
                        ctx.fillStyle = color;
                        ctx.fillRect(markerX - 4, height / 2 - 12, 8, 24);
                        const badge = formatTime(marker === "start" ? startWidget.value : endWidget.value).replace(/^00:/, "");
                        ctx.font = "11px monospace";
                        const badgeWidth = ctx.measureText(badge).width + 10;
                        const badgeX = Math.max(0, Math.min(width - badgeWidth, markerX - badgeWidth / 2));
                        ctx.fillRect(badgeX, height - 23, badgeWidth, 19);
                        ctx.fillStyle = "#0b1116";
                        ctx.fillText(badge, badgeX + 5, height - 9);
                    }
                    if (Number.isFinite(activeMedia.currentTime)) {
                        const px = plotLeft + (activeMedia.currentTime - viewStart) / span * plotWidth;
                        if (px >= plotLeft && px <= plotRight) {
                            ctx.strokeStyle = "#ffd166"; ctx.lineWidth = 1;
                            ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, height); ctx.stroke();
                        }
                    }
                }
                syncLabel();
            }

            function updateWidget(target, value) {
                // Invoke the native callback so serialized values and ComfyUI's
                // downstream execution state stay synchronized.
                target.value = Math.round(value * 1000) / 1000;
                target.callback?.(target.value);
                node.graph?.setDirtyCanvas(true, true);
            }

            async function requestVisibleWaveform(resetNormalization = false) {
                clearTimeout(waveformTimer);
                if (!duration || viewEnd <= viewStart || !sourceHasAudio) return;
                if (!isInputNode && !mediaWidget?.value) return;
                if (isInputNode && !inputPayload?.filename) return;
                const serial = ++waveformSerial;
                const filename = isInputNode ? inputPayload.filename : mediaWidget.value;
                const points = Math.max(512, Math.min(12000, Math.ceil(canvas.clientWidth * 2)));
                const query = new URLSearchParams(
                    isInputNode ? { preview: filename } : isPathNode ? { path: filename } : { filename }
                );
                query.set("start", String(viewStart));
                query.set("end", String(viewEnd));
                query.set("points", String(points));
                try {
                    const response = await api.fetchApi(`/alice_lab_audio_tools/waveform?${query}`);
                    const wave = await response.json();
                    if (!response.ok) throw new Error(wave.error || "Waveform generation failed");
                    const currentFilename = isInputNode ? inputPayload?.filename : mediaWidget?.value;
                    if (serial !== waveformSerial || filename !== currentFilename) return;
                    peaks = wave.peaks || [];
                    waveformStart = Number(wave.start ?? viewStart);
                    waveformEnd = Number(wave.end ?? viewEnd);
                    if (resetNormalization) {
                        const maximum = peaks.reduce((result, value) => {
                            const pair = Array.isArray(value) ? value : [-Number(value || 0), Number(value || 0)];
                            return Math.max(result, Math.abs(Number(pair[0]) || 0), Math.abs(Number(pair[1]) || 0));
                        }, 0);
                        normalization = maximum > 0 ? Math.min(16, 0.85 / maximum) : 1;
                    }
                    draw();
                } catch (error) {
                    if (serial !== waveformSerial) return;
                    label.textContent = `ALICE Lab: ${error.message}`;
                }
            }

            function scheduleVisibleWaveform(delay = 160) {
                clearTimeout(waveformTimer);
                waveformTimer = setTimeout(requestVisibleWaveform, delay);
                draw();
            }

            async function loadMedia(resetInputSelection = false) {
                // Ignore stale responses if another file is selected while the
                // metadata or waveform request is still in flight.
                const serial = ++loadSerial;
                const filename = isInputNode ? inputPayload?.filename : mediaWidget?.value;
                if (!filename) {
                    label.textContent = isInputNode
                        ? "Connect one AUDIO or VIDEO input, then Run"
                        : isPathNode ? "Enter an absolute media path" : "Select media";
                    return;
                }
                // Fully detach the previous resource. Merely removing `src`
                // leaves Chromium free to keep playing its decoded audio.
                rangePlayback = false;
                playButton.textContent = "▶";
                playButton.title = "Play A–B";
                playButton.setAttribute("aria-label", "Play A–B");
                for (const media of [video, audio]) {
                    media.pause();
                    media.removeAttribute("src");
                    media.load();
                }
                peaks = [];
                waveformStart = waveformEnd = 0;
                draw();
                label.textContent = "Loading media and waveform…";
                try {
                    const query = new URLSearchParams(
                        isInputNode ? { preview: filename } : isPathNode ? { path: filename } : { filename }
                    );
                    let info;
                    if (isInputNode) {
                        info = inputPayload;
                    } else {
                        const infoResponse = await api.fetchApi(`/alice_lab_audio_tools/media_info?${query}`);
                        info = await infoResponse.json();
                        if (!infoResponse.ok) throw new Error(info.error || "Media probe failed");
                    }
                    if (serial !== loadSerial) return;
                    duration = info.duration;
                    viewStart = 0;
                    viewEnd = duration;
                    // A range belongs to its source media. Reset it instead of
                    // carrying timestamps from the previously selected file.
                    selectedMarker = null;
                    activeMarker = null;
                    const matchingExecutedRange = !isInputNode
                        && executedRange?.source === filename
                        ? executedRange
                        : null;
                    updateWidget(
                        startWidget,
                        matchingExecutedRange
                            ? matchingExecutedRange.start
                            : isInputNode && !resetInputSelection ? Number(info.start) : 0
                    );
                    updateWidget(
                        endWidget,
                        matchingExecutedRange
                            ? matchingExecutedRange.end
                            : isInputNode && !resetInputSelection ? Number(info.end) : duration
                    );
                    sourceHasVideo = Boolean(info.has_video);
                    sourceHasAudio = info.has_audio !== false;
                    sourceDisplayName = isInputNode
                        ? `${sourceHasVideo ? "VIDEO" : "AUDIO"} input · ${formatTime(duration)}`
                        : filename;
                    const inputMediaQuery = new URLSearchParams({
                        filename,
                        subfolder: "",
                        type: "temp",
                        cache: Date.now().toString(),
                    });
                    if (info.has_video) {
                        video.style.display = "block";
                        audio.style.display = "none";
                        activeMedia = video;
                        activeMedia.preload = isInputNode ? "auto" : "metadata";
                        query.set("cache", Date.now().toString());
                        video.src = api.apiURL(
                            isInputNode
                                ? `/view?${inputMediaQuery}`
                                : `/alice_lab_audio_tools/preview?${query}`
                        );
                    } else {
                        video.style.display = "none";
                        audio.style.display = "block";
                        activeMedia = audio;
                        activeMedia.preload = isInputNode ? "auto" : "metadata";
                        query.set("cache", Date.now().toString());
                        // FFmpeg accepts considerably more audio encodings than
                        // Chromium/Safari media elements. Use a cached AAC proxy
                        // for UI playback while Run still reads the source file.
                        audio.src = api.apiURL(
                            isInputNode
                                ? `/view?${inputMediaQuery}`
                                : `/alice_lab_audio_tools/audio_preview?${query}`
                        );
                    }
                    // Force the media element to discard the old decoder and
                    // fetch the newly selected audio or video immediately.
                    activeMedia.load();
                    await requestVisibleWaveform(true);
                    if (serial !== loadSerial) return;
                    node.setSize([Math.max(node.size[0], 680), Math.max(node.size[1], info.has_video ? 610 : 380)]);
                    panelHeight = Math.max(panelHeight, info.has_video ? 500 : 285);
                    layoutToNodeHeight();
                    schedulePanelFit();
                } catch (error) {
                    label.textContent = `ALICE Lab: ${error.message}`;
                }
            }

            function pointerX(event) {
                const rect = canvas.getBoundingClientRect();
                return (event.clientX - rect.left) * canvas.clientWidth / Math.max(1, rect.width);
            }

            function pointerTime(event) {
                const plotWidth = Math.max(1, canvas.clientWidth - WAVEFORM_LEFT_PADDING - WAVEFORM_RIGHT_PADDING);
                const ratio = Math.max(0, Math.min(1, (pointerX(event) - WAVEFORM_LEFT_PADDING) / plotWidth));
                return Math.max(0, Math.min(duration, viewStart + ratio * (viewEnd - viewStart)));
            }

            function seekPreview(time) {
                // A metadata-only Input preview can remain stuck if a standalone
                // seek is issued before playback has requested media data. The
                // A-B play handler performs seek+play together, so defer this
                // visual-only seek until the browser has current frame data.
                if (isInputNode && activeMedia.readyState < 2) return;
                activeMedia.currentTime = time;
            }

            function updateWaveformCursor(event) {
                if (panning || movingSelection) {
                    canvas.style.cursor = "grabbing";
                    return;
                }
                if (!duration) {
                    canvas.style.cursor = "default";
                    return;
                }
                const x = pointerX(event);
                const span = Math.max(0.001, viewEnd - viewStart);
                const plotWidth = Math.max(1, canvas.clientWidth - WAVEFORM_LEFT_PADDING - WAVEFORM_RIGHT_PADDING);
                const startX = WAVEFORM_LEFT_PADDING + (startWidget.value - viewStart) / span * plotWidth;
                const endX = WAVEFORM_LEFT_PADDING + (endWidget.value - viewStart) / span * plotWidth;
                if (activeMarker || Math.abs(x - startX) <= 12 || Math.abs(x - endX) <= 12) {
                    canvas.style.cursor = "ew-resize";
                } else if (x > startX && x < endX) {
                    canvas.style.cursor = "pointer";
                } else {
                    canvas.style.cursor = "pointer";
                }
            }

            canvas.addEventListener("pointerdown", (event) => {
                if (!duration) return;
                // Right-drag pans a zoomed waveform. Left-drag edits a boundary
                // or moves the complete A-B selection.
                if (event.button === 2) {
                    panning = true;
                    panOriginX = event.clientX;
                    panOriginStart = viewStart;
                    canvas.setPointerCapture(event.pointerId);
                    canvas.style.cursor = "grabbing";
                    return;
                }
                const time = pointerTime(event);
                selectionDragCandidate = false;
                movingSelection = false;
                const x = pointerX(event);
                const span = Math.max(0.001, viewEnd - viewStart);
                const plotWidth = Math.max(1, canvas.clientWidth - WAVEFORM_LEFT_PADDING - WAVEFORM_RIGHT_PADDING);
                const startX = WAVEFORM_LEFT_PADDING + (startWidget.value - viewStart) / span * plotWidth;
                const endX = WAVEFORM_LEFT_PADDING + (endWidget.value - viewStart) / span * plotWidth;
                if (Math.abs(x - startX) <= 12) activeMarker = selectedMarker = "start";
                else if (Math.abs(x - endX) <= 12) activeMarker = selectedMarker = "end";
                else if (x > startX && x < endX) {
                    activeMarker = null;
                    selectedMarker = null;
                    selectionDragCandidate = true;
                    movingSelection = false;
                    selectionOriginTime = time;
                    selectionOriginStart = startWidget.value;
                    selectionOriginEnd = endWidget.value;
                    selectionOriginX = event.clientX;
                } else { activeMarker = null; selectedMarker = null; }
                pointerDown = time;
                markerMoved = false;
                canvas.setPointerCapture(event.pointerId);
                if (selectionDragCandidate) canvas.style.cursor = "pointer";
                else if (activeMarker) canvas.style.cursor = "ew-resize";
            });
            canvas.addEventListener("pointermove", (event) => {
                if (panning) {
                    const rect = canvas.getBoundingClientRect();
                    const span = viewEnd - viewStart;
                    const plotWidth = Math.max(1, canvas.clientWidth - WAVEFORM_LEFT_PADDING - WAVEFORM_RIGHT_PADDING);
                    const pointerDelta = (event.clientX - panOriginX) * canvas.clientWidth / Math.max(1, rect.width);
                    const delta = -pointerDelta / plotWidth * span;
                    const nextStart = Math.max(0, Math.min(duration - span, panOriginStart + delta));
                    viewStart = nextStart;
                    viewEnd = nextStart + span;
                    draw();
                    canvas.style.cursor = "grabbing";
                    return;
                }
                if (selectionDragCandidate || movingSelection) {
                    // Preserve click-to-seek: only turn a press inside A-B into
                    // a range drag after the pointer crosses this threshold.
                    if (!movingSelection && Math.abs(event.clientX - selectionOriginX) < 5) return;
                    movingSelection = true;
                    canvas.style.cursor = "grabbing";
                    const delta = pointerTime(event) - selectionOriginTime;
                    const selectionLength = selectionOriginEnd - selectionOriginStart;
                    const nextStart = Math.max(0, Math.min(duration - selectionLength, selectionOriginStart + delta));
                    updateWidget(startWidget, nextStart);
                    updateWidget(endWidget, nextStart + selectionLength);
                    markerMoved = true;
                    draw();
                    return;
                }
                if (!activeMarker) {
                    updateWaveformCursor(event);
                    return;
                }
                const time = pointerTime(event);
                markerMoved = true;
                if (activeMarker === "start") updateWidget(startWidget, Math.min(time, endWidget.value - 0.001));
                else updateWidget(endWidget, Math.max(time, startWidget.value + 0.001));
                draw();
            });
            canvas.addEventListener("pointerup", (event) => {
                if (panning) {
                    panning = false;
                    canvas.releasePointerCapture(event.pointerId);
                    scheduleVisibleWaveform(0);
                    updateWaveformCursor(event);
                    return;
                }
                if (movingSelection) {
                    movingSelection = false;
                    selectionDragCandidate = false;
                    seekPreview(startWidget.value);
                } else if (selectionDragCandidate) {
                    selectionDragCandidate = false;
                    seekPreview(pointerTime(event));
                } else if (activeMarker && markerMoved) {
                    seekPreview(activeMarker === "start" ? startWidget.value : endWidget.value);
                } else if (!activeMarker && pointerDown !== null) {
                    seekPreview(pointerTime(event));
                }
                activeMarker = null;
                pointerDown = null;
                canvas.releasePointerCapture(event.pointerId);
                draw();
                updateWaveformCursor(event);
            });
            canvas.addEventListener("pointerleave", () => {
                if (pointerDown === null && !activeMarker && !panning && !movingSelection) canvas.style.cursor = "pointer";
            });
            canvas.addEventListener("contextmenu", (event) => event.preventDefault());
            canvas.addEventListener("wheel", (event) => {
                if (!duration) return;
                event.preventDefault();
                // Zoom around the pointer, keeping the inspected moment under
                // the cursor instead of pulling it toward the center.
                const oldSpan = viewEnd - viewStart;
                const factor = event.deltaY < 0 ? 0.75 : 1.333333;
                const newSpan = Math.max(0.1, Math.min(duration, oldSpan * factor));
                const anchor = pointerTime(event);
                const ratio = (anchor - viewStart) / oldSpan;
                let nextStart = anchor - ratio * newSpan;
                nextStart = Math.max(0, Math.min(duration - newSpan, nextStart));
                viewStart = nextStart;
                viewEnd = nextStart + newSpan;
                scheduleVisibleWaveform();
            }, { passive: false });
            for (const input of [startInput, endInput]) {
                input.addEventListener("keydown", (event) => event.stopPropagation());
            }
            function commitTime(input, target, other, isStart) {
                const parsed = parseTime(input.value);
                if (parsed === null) { syncLabel(); return; }
                const value = isStart
                    ? Math.max(0, Math.min(other.value - 0.001, parsed))
                    : Math.max(other.value + 0.001, Math.min(duration, parsed));
                updateWidget(target, value);
                draw();
            }
            startInput.addEventListener("change", () => commitTime(startInput, startWidget, endWidget, true));
            endInput.addEventListener("change", () => commitTime(endInput, endWidget, startWidget, false));
            function nudge(marker, delta) {
                selectedMarker = marker;
                if (marker === "start") updateWidget(startWidget, Math.max(0, Math.min(endWidget.value - 0.001, startWidget.value + delta)));
                else updateWidget(endWidget, Math.max(startWidget.value + 0.001, Math.min(duration, endWidget.value + delta)));
                draw();
            }
            function waitForSeek(media, target) {
                if (!media.seeking && Math.abs(media.currentTime - target) <= 0.002) {
                    return Promise.resolve();
                }
                return new Promise((resolve) => {
                    let settled = false;
                    let timeout = null;
                    const finish = () => {
                        if (settled) return;
                        settled = true;
                        media.removeEventListener("seeked", finish);
                        clearTimeout(timeout);
                        resolve();
                    };
                    media.addEventListener("seeked", finish);
                    // Some browser/media combinations omit seeked for a tiny
                    // time change. Do not leave the button permanently waiting.
                    timeout = setTimeout(finish, 1000);
                });
            }
            function showRangePlayButton() {
                playButton.textContent = "▶";
                playButton.title = "Play A–B";
                playButton.setAttribute("aria-label", "Play A–B");
            }
            function showRangeStopButton() {
                playButton.textContent = "■";
                playButton.title = "Stop A–B playback";
                playButton.setAttribute("aria-label", "Stop A–B playback");
            }
            function stopRangePlayback(media, seekToEnd = false) {
                rangePlayback = false;
                media.pause();
                if (seekToEnd) {
                    media.currentTime = Math.min(endWidget.value, media.duration || endWidget.value);
                }
                showRangePlayButton();
            }
            function handleRangeEnd(media) {
                if (!rangePlayback) return;
                if (loopPlayback) {
                    // Clear the ended/B state before requesting playback again.
                    // Assigning currentTime first is important for sparse-GOP
                    // Upload previews whose backward seek can be asynchronous.
                    rangePlayback = false;
                    media.currentTime = startWidget.value;
                    rangePlayback = true;
                    const playback = media.play();
                    playback?.catch((error) => {
                        stopRangePlayback(media);
                        label.textContent = `ALICE Lab: playback failed (${error.message})`;
                    });
                } else {
                    stopRangePlayback(media, true);
                }
            }
            startMinus.addEventListener("click", () => nudge("start", -0.01));
            startPlus.addEventListener("click", () => nudge("start", 0.01));
            endMinus.addEventListener("click", () => nudge("end", -0.01));
            endPlus.addEventListener("click", () => nudge("end", 0.01));
            playButton.addEventListener("click", async () => {
                if (rangePlayback) {
                    stopRangePlayback(activeMedia);
                } else {
                    try {
                        if (!activeMedia.currentSrc && !activeMedia.src) {
                            throw new Error("preview is not loaded");
                        }
                        // Stop at B before rewinding. Starting play while still
                        // at B can leave sparse-GOP MP4 previews paused/ended
                        // when the asynchronous backward seek reaches A.
                        rangePlayback = false;
                        activeMedia.pause();
                        const start = startWidget.value;
                        const seekReady = waitForSeek(activeMedia, start);
                        activeMedia.currentTime = start;
                        // Call play synchronously from the click gesture, but
                        // only after currentTime no longer points at B/ended.
                        rangePlayback = true;
                        showRangeStopButton();
                        const playbackReady = activeMedia.play();
                        await Promise.all([seekReady, playbackReady]);
                    } catch (error) {
                        stopRangePlayback(activeMedia);
                        label.textContent = `ALICE Lab: playback failed (${error.message})`;
                    }
                }
            });
            loopButton.addEventListener("click", () => {
                loopPlayback = !loopPlayback;
                loopButton.title = loopPlayback ? "Loop: ON" : "Loop: OFF";
                loopButton.setAttribute("aria-label", loopButton.title);
                loopButton.style.color = loopPlayback ? "#79c8f2" : "";
                loopButton.style.background = loopPlayback ? "#274657" : "";
            });
            zoomButton.addEventListener("click", () => {
                if (!duration) return;
                const selection = endWidget.value - startWidget.value;
                const padding = Math.max(0.02, selection * 0.08);
                viewStart = Math.max(0, startWidget.value - padding);
                viewEnd = Math.min(duration, endWidget.value + padding);
                scheduleVisibleWaveform(0);
            });
            allButton.addEventListener("click", () => {
                viewStart = 0;
                viewEnd = duration;
                scheduleVisibleWaveform(0);
            });
            scaleInput.addEventListener("input", () => {
                waveformScale = Number(scaleInput.value);
                draw();
            });
            addButton.addEventListener("click", () => {
                if (isInputNode) return;
                if (isPathNode) loadMedia();
                else fileInput.click();
            });
            fileInput.addEventListener("change", async () => {
                if (isPathNode || isInputNode) return;
                const file = fileInput.files?.[0];
                if (!file) return;
                addButton.disabled = true;
                label.textContent = `Uploading ${file.name}…`;
                try {
                    const configResponse = await api.fetchApi("/alice_lab_audio_tools/config");
                    const config = configResponse.ok ? await configResponse.json() : {};
                    const limitMb = Number(config.max_upload_size_mb) || 100;
                    const fileMb = file.size / 1024 / 1024;
                    if (fileMb > limitMb) {
                        throw new Error(
                            `File is ${fileMb.toFixed(1)} MB, above ComfyUI's ${limitMb.toFixed(1)} MB upload limit. ` +
                            "Use ALICE Media Range (Path) to open it without copying."
                        );
                    }
                    // The standard ComfyUI upload endpoint creates a portable
                    // input-relative path that also passes backend validation.
                    const body = new FormData();
                    body.append("image", file, file.name);
                    body.append("type", "input");
                    body.append("overwrite", "false");
                    const response = await api.fetchApi("/upload/image", { method: "POST", body });
                    const contentType = response.headers.get("content-type") || "";
                    const result = contentType.includes("application/json")
                        ? await response.json()
                        : { error: (await response.text()).trim() };
                    if (!response.ok) {
                        if (response.status === 413) {
                            throw new Error(
                                `File is too large to upload (${(file.size / 1024 / 1024).toFixed(1)} MB). ` +
                                "Copy it into ComfyUI/input, or start ComfyUI with --max-upload-size set above the file size."
                            );
                        }
                        throw new Error(result.error || `Upload failed (HTTP ${response.status})`);
                    }
                    const uploaded = result.subfolder ? `${result.subfolder}/${result.name}` : result.name;
                    const values = mediaWidget.options?.values;
                    if (Array.isArray(values) && !values.includes(uploaded)) values.push(uploaded);
                    mediaWidget.value = uploaded;
                    mediaWidget.callback?.(uploaded);
                } catch (error) {
                    const message = `ALICE Lab: ${error.message}`;
                    label.textContent = message;
                    window.alert(message);
                } finally {
                    addButton.disabled = false;
                    fileInput.value = "";
                }
            });
            for (const media of [video, audio]) {
                media.addEventListener("loadedmetadata", () => {
                    if (media !== activeMedia) return;
                    // Keep the yellow playhead at A after every preview refresh.
                    seekPreview(Math.min(startWidget.value, media.duration || duration));
                    draw();
                });
                media.addEventListener("error", () => {
                    if (media !== activeMedia) return;
                    label.textContent = "ALICE Lab: preview could not be loaded";
                });
                media.addEventListener("timeupdate", () => {
                    // Native media playback has no A-B boundary, so enforce it
                    // only while the dedicated A-B playback mode is active.
                    if (media === activeMedia && rangePlayback && media.currentTime >= endWidget.value) {
                        handleRangeEnd(media);
                    }
                    if (media === activeMedia) draw();
                });
                media.addEventListener("ended", () => {
                    // Some browser/media combinations can reach ended without
                    // delivering a final timeupdate at the exact B timestamp.
                    if (media === activeMedia) handleRangeEnd(media);
                });
                media.addEventListener("seeked", () => {
                    if (media === activeMedia) draw();
                });
            }
            if (mediaWidget) chainCallback(mediaWidget, "callback", loadMedia);
            chainCallback(startWidget, "callback", draw);
            chainCallback(endWidget, "callback", draw);
            if (!isInputNode) {
                chainCallback(node, "onExecuted", function (message) {
                    try {
                        const raw = message?.alice_lab_media_range?.[0]
                            ?? message?.alice_lab_media_range;
                        if (raw === undefined) return;
                        const payload = typeof raw === "string" ? JSON.parse(raw) : raw;
                        const start = Number(payload?.start);
                        const end = Number(payload?.end);
                        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
                            throw new Error("Executed media range is invalid");
                        }
                        executedRange = {
                            source: String(payload?.source ?? ""),
                            start,
                            end,
                        };
                        if (executedRange.source !== String(mediaWidget?.value ?? "")) return;
                        updateWidget(startWidget, start);
                        updateWidget(endWidget, end);
                        selectedMarker = null;
                        activeMarker = null;
                        if (duration > 0) seekPreview(Math.min(start, duration));
                        draw();
                    } catch (error) {
                        label.textContent = `ALICE Lab: ${error.message}`;
                    }
                });
            }
            if (isInputNode) {
                chainCallback(node, "onExecuted", function (message) {
                    try {
                        const raw = message?.alice_lab_media_range_input?.[0]
                            ?? message?.alice_lab_media_range_input;
                        inputPayload = typeof raw === "string" ? JSON.parse(raw) : raw;
                        if (!inputPayload?.filename) throw new Error("Input preview data is missing");
                        const resetInputSelection = !inputHasLoaded;
                        inputHasLoaded = true;
                        loadMedia(resetInputSelection);
                    } catch (error) {
                        label.textContent = `ALICE Lab: ${error.message}`;
                    }
                });
                setTimeout(() => {
                    label.textContent = "Connect one AUDIO or VIDEO input, then Run";
                    syncLabel();
                }, 0);
            } else {
                setTimeout(loadMedia, 0);
            }
            new ResizeObserver(() => scheduleVisibleWaveform()).observe(canvas);
        });
    },
});

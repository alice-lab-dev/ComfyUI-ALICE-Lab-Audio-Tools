import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { drawTimeAxis } from "./time_axis.js";

const WAVE_LEFT = 48;
const PLOT_RIGHT = 45;
const TIME_BOTTOM = 26;

function chainCallback(target, key, callback) {
    const original = target[key];
    target[key] = function (...args) {
        const result = original?.apply(this, args);
        callback.apply(this, args);
        return result;
    };
}

function formatTime(seconds) {
    const value = Math.max(0, Number(seconds) || 0);
    const minutes = Math.floor(value / 60);
    return `${String(minutes).padStart(2, "0")}:${(value % 60).toFixed(3).padStart(6, "0")}`;
}

function parseTime(text) {
    const parts = String(text).trim().split(":");
    if (!parts.length || parts.length > 3) return null;
    const values = parts.map(Number);
    if (values.some((value) => !Number.isFinite(value) || value < 0)) return null;
    return values.reduce((total, value) => total * 60 + value, 0);
}

function button(text) {
    const element = document.createElement("button");
    element.textContent = text;
    return element;
}

function select(options) {
    const element = document.createElement("select");
    for (const [value, label] of options) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        element.append(option);
    }
    return element;
}

function labelled(label, control, title = "") {
    const wrapper = document.createElement("label");
    wrapper.style.cssText = "display:flex;align-items:center;gap:4px;white-space:nowrap;color:#aeb9c5";
    wrapper.textContent = label;
    wrapper.title = title;
    wrapper.append(control);
    return wrapper;
}

app.registerExtension({
    name: "ALICE_Lab.AudioCompare",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "AliceLabCompareAudio") return;

        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            const node = this;
            const minimumPanelHeight = 594;
            let data = null;
            let detail = null;
            let viewStart = 0;
            let viewEnd = 0;
            let selectionStart = 0;
            let selectionEnd = 0;
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
            let analysisSerial = 0;
            let analysisTimer = null;
            let playbackStart = 0;
            let playbackEnd = 0;
            let inspectionTime = null;
            let playing = false;
            let loopPlayback = false;
            let fixedDisplay = false;

            const root = document.createElement("div");
            root.style.cssText = `height:${minimumPanelHeight}px;margin-top:44px;display:flex;flex-direction:column;gap:6px;padding:6px;box-sizing:border-box;background:#15191f;color:#dce3ea;font:12px sans-serif;overflow:hidden`;

            const heading = document.createElement("div");
            heading.style.cssText = "display:flex;align-items:center;gap:10px;padding:6px 8px;background:#242830;border:1px solid #39424e;border-radius:6px;flex:none";
            const title = document.createElement("strong");
            title.textContent = "Interactive audio analysis";
            const summary = document.createElement("span");
            summary.textContent = "Run to compare audio";
            // Reserve a stable status area so changing run/selection text does
            // not move the heading or alter the panel layout.
            summary.style.cssText = "margin-left:auto;width:300px;max-width:45%;color:#9eabb8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right";
            heading.append(title, summary);

            const toolbar = document.createElement("div");
            toolbar.style.cssText = "display:flex;align-items:center;flex-wrap:wrap;gap:6px;padding:5px;background:#20252c;border:1px solid #39424e;border-radius:5px;flex:none";
            const playButton = button("▶");
            playButton.title = "Play A–B";
            playButton.setAttribute("aria-label", "Play A–B");
            playButton.style.cssText = "display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;width:34px;min-width:34px;height:22px;padding:0;font:12px/1 sans-serif;text-align:center;overflow:hidden";
            const playbackSelect = select([["overlay", "1+2 overlay"], ["a", "Audio 1 only"], ["b", "Audio 2 only"], ["difference", "1−2 difference"]]);
            const viewSelect = select([["aligned", "Aligned"], ["raw", "Before alignment"]]);
            const displaySelect = select([["overlay", "1+2 overlay"], ["stacked", "All (stacked)"], ["a", "Audio 1 only"], ["b", "Audio 2 only"], ["difference", "1−2 difference"]]);
            const fixedDisplayButton = button("◆");
            fixedDisplayButton.title = "Fixed display: OFF";
            fixedDisplayButton.setAttribute("aria-label", "Fixed display: OFF");
            fixedDisplayButton.style.cssText = "display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;width:34px;min-width:34px;height:22px;padding:0;font:12px/1 sans-serif;text-align:center;overflow:hidden";
            const zoomSelection = button("Zoom A–B");
            const showAll = button("Show all");
            const selectAll = button("Select all");
            const loopButton = button("↻");
            loopButton.title = "Loop: OFF";
            loopButton.setAttribute("aria-label", "Loop: OFF");
            loopButton.style.cssText = "display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;width:34px;min-width:34px;height:22px;padding:0;font:12px/1 sans-serif;text-align:center;overflow:hidden";
            const resolution = document.createElement("span");
            // overview, analysing, and detailed point counts have very
            // different text widths. Keep their slot fixed to prevent toolbar
            // wrapping and the canvases below it from bouncing vertically.
            resolution.style.cssText = "margin-left:auto;flex:0 0 260px;width:260px;color:#8fa0b1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right";
            resolution.textContent = "overview";
            toolbar.append(
                labelled("Playback:", playbackSelect, "Wave display follows this selection unless Fixed display is ON."),
                labelled("Alignment view:", viewSelect, "Switch between the original timing and automatic alignment."),
                fixedDisplayButton,
                labelled("Wave display:", displaySelect, "Available when Fixed display is ON; changes only the waveform display."),
                
                resolution,
            );

            const rangeControls = document.createElement("div");
            rangeControls.style.cssText = "display:flex;align-items:center;flex-wrap:wrap;gap:6px;padding:5px;background:#20252c;border:1px solid #39424e;border-radius:5px;flex:none";
            const inputStyle = "width:92px;padding:3px 5px;background:#171a20;color:#e7edf3;border:1px solid #48515e;border-radius:4px;font:11px monospace";
            const startInput = document.createElement("input");
            const endInput = document.createElement("input");
            startInput.style.cssText = inputStyle;
            endInput.style.cssText = inputStyle;
            const startMinus = button("A−");
            const startPlus = button("A+");
            const endMinus = button("B−");
            const endPlus = button("B+");
            rangeControls.append(
                playButton, document.createTextNode("A"), startInput, startMinus, startPlus,
                document.createTextNode("B"), endInput, endMinus, endPlus,
                loopButton, zoomSelection, showAll, selectAll,
            );

            const metrics = document.createElement("div");
            metrics.style.cssText = "display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;flex:none";
            const metricDefinitions = [
                ["Similarity", "Overall result for the complete Run. With Auto Align enabled: 65% Alignment + 35% Waveform. With Auto Align disabled: Similarity equals Waveform."],
                ["Alignment", "Amplitude-envelope match at the detected delay for the complete Run. This value stays fixed while zooming."],
                ["Waveform", "Absolute waveform correlation after optional alignment for the complete Run. Polarity inversion is treated as a match."],
                ["Delay (s)", "Signed time applied to Audio 2 during the complete Run. A negative value advances Audio 2."],
                ["Visible Waveform", "Absolute waveform correlation for the currently visible interval. This value changes with zoom and pan."],
            ];
            const metricElements = metricDefinitions.map(([name, description]) => {
                const item = document.createElement("div");
                item.style.cssText = "padding:5px 6px;background:#1d2229;border:1px solid #343c46;border-radius:4px;min-width:0";
                item.title = description;
                const label = document.createElement("div");
                label.textContent = name;
                label.style.cssText = "font-size:10px;color:#8f9ba8;white-space:nowrap";
                const value = document.createElement("strong");
                value.textContent = "—";
                value.style.cssText = "display:block;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis";
                item.append(label, value);
                metrics.append(item);
                return value;
            });

            const waveformLabel = document.createElement("div");
            waveformLabel.style.cssText = "display:flex;gap:12px;font-weight:600;flex:none";
            waveformLabel.innerHTML = '<span style="color:#67c5e8">Audio 1</span><span style="color:#ff7474">Audio 2</span><span style="color:#ffd166">1 − 2</span><span style="margin-left:auto;color:#8fa0b1;font-weight:400">Media Range controls · Wheel: zoom · Right-drag: pan · Drag edge/range: edit</span>';
            const waveform = document.createElement("canvas");
            waveform.tabIndex = 0;
            waveform.style.cssText = "width:100%;min-height:130px;flex:1 1 auto;background:#101419;border:1px solid #39424e;box-sizing:border-box;cursor:pointer;touch-action:none";
            root.append(heading, rangeControls, toolbar, metrics, waveformLabel, waveform);

            const audioA = document.createElement("audio");
            const audioB = document.createElement("audio");
            for (const media of [audioA, audioB]) media.preload = "auto";

            function currentDuration() {
                if (!data) return 0;
                return viewSelect.value === "raw" ? Number(data.raw_duration) : Number(data.duration);
            }

            function minimumViewSpan() {
                // Keep enough samples for meaningful min/max and STFT data.
                return Math.max(64 / Number(data?.sample_rate || 44100), 0.002);
            }

            function setView(start, end) {
                const duration = currentDuration();
                if (!duration) { viewStart = viewEnd = 0; return; }
                const span = Math.max(minimumViewSpan(), Math.min(duration, Number(end) - Number(start)));
                viewStart = Math.max(0, Math.min(duration - span, Number(start) || 0));
                viewEnd = Math.min(duration, viewStart + span);
            }

            function setSelection(start, end) {
                const duration = currentDuration();
                const minimum = 1 / Number(data?.sample_rate || 44100);
                selectionStart = Math.max(0, Math.min(duration, Number(start) || 0));
                selectionEnd = Math.max(selectionStart + minimum, Math.min(duration, Number(end) || 0));
                if (selectionEnd > duration) {
                    selectionEnd = duration;
                    selectionStart = Math.max(0, selectionEnd - minimum);
                }
                syncSelectionControls();
            }

            function syncSelectionControls() {
                if (document.activeElement !== startInput) startInput.value = formatTime(selectionStart);
                if (document.activeElement !== endInput) endInput.value = formatTime(selectionEnd);
            }

            function currentPlaybackTime() {
                if (!playing) return inspectionTime;
                const master = playbackSelect.value === "b" ? audioB : audioA;
                return Math.min(playbackEnd, playbackStart + (Number(master.currentTime) || 0));
            }

            function effectiveWaveDisplay() {
                if (fixedDisplay) return displaySelect.value;
                return playbackSelect.value;
            }

            function syncDisplayControls() {
                displaySelect.disabled = !fixedDisplay;
                
                displaySelect.style.opacity = fixedDisplay ? "1" : "0.55";
                fixedDisplayButton.title = `Fixed display: ${fixedDisplay ? "ON" : "OFF"}`;
                fixedDisplayButton.setAttribute("aria-label", fixedDisplayButton.title);
                fixedDisplayButton.setAttribute("aria-pressed", String(fixedDisplay));
                fixedDisplayButton.style.color = fixedDisplay ? "#79c8f2" : "";
                fixedDisplayButton.style.background = fixedDisplay ? "#274657" : "";
                if (!fixedDisplay) {
                    displaySelect.value = playbackSelect.value;
                }
            }

            function prepareCanvas(canvas, background = "#101419") {
                // DOM widgets can be CSS-transformed with the ComfyUI canvas.
                // Draw in the untransformed CSS coordinate space so a redraw
                // does not apply the node zoom to the bitmap a second time.
                const rect = {
                    width: canvas.clientWidth,
                    height: canvas.clientHeight,
                };
                const ratio = window.devicePixelRatio || 1;
                canvas.width = Math.max(1, Math.round(rect.width * ratio));
                canvas.height = Math.max(1, Math.round(rect.height * ratio));
                const context = canvas.getContext("2d");
                context.setTransform(ratio, 0, 0, ratio, 0, 0);
                context.fillStyle = background;
                context.fillRect(0, 0, rect.width, rect.height);
                return { context, rect };
            }

            function plotRect(rect, left = WAVE_LEFT) {
                return {
                    left,
                    top: 8,
                    right: Math.max(left + 1, rect.width - PLOT_RIGHT),
                    bottom: Math.max(9, rect.height - TIME_BOTTOM),
                    get width() { return this.right - this.left; },
                    get height() { return this.bottom - this.top; },
                };
            }

            function drawWaveformAxis(context, rect, plot) {
                context.font = "10px sans-serif";
                context.textAlign = "right";
                context.textBaseline = "middle";
                for (const [value, label] of [[1, "+1.0"], [0.5, "+0.5"], [0, "0 FS"], [-0.5, "−0.5"], [-1, "−1.0"]]) {
                    const y = plot.top + (1 - (value + 1) / 2) * plot.height;
                    context.strokeStyle = value === 0 ? "#48515c" : "#29313a";
                    context.beginPath(); context.moveTo(plot.left, y); context.lineTo(plot.right, y); context.stroke();
                    context.fillStyle = "#8795a3";
                    context.fillText(label, plot.left - 5, y);
                }
                context.textAlign = "left";
                context.textBaseline = "alphabetic";
            }

            function stackedPlots(plot) {
                const laneHeight = plot.height / 3;
                return Array.from({ length: 3 }, (_, index) => ({
                    left: plot.left,
                    right: plot.right,
                    top: plot.top + laneHeight * index,
                    bottom: plot.top + laneHeight * (index + 1),
                    width: plot.width,
                    height: laneHeight,
                }));
            }

            function drawStackedAxes(context, plot, lanes) {
                const definitions = [
                    ["Audio 1", "#67c5e8"],
                    ["Audio 2", "#ff7474"],
                    ["1 − 2", "#ffd166"],
                ];
                lanes.forEach((lane, index) => {
                    context.strokeStyle = "#303844";
                    context.lineWidth = 1;
                    context.beginPath();
                    context.moveTo(plot.left, lane.top + lane.height / 2);
                    context.lineTo(plot.right, lane.top + lane.height / 2);
                    context.stroke();
                    if (index > 0) {
                        context.strokeStyle = "#48515c";
                        context.beginPath();
                        context.moveTo(plot.left, lane.top);
                        context.lineTo(plot.right, lane.top);
                        context.stroke();
                    }
                    context.fillStyle = definitions[index][1];
                    context.font = "bold 9px sans-serif";
                    context.textAlign = "right";
                    context.fillText(definitions[index][0], plot.left - 5, lane.top + 11);
                });
                context.textAlign = "left";
            }


            function drawEnvelope(context, plot, envelope, color, alpha = 0.8, sourceStart = 0, sourceEnd = null) {
                if (!envelope?.length) return;
                const rangeStart = Number(sourceStart) || 0;
                const rangeEnd = Number(sourceEnd ?? currentDuration());
                const visibleStart = Math.max(viewStart, rangeStart);
                const visibleEnd = Math.min(viewEnd, rangeEnd);
                if (visibleEnd <= visibleStart || rangeEnd <= rangeStart) return;
                context.strokeStyle = color;
                context.globalAlpha = alpha;
                context.lineWidth = 1;
                // At sample-level zoom a continuous trace is clearer than
                // repeating a small number of min/max bars across the canvas.
                if (envelope.length < plot.width * 0.8) {
                    context.beginPath();
                    let started = false;
                    envelope.forEach((value, index) => {
                        const time = rangeStart + index / Math.max(1, envelope.length - 1) * (rangeEnd - rangeStart);
                        if (time < viewStart || time > viewEnd) return;
                        const pair = Array.isArray(value) ? value : [-Number(value || 0), Number(value || 0)];
                        const sample = (Number(pair[0]) + Number(pair[1])) / 2;
                        const x = timeX(time, plot);
                        const y = plot.top + plot.height / 2 - Math.max(-1, Math.min(1, sample)) * plot.height * 0.46;
                        if (!started) { context.moveTo(x, y); started = true; }
                        else context.lineTo(x, y);
                    });
                    context.stroke();
                    context.globalAlpha = 1;
                    return;
                }
                context.beginPath();
                const left = Math.max(plot.left, Math.floor(timeX(visibleStart, plot)));
                const right = Math.min(plot.right, Math.ceil(timeX(visibleEnd, plot)));
                for (let x = left; x < right; x++) {
                    const time = viewStart + (x - plot.left) / Math.max(1, plot.width) * (viewEnd - viewStart);
                    const sourceRatio = (time - rangeStart) / (rangeEnd - rangeStart);
                    const value = envelope[Math.max(0, Math.min(envelope.length - 1, Math.floor(sourceRatio * envelope.length)))];
                    const pair = Array.isArray(value) ? value : [-Number(value || 0), Number(value || 0)];
                    const y1 = plot.top + plot.height / 2 - Math.max(-1, Math.min(1, pair[1])) * plot.height * 0.46;
                    const y2 = plot.top + plot.height / 2 - Math.max(-1, Math.min(1, pair[0])) * plot.height * 0.46;
                    context.moveTo(x + 0.5, y1);
                    context.lineTo(x + 0.5, y2);
                }
                context.stroke();
                context.globalAlpha = 1;
            }

            function timeX(time, plot) {
                return plot.left + (time - viewStart) / Math.max(1e-9, viewEnd - viewStart) * plot.width;
            }

            function drawTimeOverlay(context, rect, plot) {
                if (!data || viewEnd <= viewStart) return;
                const left = Math.max(plot.left, Math.min(plot.right, timeX(selectionStart, plot)));
                const right = Math.max(plot.left, Math.min(plot.right, timeX(selectionEnd, plot)));
                if (right > left) {
                    context.fillStyle = "rgba(85,189,232,.14)";
                    context.fillRect(left, plot.top, right - left, plot.height);
                    context.strokeStyle = "#55bde8";
                    context.strokeRect(left + 0.5, plot.top + 0.5, Math.max(0, right - left - 1), plot.height - 1);
                }
                for (const [time, color, marker, markerKey] of [[selectionStart, "#42d392", "A", "start"], [selectionEnd, "#ff6b6b", "B", "end"]]) {
                    const x = timeX(time, plot);
                    if (x < plot.left || x > plot.right) continue;
                    context.strokeStyle = color;
                    context.lineWidth = selectedMarker === markerKey ? 4 : 2;
                    context.beginPath(); context.moveTo(x, plot.top); context.lineTo(x, plot.bottom); context.stroke();
                    context.fillStyle = color; context.font = "bold 10px sans-serif";
                    const markerWidth = context.measureText(marker).width;
                    context.fillText(marker, marker === "B" ? x - markerWidth - 3 : x + 3, plot.top + 11);
                    context.fillRect(x - 4, plot.top + plot.height / 2 - 12, 8, 24);
                }
                const playhead = currentPlaybackTime();
                if (playhead !== null) {
                    const x = timeX(playhead, plot);
                    if (x >= plot.left && x <= plot.right) {
                        // Match Media Range: yellow always means the current
                        // inspection/playback position, independent of whether
                        // A, B, or both are being monitored.
                        context.strokeStyle = "#ffd166";
                        context.lineWidth = 1;
                        context.beginPath(); context.moveTo(x, plot.top); context.lineTo(x, plot.bottom); context.stroke();
                    }
                }
                context.font = "10px monospace";
                context.textBaseline = "middle";
                for (const [time, color, side] of [[selectionStart, "#42d392", "start"], [selectionEnd, "#ff6b6b", "end"]]) {
                    const markerX = Math.max(plot.left, Math.min(plot.right, timeX(time, plot)));
                    const text = formatTime(time);
                    const width = context.measureText(text).width + 8;
                    const preferred = side === "start" ? markerX : markerX - width;
                    const x = Math.max(plot.left, Math.min(plot.right - width, preferred));
                    const y = plot.bottom + 4;
                    context.fillStyle = color;
                    context.fillRect(x, y, width, 17);
                    context.fillStyle = "#101419";
                    context.fillText(text, x + 4, y + 8.5);
                }
                context.textBaseline = "alphabetic";
                syncSelectionControls();
            }

            function drawWaveform() {
                const view = prepareCanvas(waveform);
                const plot = plotRect(view.rect);
                const waveDisplay = effectiveWaveDisplay();
                const lanes = waveDisplay === "stacked" ? stackedPlots(plot) : null;
                if (lanes) drawStackedAxes(view.context, plot, lanes);
                else drawWaveformAxis(view.context, view.rect, plot);
                view.context.fillStyle = "rgba(5,8,12,.75)";
                view.context.fillRect(plot.left, view.rect.height - TIME_BOTTOM, plot.width, TIME_BOTTOM);
                if (data && viewEnd > viewStart) {
                    drawTimeAxis(view.context, {
                        left: plot.left,
                        right: plot.right,
                        top: plot.top,
                        bottom: plot.bottom,
                        start: viewStart,
                        end: viewEnd,
                        labelY: view.rect.height - 2,
                    });
                }
                // Do not briefly show the coarse Run-result envelope before
                // the viewport analysis arrives. Otherwise the asynchronous
                // replacement can coincide with Play and look playback-driven.
                const source = detail || (!data?.analysis_id ? data : null);
                const drawSource = (waveSource, sourceStart, sourceEnd, opacityScale = 1) => {
                    if (waveDisplay === "stacked") {
                        drawEnvelope(view.context, lanes[0], waveSource.overlay_a, "#67c5e8", 0.95 * opacityScale, sourceStart, sourceEnd);
                        drawEnvelope(view.context, lanes[1], waveSource.overlay_b, "#ff7474", 0.95 * opacityScale, sourceStart, sourceEnd);
                        drawEnvelope(view.context, lanes[2], waveSource.difference, "#ffd166", 0.95 * opacityScale, sourceStart, sourceEnd);
                        return;
                    }
                    if (["overlay", "a"].includes(waveDisplay)) drawEnvelope(view.context, plot, waveSource.overlay_a, "#67c5e8", (waveDisplay === "overlay" ? 0.72 : 0.95) * opacityScale, sourceStart, sourceEnd);
                    if (["overlay", "b"].includes(waveDisplay)) drawEnvelope(view.context, plot, waveSource.overlay_b, "#ff7474", (waveDisplay === "overlay" ? 0.62 : 0.95) * opacityScale, sourceStart, sourceEnd);
                    if (waveDisplay === "difference") drawEnvelope(view.context, plot, waveSource.difference, "#ffd166", 0.95 * opacityScale, sourceStart, sourceEnd);
                };
                if (source) {
                    // Keep the full aligned overview underneath the detailed
                    // viewport. During a right-drag this makes the entire
                    // waveform move immediately; the shifted high-resolution
                    // patch is replaced when range reanalysis completes.
                    const detailCoversView = detail
                        && Number(detail.start) <= viewStart + 1e-6
                        && Number(detail.end) >= viewEnd - 1e-6;
                    if (detail && data && viewSelect.value === "aligned" && !detailCoversView) {
                        drawSource(data, 0, currentDuration(), 0.28);
                    }
                    const sourceStart = source === detail ? Number(detail.start) : 0;
                    const sourceEnd = source === detail ? Number(detail.end) : currentDuration();
                    drawSource(source, sourceStart, sourceEnd);
                }
                drawTimeOverlay(view.context, view.rect, plot);
            }

            function draw() {
                drawWaveform();
            }

            function updateGlobalMetrics(source) {
                if (!source) return;
                const percent = (value, precision = 1) => `${(100 * Number(value || 0)).toFixed(precision)}%`;
                metricElements[0].textContent = percent(source.similarity, 6);
                metricElements[1].textContent = data?.auto_align === false
                    ? "OFF"
                    : percent(source.alignment_score, 6);
                metricElements[2].textContent = percent(source.waveform_similarity, 6);
                metricElements[3].textContent = `${Number(data?.delay_seconds || 0).toFixed(4)}s`;
                metricElements[0].title = `similarity output = ${Number(data?.similarity || 0).toFixed(9)}${data?.auto_align === false ? " · Auto Align OFF: Similarity = Waveform" : ""}`;
                metricElements[1].title = data?.auto_align === false
                    ? "Auto Align is OFF."
                    : `alignment score = ${Number(data?.alignment_score || 0).toFixed(9)}`;
                metricElements[2].title = `waveform correlation magnitude = ${Number(data?.waveform_similarity || 0).toFixed(9)}`;
                metricElements[3].title = `audio_2_delay_seconds output = ${Number(data?.delay_seconds || 0).toFixed(9)}`;
                metricElements[4].textContent = percent(source.waveform_similarity, 6);
                metricElements[4].title = `visible waveform correlation magnitude = ${Number(source.waveform_similarity || 0).toFixed(9)}`;
            }

            function updateVisibleMetric(source) {
                if (!source) return;
                metricElements[4].textContent = `${(100 * Number(source.waveform_similarity || 0)).toFixed(6)}%`;
                metricElements[4].title = `visible waveform correlation magnitude = ${Number(source.waveform_similarity || 0).toFixed(9)} · ${formatTime(source.start)}–${formatTime(source.end)}`;
            }

            async function requestDetail() {
                clearTimeout(analysisTimer);
                if (!data?.analysis_id || viewEnd <= viewStart) return;
                const serial = ++analysisSerial;
                resolution.textContent = "analysing…";
                metricElements[4].textContent = "…";
                const points = Math.max(512, Math.ceil(waveform.clientWidth * 2));
                const query = new URLSearchParams({
                    id: data.analysis_id,
                    view: viewSelect.value,
                    start: String(viewStart),
                    end: String(viewEnd),
                    points: String(points),
                    });
                try {
                    const response = await api.fetchApi(`/alice_lab_audio_tools/audio_compare_analysis?${query}`);
                    const payload = await response.json();
                    if (!response.ok) throw new Error(payload.error || "Detailed analysis failed");
                    if (serial !== analysisSerial) return;
                    detail = payload;
                    updateVisibleMetric(detail);
                    resolution.textContent = `${formatTime(viewStart)}–${formatTime(viewEnd)} · ${Number(detail.points).toLocaleString()} pts`;
                    resolution.title = `Visible range: ${formatTime(viewStart)}–${formatTime(viewEnd)}`;
                    summary.textContent = `${formatTime(selectionStart)} – ${formatTime(selectionEnd)} selected`;
                    summary.style.color = "#9eabb8";
                    draw();
                } catch (error) {
                    if (serial !== analysisSerial) return;
                    resolution.textContent = "analysis unavailable";
                    metricElements[4].textContent = "—";
                    summary.textContent = `Audio Compare: ${error.message}`;
                    summary.style.color = "#ff7474";
                }
            }

            function scheduleAnalysis(delay = 180) {
                clearTimeout(analysisTimer);
                analysisTimer = setTimeout(requestDetail, delay);
                draw();
            }

            function stopPlayback() {
                if (playing) inspectionTime = currentPlaybackTime();
                playing = false;
                for (const media of [audioA, audioB]) {
                    media.pause();
                    media.removeAttribute("src");
                    media.load();
                }
                playButton.textContent = "▶";
                playButton.title = "Play A–B";
                playButton.setAttribute("aria-label", "Play A–B");
                draw();
            }

            function waitForMedia(media, url) {
                return new Promise((resolve, reject) => {
                    const ready = () => { cleanup(); resolve(); };
                    const failed = () => { cleanup(); reject(new Error("Playback audio could not be loaded")); };
                    const cleanup = () => {
                        media.removeEventListener("canplay", ready);
                        media.removeEventListener("error", failed);
                    };
                    media.addEventListener("canplay", ready, { once: true });
                    media.addEventListener("error", failed, { once: true });
                    media.src = url;
                    media.load();
                });
            }

            async function playSelection() {
                if (!data?.analysis_id) return;
                if (playing) { stopPlayback(); return; }
                playbackStart = Math.max(0, Math.min(selectionStart, selectionEnd));
                playbackEnd = Math.min(currentDuration(), Math.max(selectionStart, selectionEnd), playbackStart + 600);
                if (playbackEnd <= playbackStart) return;
                playButton.textContent = "…";
                playButton.title = "Loading A–B audio";
                playButton.setAttribute("aria-label", "Loading A–B audio");
                playButton.disabled = true;
                const base = {
                    id: data.analysis_id,
                    view: viewSelect.value,
                    start: String(playbackStart),
                    end: String(playbackEnd),
                    cache: String(Date.now()),
                };
                try {
                    const mode = playbackSelect.value;
                    const tasks = [];
                    if (mode === "difference") {
                        tasks.push(waitForMedia(audioA, api.apiURL(`/alice_lab_audio_tools/audio_compare_audio?${new URLSearchParams({ ...base, track: "difference" })}`)));
                    } else {
                        if (mode !== "b") tasks.push(waitForMedia(audioA, api.apiURL(`/alice_lab_audio_tools/audio_compare_audio?${new URLSearchParams({ ...base, track: "a" })}`)));
                        if (mode !== "a") tasks.push(waitForMedia(audioB, api.apiURL(`/alice_lab_audio_tools/audio_compare_audio?${new URLSearchParams({ ...base, track: "b" })}`)));
                    }
                    await Promise.all(tasks);
                    audioA.volume = mode === "overlay" ? 0.65 : 1;
                    audioB.volume = mode === "overlay" ? 0.65 : 1;
                    inspectionTime = playbackStart;
                    playing = true;
                    playButton.textContent = "■";
                    playButton.title = "Stop A–B playback";
                    playButton.setAttribute("aria-label", "Stop A–B playback");
                    if (mode !== "b") await audioA.play();
                    if (mode === "b" || mode === "overlay") await audioB.play();
                } catch (error) {
                    stopPlayback();
                    summary.textContent = `Audio Compare: ${error.message}`;
                    summary.style.color = "#ff7474";
                } finally {
                    playButton.disabled = false;
                }
            }

            function pointerX(event) {
                const rect = waveform.getBoundingClientRect();
                return (event.clientX - rect.left) * waveform.clientWidth / Math.max(1, rect.width);
            }

            function pointerTime(event) {
                const plot = plotRect({ width: waveform.clientWidth, height: waveform.clientHeight });
                const ratio = Math.max(0, Math.min(1, (pointerX(event) - plot.left) / Math.max(1, plot.width)));
                return viewStart + ratio * (viewEnd - viewStart);
            }

            function updateWaveformCursor(event) {
                if (panning || movingSelection) {
                    waveform.style.cursor = "grabbing";
                    return;
                }
                if (!data) {
                    waveform.style.cursor = "default";
                    return;
                }
                const plot = plotRect({ width: waveform.clientWidth, height: waveform.clientHeight });
                const x = pointerX(event);
                const startX = timeX(selectionStart, plot);
                const endX = timeX(selectionEnd, plot);
                waveform.style.cursor = activeMarker || Math.abs(x - startX) <= 12 || Math.abs(x - endX) <= 12
                    ? "ew-resize"
                    : "pointer";
            }

            waveform.addEventListener("pointerdown", (event) => {
                if (!data) return;
                waveform.focus();
                if (event.button === 2) {
                    panning = true;
                    panOriginX = event.clientX;
                    panOriginStart = viewStart;
                    waveform.setPointerCapture(event.pointerId);
                    waveform.style.cursor = "grabbing";
                    return;
                }
                const time = pointerTime(event);
                const plot = plotRect({ width: waveform.clientWidth, height: waveform.clientHeight });
                const x = pointerX(event);
                const startX = timeX(selectionStart, plot);
                const endX = timeX(selectionEnd, plot);
                selectionDragCandidate = false;
                movingSelection = false;
                if (Math.abs(x - startX) <= 12) activeMarker = selectedMarker = "start";
                else if (Math.abs(x - endX) <= 12) activeMarker = selectedMarker = "end";
                else if (time > selectionStart && time < selectionEnd) {
                    activeMarker = null;
                    selectedMarker = null;
                    selectionDragCandidate = true;
                    selectionOriginTime = time;
                    selectionOriginStart = selectionStart;
                    selectionOriginEnd = selectionEnd;
                    selectionOriginX = event.clientX;
                } else { activeMarker = null; selectedMarker = null; }
                pointerDown = time;
                markerMoved = false;
                waveform.setPointerCapture(event.pointerId);
                waveform.style.cursor = activeMarker ? "ew-resize" : "pointer";
            });
            waveform.addEventListener("pointermove", (event) => {
                if (panning) {
                    const rect = waveform.getBoundingClientRect();
                    const plot = plotRect({ width: waveform.clientWidth, height: waveform.clientHeight });
                    const span = viewEnd - viewStart;
                    const duration = currentDuration();
                    const pointerDelta = (event.clientX - panOriginX) * waveform.clientWidth / Math.max(1, rect.width);
                    const delta = -pointerDelta / Math.max(1, plot.width) * span;
                    const start = Math.max(0, Math.min(duration - span, panOriginStart + delta));
                    viewStart = start;
                    viewEnd = start + span;
                    draw();
                    waveform.style.cursor = "grabbing";
                    return;
                }
                if (selectionDragCandidate || movingSelection) {
                    if (!movingSelection && Math.abs(event.clientX - selectionOriginX) < 5) return;
                    movingSelection = true;
                    waveform.style.cursor = "grabbing";
                    const duration = currentDuration();
                    const length = selectionOriginEnd - selectionOriginStart;
                    const delta = pointerTime(event) - selectionOriginTime;
                    const start = Math.max(0, Math.min(duration - length, selectionOriginStart + delta));
                    setSelection(start, start + length);
                    draw();
                    return;
                }
                if (!activeMarker) { updateWaveformCursor(event); return; }
                const time = pointerTime(event);
                markerMoved = true;
                if (activeMarker === "start") setSelection(Math.min(time, selectionEnd), selectionEnd);
                else setSelection(selectionStart, Math.max(time, selectionStart));
                draw();
            });
            waveform.addEventListener("pointerup", (event) => {
                if (panning) {
                    panning = false;
                    waveform.releasePointerCapture(event.pointerId);
                    scheduleAnalysis(0);
                    updateWaveformCursor(event);
                    return;
                }
                if (movingSelection) {
                    movingSelection = false;
                    selectionDragCandidate = false;
                    summary.textContent = `${formatTime(selectionStart)} – ${formatTime(selectionEnd)} selected`;
                } else if (selectionDragCandidate) {
                    selectionDragCandidate = false;
                    inspectionTime = pointerTime(event);
                } else if (activeMarker && markerMoved) {
                    inspectionTime = activeMarker === "start" ? selectionStart : selectionEnd;
                } else if (!activeMarker && pointerDown !== null) inspectionTime = pointerTime(event);
                activeMarker = null;
                pointerDown = null;
                waveform.releasePointerCapture(event.pointerId);
                draw();
                updateWaveformCursor(event);
            });
            waveform.addEventListener("pointerleave", () => {
                if (pointerDown === null && !activeMarker && !panning && !movingSelection) waveform.style.cursor = "pointer";
            });
            waveform.addEventListener("contextmenu", (event) => event.preventDefault());
            waveform.addEventListener("wheel", (event) => {
                if (!data) return;
                event.preventDefault();
                const duration = currentDuration();
                const oldSpan = viewEnd - viewStart;
                const factor = event.deltaY < 0 ? 0.75 : 1.333333;
                const newSpan = Math.max(minimumViewSpan(), Math.min(duration, oldSpan * factor));
                const anchor = pointerTime(event);
                const ratio = (anchor - viewStart) / Math.max(oldSpan, 1e-9);
                let start = anchor - ratio * newSpan;
                start = Math.max(0, Math.min(duration - newSpan, start));
                setView(start, start + newSpan);
                scheduleAnalysis();
            }, { passive: false });
            waveform.addEventListener("keydown", (event) => {
                if (!selectedMarker || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
                event.preventDefault();
                event.stopPropagation();
                event.stopImmediatePropagation();
                const step = event.ctrlKey ? 0.1 : event.shiftKey ? 0.01 : 0.001;
                const direction = event.key === "ArrowLeft" ? -1 : 1;
                const minimum = 1 / Number(data?.sample_rate || 44100);
                if (selectedMarker === "start") {
                    setSelection(Math.max(0, Math.min(selectionEnd - minimum, selectionStart + direction * step)), selectionEnd);
                    inspectionTime = selectionStart;
                } else {
                    setSelection(selectionStart, Math.max(selectionStart + minimum, Math.min(currentDuration(), selectionEnd + direction * step)));
                    inspectionTime = selectionEnd;
                }
                draw();
            }, true);

            playButton.addEventListener("click", playSelection);
            zoomSelection.addEventListener("click", () => {
                if (!data || selectionEnd <= selectionStart) return;
                const padding = Math.max(0.02, (selectionEnd - selectionStart) * 0.08);
                setView(selectionStart - padding, selectionEnd + padding);
                scheduleAnalysis(0);
            });
            showAll.addEventListener("click", () => {
                setView(0, currentDuration());
                scheduleAnalysis(0);
            });
            selectAll.addEventListener("click", () => {
                setSelection(0, currentDuration());
                summary.textContent = `${formatTime(selectionStart)} – ${formatTime(selectionEnd)} selected`;
                draw();
            });
            loopButton.addEventListener("click", () => {
                loopPlayback = !loopPlayback;
                loopButton.title = loopPlayback ? "Loop: ON" : "Loop: OFF";
                loopButton.setAttribute("aria-label", loopButton.title);
                loopButton.style.color = loopPlayback ? "#79c8f2" : "";
                loopButton.style.background = loopPlayback ? "#274657" : "";
            });

            function commitSelection(input, isStart) {
                const value = parseTime(input.value);
                if (value === null) { syncSelectionControls(); return; }
                if (isStart) setSelection(Math.min(value, selectionEnd), selectionEnd);
                else setSelection(selectionStart, Math.max(value, selectionStart));
                draw();
            }
            for (const input of [startInput, endInput]) {
                input.addEventListener("keydown", (event) => event.stopPropagation());
            }
            startInput.addEventListener("change", () => commitSelection(startInput, true));
            endInput.addEventListener("change", () => commitSelection(endInput, false));
            const nudgeSelection = (marker, delta) => {
                selectedMarker = marker;
                if (marker === "start") setSelection(selectionStart + delta, selectionEnd);
                else setSelection(selectionStart, selectionEnd + delta);
                inspectionTime = marker === "start" ? selectionStart : selectionEnd;
                draw();
            };
            startMinus.addEventListener("click", () => nudgeSelection("start", -0.01));
            startPlus.addEventListener("click", () => nudgeSelection("start", 0.01));
            endMinus.addEventListener("click", () => nudgeSelection("end", -0.01));
            endPlus.addEventListener("click", () => nudgeSelection("end", 0.01));
            displaySelect.addEventListener("change", () => scheduleAnalysis(0));
            fixedDisplayButton.addEventListener("click", () => {
                fixedDisplay = !fixedDisplay;
                syncDisplayControls();
                scheduleAnalysis(0);
            });
            playbackSelect.addEventListener("change", () => {
                if (playing) stopPlayback();
                syncDisplayControls();
                scheduleAnalysis(0);
            });
            viewSelect.addEventListener("change", () => {
                stopPlayback();
                setView(0, currentDuration());
                setSelection(0, currentDuration());
                detail = null;
                scheduleAnalysis(0);
            });
            syncDisplayControls();

            const updatePlayback = () => {
                if (!playing) return;
                const mode = playbackSelect.value;
                const master = mode === "b" ? audioB : audioA;
                const follower = mode === "overlay" ? audioB : null;
                if (follower && Math.abs(follower.currentTime - master.currentTime) > 0.03) follower.currentTime = master.currentTime;
                if (master.ended || playbackStart + master.currentTime >= playbackEnd) {
                    if (loopPlayback) {
                        audioA.currentTime = 0;
                        audioB.currentTime = 0;
                        if (mode !== "b") audioA.play();
                        if (mode === "b" || mode === "overlay") audioB.play();
                    } else stopPlayback();
                }
                else draw();
            };
            audioA.addEventListener("timeupdate", updatePlayback);
            audioB.addEventListener("timeupdate", updatePlayback);
            audioA.addEventListener("ended", updatePlayback);
            audioB.addEventListener("ended", updatePlayback);

            const widget = node.addDOMWidget("alice_lab_audio_compare_ui", "ALICE_LAB_AUDIO_COMPARE", root, {
                serialize: false,
                hideOnZoom: true,
            });
            widget.computeSize = (width) => [width, minimumPanelHeight];
            let resizeTimer = null;
            let panelHeight = minimumPanelHeight - 44;
            let fitPending = false;
            function fitPanel() {
                fitPending = false;
                root.style.width = `${Math.max(120, node.size[0] - 20)}px`;
                root.style.minWidth = `0`;
                root.style.boxSizing = "border-box";
                const computedHeight = Number(node.computeSize?.()[1]) || minimumPanelHeight;
                const chromeHeight = Math.max(0, computedHeight - minimumPanelHeight);
                const availableHeight = Math.max(minimumPanelHeight, node.size[1] - chromeHeight);
                panelHeight = Math.max(minimumPanelHeight - 44, availableHeight - 44);
                root.style.height = `${panelHeight}px`;
                draw();
                clearTimeout(resizeTimer);
                resizeTimer = setTimeout(() => requestDetail(), 250);
                node.graph?.setDirtyCanvas(true, true);
            }
            function schedulePanelFit() {
                if (fitPending) return;
                fitPending = true;
                requestAnimationFrame(fitPanel);
            }
            chainCallback(node, "onResize", schedulePanelFit);
            chainCallback(node, "onConfigure", schedulePanelFit);
            chainCallback(node, "onRemoved", stopPlayback);
            chainCallback(node, "onExecuted", function (message) {
                stopPlayback();
                try {
                    const payload = message?.alice_lab_audio_compare?.[0] ?? message?.alice_lab_audio_compare;
                    data = typeof payload === "string" ? JSON.parse(payload) : payload;
                    if (!data) throw new Error("No comparison result was returned");
                    detail = null;
                    inspectionTime = null;
                    activeMarker = null;
                    selectedMarker = null;
                    const autoAlignEnabled = data.auto_align !== false;
                    viewSelect.value = autoAlignEnabled ? "aligned" : "raw";
                    viewSelect.disabled = !autoAlignEnabled;
                    viewSelect.title = autoAlignEnabled
                        ? "Switch between the original timing and automatic alignment."
                        : "Auto-align is OFF; the original timing is shown.";
                    setView(0, currentDuration());
                    setSelection(0, currentDuration());
                    updateGlobalMetrics(data);
                    summary.textContent = `${Number(data.duration).toFixed(3)}s · ${data.sample_rate} Hz${autoAlignEnabled ? "" : " · auto-align OFF"}`;
                    summary.style.color = "#9eabb8";
                    scheduleAnalysis(0);
                } catch (error) {
                    summary.textContent = `Audio Compare: ${error.message}`;
                    summary.style.color = "#ff7474";
                }
            });
            node.setSize([Math.max(node.size[0], 900), Math.max(node.size[1], 720)]);
            schedulePanelFit();
            new ResizeObserver(draw).observe(root);
        });
    },
});

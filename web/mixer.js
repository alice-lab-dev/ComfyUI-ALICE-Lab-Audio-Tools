import { app } from "../../../scripts/app.js";
import { drawTimeAxis } from "./time_axis.js";

const TRACK_COUNT = 8;
const COLORS = ["#67c5e8", "#6bd39a", "#ffb45e", "#c69cff", "#ff7474", "#63d7d1", "#e8d267", "#ed8fd1"];

function defaults(index) {
    return {
        name: `Track ${index + 1}`,
        color: COLORS[index % COLORS.length],
        gain_db: 0,
        mute: false,
        solo: false,
        offset: 0,
        fade_in: 0,
        fade_out: 0,
    };
}

function chainCallback(target, key, callback) {
    const original = target[key];
    target[key] = function (...args) {
        const result = original?.apply(this, args);
        callback.apply(this, args);
        return result;
    };
}

app.registerExtension({
    name: "ALICE_Lab.AudioMixer",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "AliceLabAudioMixer") return;

        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            const node = this;
            const settingsWidget = node.widgets.find((widget) => widget.name === "track_settings");
            const masterWidget = node.widgets.find((widget) => widget.name === "master_db");
            const clippingWidget = node.widgets.find((widget) => widget.name === "prevent_clipping");
            const resetWidget = node.widgets.find((widget) => widget.name === "reset_before_run");
            let settings;
            try {
                const parsed = JSON.parse(settingsWidget.value || "[]");
                settings = Array.from({ length: TRACK_COUNT }, (_, index) => ({ ...defaults(index), ...(parsed[index] || {}) }));
            } catch {
                settings = Array.from({ length: TRACK_COUNT }, (_, index) => defaults(index));
            }
            let renderData = { duration: 0, peak: 0, tracks: [], mix_peaks: [] };
            let drag = null;
            let viewStart = 0;
            let viewEnd = 0;
            let showFullTimeline = true;

            function readStoredSettings() {
                try {
                    const parsed = JSON.parse(settingsWidget.value || "[]");
                    return Array.from({ length: TRACK_COUNT }, (_, index) => ({
                        ...defaults(index),
                        ...(parsed[index] || {}),
                    }));
                } catch {
                    return Array.from({ length: TRACK_COUNT }, (_, index) => defaults(index));
                }
            }

            // The JSON widget is the workflow-safe bridge between this rich UI
            // and the backend node arguments; users should not edit it directly.
            // ComfyUI/LiteGraph treats converted-widget as a non-drawing widget.
            // A generic "hidden" type can still be painted by some frontends.
            settingsWidget.type = "converted-widget";
            settingsWidget.hidden = true;
            settingsWidget.computeSize = () => [0, -4];

            const root = document.createElement("div");
            root.style.cssText = "height:610px;display:flex;flex-direction:column;gap:7px;padding:6px;box-sizing:border-box;background:#15191f;color:#dce3ea;font:12px sans-serif;overflow:hidden";
            const header = document.createElement("div");
            header.style.cssText = "display:flex;align-items:center;gap:10px;padding:7px 9px;background:#242830;border:1px solid #39424e;border-radius:6px";
            const title = document.createElement("strong");
            title.textContent = "Audio tracks";
            const status = document.createElement("span");
            status.style.cssText = "margin-left:auto;color:#9eabb8";
            header.append(title, status);

            const rows = document.createElement("div");
            rows.style.cssText = "display:flex;flex-direction:column;gap:4px;max-height:265px;overflow:auto;padding-right:3px";
            const canvas = document.createElement("canvas");
            canvas.tabIndex = 0;
            canvas.style.cssText = "width:100%;height:270px;min-height:170px;flex:1;background:#101419;border:1px solid #39424e;box-sizing:border-box;cursor:pointer;touch-action:none";
            const footer = document.createElement("div");
            footer.style.cssText = "display:flex;gap:12px;align-items:center;color:#aeb9c4";
            const help = document.createElement("span");
            help.textContent = "Wheel: zoom · Right-drag: pan · Left-drag waveform: position · Drag edge handles: fades";
            help.style.flex = "1";
            const viewLabel = document.createElement("span");
            viewLabel.style.whiteSpace = "nowrap";
            const showAllButton = document.createElement("button");
            showAllButton.textContent = "Show All";
            showAllButton.style.whiteSpace = "nowrap";
            footer.append(help, viewLabel, showAllButton);
            root.append(header, rows, canvas, footer);

            function connected(index) {
                return node.inputs?.find((input) => input.name === `audio_${index + 1}`)?.link != null;
            }

            function renderedTrack(index) {
                return renderData.tracks?.find((track) => track.index === index);
            }

            function timelineExtent() {
                const tracks = renderData.tracks || [];
                const start = Math.min(0, ...tracks.map((data) => settings[data.index].offset));
                const end = Math.max(
                    0.001,
                    renderData.duration || 0,
                    ...tracks.map((data) => settings[data.index].offset + data.duration),
                );
                return { start, end, span: Math.max(0.001, end - start) };
            }

            function viewBounds() {
                const extent = timelineExtent();
                if (showFullTimeline || viewEnd <= viewStart) {
                    return extent;
                }
                const minimumSpan = Math.min(0.1, extent.span);
                const span = Math.max(minimumSpan, Math.min(extent.span, viewEnd - viewStart));
                const start = Math.max(extent.start, Math.min(extent.end - span, viewStart));
                return { start, end: start + span, span };
            }

            function showAll() {
                showFullTimeline = true;
                const extent = timelineExtent();
                viewStart = extent.start;
                viewEnd = extent.end;
                draw();
            }

            showAllButton.addEventListener("click", showAll);

            function setFade(index, kind, value) {
                const track = settings[index];
                const data = renderedTrack(index);
                const other = kind === "fade_in" ? track.fade_out : track.fade_in;
                const maximum = data ? Math.max(0, data.duration - other) : 86400;
                track[kind] = Math.round(Math.max(0, Math.min(maximum, value)) * 1000) / 1000;
                const input = kind === "fade_in" ? rowElements[index]?.fadeIn : rowElements[index]?.fadeOut;
                if (input) input.value = String(track[kind]);
            }

            function saveSettings() {
                settingsWidget.value = JSON.stringify(settings);
                settingsWidget.callback?.(settingsWidget.value);
                node.graph?.setDirtyCanvas(true, true);
                draw();
            }

            function numberInput(value, min, max, step, onChange) {
                const input = document.createElement("input");
                input.type = "number";
                input.value = String(value);
                input.min = String(min);
                input.max = String(max);
                input.step = String(step);
                input.style.cssText = "width:61px;background:#171a20;color:#e7edf3;border:1px solid #48515e;border-radius:3px;padding:3px";
                input.addEventListener("keydown", (event) => event.stopPropagation());
                input.addEventListener("change", () => onChange(Number(input.value) || 0));
                return input;
            }

            const rowElements = settings.map((track, index) => {
                const row = document.createElement("div");
                row.style.cssText = "display:grid;grid-template-columns:12px minmax(100px,1fr) 44px 44px auto auto auto auto;gap:5px;align-items:center;padding:5px 6px;background:#20252c;border-radius:4px";
                const color = document.createElement("span");
                color.style.cssText = `width:10px;height:28px;border-radius:2px;background:${track.color}`;
                const name = document.createElement("input");
                name.value = track.name;
                name.style.cssText = "min-width:0;background:#171a20;color:#e7edf3;border:1px solid #48515e;border-radius:3px;padding:4px";
                name.addEventListener("keydown", (event) => event.stopPropagation());
                name.addEventListener("change", () => { track.name = name.value || `Track ${index + 1}`; saveSettings(); });
                const mute = document.createElement("button");
                const solo = document.createElement("button");
                mute.textContent = "M"; solo.textContent = "S";
                const updateButtons = () => {
                    mute.style.background = track.mute ? "#c75b5b" : "";
                    solo.style.background = track.solo ? "#c49a45" : "";
                };
                mute.onclick = () => {
                    track.mute = !track.mute;
                    if (track.mute) track.solo = false;
                    updateButtons();
                    saveSettings();
                };
                solo.onclick = () => {
                    track.solo = !track.solo;
                    if (track.solo) track.mute = false;
                    updateButtons();
                    saveSettings();
                };
                updateButtons();
                const labeled = (text, input) => {
                    const label = document.createElement("label");
                    label.style.cssText = "display:flex;align-items:center;gap:3px;white-space:nowrap";
                    label.append(text, input);
                    return label;
                };
                const gain = numberInput(track.gain_db, -100, 24, 0.1, (value) => { track.gain_db = value; saveSettings(); });
                const offset = numberInput(track.offset, -86400, 86400, 0.01, (value) => { track.offset = value; saveSettings(); });
                const fadeIn = numberInput(track.fade_in, 0, 86400, 0.01, (value) => { setFade(index, "fade_in", value); saveSettings(); });
                const fadeOut = numberInput(track.fade_out, 0, 86400, 0.01, (value) => { setFade(index, "fade_out", value); saveSettings(); });
                row.append(color, name, mute, solo, labeled("dB", gain), labeled("Pos", offset), labeled("Fade In", fadeIn), labeled("Fade Out", fadeOut));
                rows.append(row);
                return { row, name, mute, solo, gain, offset, fadeIn, fadeOut, updateButtons };
            });

            function resetTrackValues() {
                settings.forEach((track, index) => {
                    track.gain_db = 0;
                    track.offset = 0;
                    track.fade_in = 0;
                    track.fade_out = 0;
                    const controls = rowElements[index];
                    controls.gain.value = "0";
                    controls.offset.value = "0";
                    controls.fadeIn.value = "0";
                    controls.fadeOut.value = "0";
                });
                settingsWidget.value = JSON.stringify(settings);
                settingsWidget.callback?.(settingsWidget.value);
                node.graph?.setDirtyCanvas(true, true);
                draw();
            }

            chainCallback(settingsWidget, "beforeQueued", function () {
                if (resetWidget?.value) resetTrackValues();
            });

            // ComfyUI restores serialized widget values after onNodeCreated. Reload
            // the hidden JSON then, otherwise the visible M/S buttons can disagree
            // with the settings used by the backend mixer.
            function restoreVisibleSettings() {
                const restored = readStoredSettings();
                let normalized = false;
                restored.forEach((value, index) => {
                    Object.assign(settings[index], value);
                    // Mute and solo are mutually exclusive on one track. Prefer
                    // mute when normalizing workflows saved by older versions.
                    if (settings[index].mute && settings[index].solo) {
                        settings[index].solo = false;
                        normalized = true;
                    }
                    const controls = rowElements[index];
                    controls.name.value = settings[index].name;
                    controls.gain.value = String(settings[index].gain_db);
                    controls.offset.value = String(settings[index].offset);
                    controls.fadeIn.value = String(settings[index].fade_in);
                    controls.fadeOut.value = String(settings[index].fade_out);
                    controls.updateButtons();
                });
                if (normalized) {
                    settingsWidget.value = JSON.stringify(settings);
                    settingsWidget.callback?.(settingsWidget.value);
                }
                draw();
            }

            function refreshRows() {
                let count = 0;
                rowElements.forEach(({ row }, index) => {
                    const show = connected(index);
                    row.style.display = show ? "grid" : "none";
                    if (show) count++;
                });
                title.textContent = `Audio tracks (${count}/${TRACK_COUNT})`;
                if (!count) status.textContent = "Connect AUDIO inputs";
                draw();
            }

            function drawPeaks(ctx, peaks, x, y, width, height, color) {
                if (!peaks?.length || width <= 0) return;
                ctx.strokeStyle = color;
                ctx.lineWidth = 1;
                ctx.beginPath();
                // A zoomed clip can be much wider than the canvas. Draw only
                // the visible pixels while retaining their source positions.
                const firstPixel = Math.max(0, Math.floor(-x));
                const lastPixel = Math.min(Math.ceil(width), Math.ceil(ctx.canvas.clientWidth - x));
                for (let px = firstPixel; px < lastPixel; px++) {
                    const peak = peaks[Math.min(peaks.length - 1, Math.floor(px / width * peaks.length))] || 0;
                    const amplitude = peak * height * 0.42;
                    ctx.moveTo(x + px + 0.5, y + height / 2 - amplitude);
                    ctx.lineTo(x + px + 0.5, y + height / 2 + amplitude);
                }
                ctx.stroke();
            }

            function draw() {
                // DOM widgets can be CSS-transformed with the ComfyUI canvas.
                // Draw in the untransformed CSS coordinate space so redraws
                // do not apply the node zoom to the bitmap a second time.
                const rect = {
                    width: canvas.clientWidth,
                    height: canvas.clientHeight,
                };
                const ratio = window.devicePixelRatio || 1;
                canvas.width = Math.max(1, Math.round(rect.width * ratio));
                canvas.height = Math.max(1, Math.round(rect.height * ratio));
                const ctx = canvas.getContext("2d");
                ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
                ctx.fillStyle = "#101419";
                ctx.fillRect(0, 0, rect.width, rect.height);
                const tracks = renderData.tracks || [];
                const view = viewBounds();
                const rowHeight = tracks.length ? rect.height / tracks.length : rect.height;
                tracks.forEach((data, rowIndex) => {
                    const index = data.index;
                    const track = settings[index];
                    const y = rowIndex * rowHeight;
                    const x = (track.offset - view.start) / view.span * rect.width;
                    const width = data.duration / view.span * rect.width;
                    ctx.fillStyle = rowIndex % 2 ? "#151b22" : "#12171d";
                    ctx.fillRect(0, y, rect.width, rowHeight);
                    ctx.strokeStyle = "#303844";
                    ctx.beginPath();
                    ctx.moveTo(0, y + rowHeight / 2);
                    ctx.lineTo(rect.width, y + rowHeight / 2);
                    ctx.stroke();
                    ctx.globalAlpha = data.enabled ? 1 : 0.28;
                    drawPeaks(ctx, data.peaks, x, y, width, rowHeight, track.color);
                    ctx.globalAlpha = 1;
                    ctx.fillStyle = track.color;
                    ctx.font = "bold 11px sans-serif";
                    ctx.fillText(track.name, 6, y + 13);
                    if (rowIndex === 0) {
                        ctx.fillStyle = "#8795a3";
                        ctx.font = "9px sans-serif";
                        ctx.textAlign = "right";
                        ctx.fillText("±1 FS", rect.width - 5, y + 11);
                        ctx.textAlign = "left";
                    }
                    if (track.fade_in > 0) {
                        ctx.strokeStyle = "#e7edf3";
                        ctx.beginPath(); ctx.moveTo(x, y + rowHeight); ctx.lineTo(x + track.fade_in / view.span * rect.width, y); ctx.stroke();
                    }
                    if (track.fade_out > 0) {
                        const end = x + width;
                        ctx.strokeStyle = "#e7edf3";
                        ctx.beginPath(); ctx.moveTo(end - track.fade_out / view.span * rect.width, y); ctx.lineTo(end, y + rowHeight); ctx.stroke();
                    }
                    const fadeInX = x + track.fade_in / view.span * rect.width;
                    const fadeOutX = x + width - track.fade_out / view.span * rect.width;
                    ctx.fillStyle = "#f2f5f8";
                    ctx.beginPath();
                    ctx.moveTo(fadeInX, y + 1); ctx.lineTo(fadeInX + 8, y + 1); ctx.lineTo(fadeInX, y + 9); ctx.closePath(); ctx.fill();
                    ctx.beginPath();
                    ctx.moveTo(fadeOutX, y + 1); ctx.lineTo(fadeOutX - 8, y + 1); ctx.lineTo(fadeOutX, y + 9); ctx.closePath(); ctx.fill();
                });
                drawTimeAxis(ctx, { left: 0, right: rect.width, top: 0, bottom: rect.height, start: view.start, end: view.end });
                if (view.start < 0 && view.end > 0) {
                    const zeroX = -view.start / view.span * rect.width;
                    ctx.strokeStyle = "#8795a3";
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.moveTo(zeroX, 0);
                    ctx.lineTo(zeroX, rect.height);
                    ctx.stroke();
                }
                if (drag?.mode === "fade_in" || drag?.mode === "fade_out") {
                    const value = settings[drag.index][drag.mode];
                    const label = `${drag.mode === "fade_in" ? "Fade In" : "Fade Out"} ${value.toFixed(3)}s`;
                    ctx.font = "bold 12px sans-serif";
                    const labelWidth = ctx.measureText(label).width + 12;
                    const labelX = Math.max(3, Math.min(rect.width - labelWidth - 3, drag.currentX - rect.left + 10));
                    const labelY = Math.max(18, drag.currentY - rect.top - 8);
                    ctx.fillStyle = "rgba(8, 10, 13, 0.92)";
                    ctx.fillRect(labelX, labelY - 15, labelWidth, 20);
                    ctx.fillStyle = "#f2f5f8";
                    ctx.fillText(label, labelX + 6, labelY);
                }
                if (renderData.duration) {
                    const peakDb = renderData.peak > 0 ? 20 * Math.log10(renderData.peak) : -Infinity;
                    status.textContent = `${renderData.duration.toFixed(3)}s · peak ${Number.isFinite(peakDb) ? peakDb.toFixed(1) : "−∞"} dBFS${renderData.clipping_prevented ? " · protected" : ""}`;
                    status.style.color = renderData.peak > 1 ? "#ff7474" : "#9eabb8";
                }
                viewLabel.textContent = `${view.start.toFixed(3)}–${view.end.toFixed(3)}s`;
            }

            function trackAt(event) {
                const connectedTracks = renderData.tracks || [];
                if (!connectedTracks.length) return null;
                const rect = canvas.getBoundingClientRect();
                const row = Math.max(0, Math.min(connectedTracks.length - 1, Math.floor((event.clientY - rect.top) / rect.height * connectedTracks.length)));
                return connectedTracks[row];
            }

            function fadeHandleAt(event) {
                const data = trackAt(event);
                if (!data || !renderData.duration) return null;
                const rect = canvas.getBoundingClientRect();
                const track = settings[data.index];
                const view = viewBounds();
                const x = (track.offset - view.start) / view.span * rect.width;
                const trackWidth = data.duration / view.span * rect.width;
                const pointerX = event.clientX - rect.left;
                const handles = [
                    { mode: "fade_in", x: x + track.fade_in / view.span * rect.width },
                    { mode: "fade_out", x: x + trackWidth - track.fade_out / view.span * rect.width },
                ];
                const closest = handles.sort((a, b) => Math.abs(pointerX - a.x) - Math.abs(pointerX - b.x))[0];
                return Math.abs(pointerX - closest.x) <= 10 ? { ...closest, data } : null;
            }

            canvas.addEventListener("pointerdown", (event) => {
                const data = trackAt(event);
                if (!data || !renderData.duration) return;
                if (event.button === 2) {
                    const view = viewBounds();
                    drag = { mode: "pan", x: event.clientX, start: view.start, span: view.span };
                    canvas.setPointerCapture(event.pointerId);
                    canvas.style.cursor = "grabbing";
                    return;
                }
                if (event.button !== 0) return;
                const handle = fadeHandleAt(event);
                if (handle) {
                    drag = {
                        mode: handle.mode,
                        index: data.index,
                        x: event.clientX,
                        initial: settings[data.index][handle.mode],
                        currentX: event.clientX,
                        currentY: event.clientY,
                    };
                } else {
                    drag = { mode: "offset", index: data.index, x: event.clientX, offset: settings[data.index].offset };
                }
                canvas.setPointerCapture(event.pointerId);
                canvas.style.cursor = handle ? "ew-resize" : "grabbing";
            });
            canvas.addEventListener("pointermove", (event) => {
                if (!drag) {
                    canvas.style.cursor = fadeHandleAt(event) ? "ew-resize" : "grab";
                    return;
                }
                const width = canvas.getBoundingClientRect().width;
                const view = viewBounds();
                if (drag.mode === "pan") {
                    const extent = timelineExtent();
                    const nextStart = drag.start - (event.clientX - drag.x) / width * drag.span;
                    viewStart = Math.max(extent.start, Math.min(extent.end - drag.span, nextStart));
                    viewEnd = viewStart + drag.span;
                } else if (drag.mode === "offset") {
                    settings[drag.index].offset = Math.round((drag.offset + (event.clientX - drag.x) / width * view.span) * 1000) / 1000;
                    rowElements[drag.index].offset.value = String(settings[drag.index].offset);
                } else {
                    const direction = drag.mode === "fade_in" ? 1 : -1;
                    setFade(drag.index, drag.mode, drag.initial + direction * (event.clientX - drag.x) / width * view.span);
                    drag.currentX = event.clientX;
                    drag.currentY = event.clientY;
                }
                draw();
            });
            canvas.addEventListener("pointerup", (event) => {
                if (!drag) return;
                const save = drag.mode !== "pan";
                drag = null;
                canvas.releasePointerCapture(event.pointerId);
                canvas.style.cursor = "grab";
                if (save) saveSettings();
                else draw();
            });
            canvas.addEventListener("pointercancel", () => {
                drag = null;
                canvas.style.cursor = "grab";
            });
            canvas.addEventListener("dblclick", (event) => {
                const handle = fadeHandleAt(event);
                if (!handle) return;
                setFade(handle.data.index, handle.mode, 0);
                saveSettings();
                event.preventDefault();
                event.stopPropagation();
            });
            canvas.addEventListener("contextmenu", (event) => event.preventDefault());
            canvas.addEventListener("wheel", (event) => {
                if (!renderData.duration) return;
                event.preventDefault();
                event.stopPropagation();
                const rect = canvas.getBoundingClientRect();
                const view = viewBounds();
                const extent = timelineExtent();
                const minimumSpan = Math.min(0.1, extent.span);
                const nextSpan = Math.max(minimumSpan, Math.min(extent.span, view.span * (event.deltaY < 0 ? 0.75 : 1.333333)));
                const pointerRatio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
                const anchor = view.start + pointerRatio * view.span;
                const nextStart = Math.max(extent.start, Math.min(extent.end - nextSpan, anchor - pointerRatio * nextSpan));
                viewStart = nextStart;
                viewEnd = nextStart + nextSpan;
                showFullTimeline = nextSpan >= extent.span - 0.001;
                draw();
            }, { passive: false });

            const widget = node.addDOMWidget("alice_lab_audio_tools_mixer_ui", "ALICE_LAB_MIXER", root, { serialize: false, hideOnZoom: true });
            const minimumPanelHeight = 610;
            let panelHeight = minimumPanelHeight;
            let fitPending = false;
            // Report only the minimum. The DOM panel then consumes the height
            // already assigned by the user instead of forcing the node larger.
            widget.computeSize = (width) => [width, minimumPanelHeight];

            function fitPanelToNode() {
                fitPending = false;
                root.style.width = `${Math.max(120, node.size[0] - 20)}px`;
                const computedHeight = Number(node.computeSize?.()[1]) || minimumPanelHeight;
                const chromeHeight = Math.max(0, computedHeight - minimumPanelHeight);
                const availableHeight = Math.max(minimumPanelHeight, node.size[1] - chromeHeight);
                if (Math.abs(availableHeight - panelHeight) < 1) return;
                panelHeight = availableHeight;
                root.style.height = `${panelHeight}px`;
                draw();
                node.graph?.setDirtyCanvas(true, true);
            }

            function schedulePanelFit() {
                if (fitPending) return;
                fitPending = true;
                requestAnimationFrame(fitPanelToNode);
            }

            chainCallback(node, "onResize", schedulePanelFit);
            node.setSize([Math.max(node.size[0], 780), Math.max(node.size[1], 760)]);
            schedulePanelFit();

            chainCallback(node, "onConnectionsChange", refreshRows);
            chainCallback(node, "onConfigure", function () {
                setTimeout(restoreVisibleSettings, 0);
                schedulePanelFit();
            });
            chainCallback(node, "onExecuted", function (message) {
                try {
                    const value = message?.alice_lab_audio_tools_mixer?.[0] ?? message?.alice_lab_audio_tools_mixer;
                    renderData = typeof value === "string" ? JSON.parse(value) : value;
                    if (showFullTimeline || viewEnd <= viewStart) {
                        const extent = timelineExtent();
                        viewStart = extent.start;
                        viewEnd = extent.end;
                    } else {
                        const view = viewBounds();
                        viewStart = view.start;
                        viewEnd = view.end;
                    }
                    draw();
                } catch (error) {
                    status.textContent = `Mixer display error: ${error.message}`;
                }
            });
            chainCallback(masterWidget, "callback", draw);
            chainCallback(clippingWidget, "callback", draw);
            new ResizeObserver(draw).observe(canvas);
            setTimeout(refreshRows, 0);
        });
    },
});

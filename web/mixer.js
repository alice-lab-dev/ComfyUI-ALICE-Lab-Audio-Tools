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
        source_start: 0,
        timeline_duration: null,
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
            let selectedClip = null;
            let clipClipboard = null;
            let clipboardVisual = null;
            let rightDragMoved = false;
            let contextMenu = null;
            let contextMenuDismiss = null;
            const visualClips = new Map();

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
            help.textContent = "Right-click: edit · Right-drag: pan · Wheel: zoom · Drag clip: position · Edges: length · Top: fades";
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

            function laneData(index) {
                return renderData.tracks?.find((track) => track.index === index);
            }

            function clipDuration(data) {
                return Number(data.timeline_duration ?? data.duration) || 0.001;
            }

            function clipSourceStart(data) {
                const value = Number(data.source_start);
                return Number.isFinite(value) ? value : 0;
            }

            function payloadClips(index) {
                const lane = laneData(index);
                if (Array.isArray(lane?.clips)) return lane.clips;
                return lane?.duration ? [{ ...lane, id: `source-${index}`, source_index: index }] : [];
            }

            function clipStateFromData(data, fallbackSource) {
                return {
                    id: String(data.id || `clip-${Date.now()}-${Math.random().toString(36).slice(2)}`),
                    source_index: Number.isInteger(data.source_index) ? data.source_index : fallbackSource,
                    gain_db: Number(data.gain_db) || 0,
                    offset: Number(data.offset) || 0,
                    source_start: Number(data.source_start) || 0,
                    timeline_duration: Number(data.timeline_duration ?? data.duration) || null,
                    fade_in: Number(data.fade_in) || 0,
                    fade_out: Number(data.fade_out) || 0,
                };
            }

            function ensureLaneClips(index) {
                if (!Array.isArray(settings[index].clips)) {
                    settings[index].clips = payloadClips(index).map((clip) => clipStateFromData(clip, index));
                }
                return settings[index].clips;
            }

            function allClipData(index) {
                if (!Array.isArray(settings[index].clips)) return payloadClips(index);
                const payload = payloadClips(index);
                return settings[index].clips.map((state) => {
                    const visual = payload.find((clip) => clip.id === state.id)
                        || visualClips.get(state.id)
                        || renderData.tracks?.flatMap((lane) => lane.clips || []).find((clip) => clip.source_index === state.source_index)
                        || null;
                    // A serialized edit state without current audio metadata
                    // used to be drawn as an empty selection rectangle. Do not
                    // render it until its source has produced a real waveform.
                    return visual ? { ...visual, ...state } : null;
                }).filter(Boolean);
            }

            function pruneDisconnectedClips() {
                let changed = false;
                settings.forEach((track, index) => {
                    if (!Array.isArray(track.clips)) return;
                    const retained = track.clips.filter((clip) => connected(clip.source_index));
                    if (retained.length === track.clips.length) return;
                    changed = true;
                    if (!retained.length && !connected(index)) delete track.clips;
                    else track.clips = retained;
                });
                if (selectedClip && !findClip(selectedClip.trackIndex, selectedClip.id)) selectedClip = null;
                if (changed) {
                    settingsWidget.value = JSON.stringify(settings);
                    settingsWidget.callback?.(settingsWidget.value);
                    node.graph?.setDirtyCanvas(true, true);
                }
            }

            function clearConnectionRenderState() {
                pruneDisconnectedClips();
                renderData = { duration: 0, peak: 0, tracks: [], mix_peaks: [] };
                visualClips.clear();
                selectedClip = null;
                clipClipboard = null;
                clipboardVisual = null;
                drag = null;
                closeContextMenu();
                refreshRows();
            }

            function findClip(index, id) {
                return allClipData(index).find((clip) => clip.id === id) || null;
            }

            function clipState(index, id) {
                return ensureLaneClips(index).find((clip) => clip.id === id) || null;
            }

            function activeClipState(index, create = false) {
                if (!Array.isArray(settings[index].clips)) {
                    if (!create) return null;
                    ensureLaneClips(index);
                }
                const clips = settings[index].clips;
                if (!clips.length) return null;
                if (selectedClip?.trackIndex === index) {
                    const selected = clips.find((clip) => clip.id === selectedClip.id);
                    if (selected) return selected;
                }
                return clips[0];
            }

            function newClipId() {
                return `clip-${Date.now()}-${Math.random().toString(36).slice(2)}`;
            }

            function timelineExtent() {
                const clips = Array.from({ length: TRACK_COUNT }, (_, index) => allClipData(index)).flat();
                const start = Math.min(0, ...clips.map((clip) => clip.offset));
                const end = Math.max(
                    0.001,
                    clips.length ? 0 : (renderData.duration || 0),
                    ...clips.map((clip) => clip.offset + clipDuration(clip)),
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
                const clip = activeClipState(index, true);
                if (!clip) return;
                const data = findClip(index, clip.id) || clip;
                const other = kind === "fade_in" ? clip.fade_out : clip.fade_in;
                const maximum = Math.max(0, clipDuration(data) - other);
                clip[kind] = Math.round(Math.max(0, Math.min(maximum, value)) * 1000) / 1000;
                const input = kind === "fade_in" ? rowElements[index]?.fadeIn : rowElements[index]?.fadeOut;
                if (input) input.value = String(clip[kind]);
            }

            function clampFades(index, duration) {
                const clip = activeClipState(index, true);
                if (!clip) return;
                clip.fade_in = Math.min(clip.fade_in, duration);
                clip.fade_out = Math.min(clip.fade_out, Math.max(0, duration - clip.fade_in));
                rowElements[index].fadeIn.value = String(clip.fade_in);
                rowElements[index].fadeOut.value = String(clip.fade_out);
            }

            function setClipValue(index, key, value) {
                const clip = activeClipState(index, true);
                if (clip) clip[key] = value;
                else settings[index][key] = value;
                saveSettings();
            }

            function syncRowToSelection(index) {
                const controls = rowElements[index];
                if (!controls) return;
                const clip = activeClipState(index, false);
                controls.gain.value = String(clip?.gain_db ?? settings[index].gain_db);
                controls.offset.value = String(clip?.offset ?? settings[index].offset);
                controls.fadeIn.value = String(clip?.fade_in ?? settings[index].fade_in);
                controls.fadeOut.value = String(clip?.fade_out ?? settings[index].fade_out);
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
                const labeled = (text, input, onReset) => {
                    const label = document.createElement("label");
                    label.style.cssText = "display:flex;align-items:center;gap:3px;white-space:nowrap";
                    const caption = document.createElement("span");
                    caption.textContent = text;
                    caption.title = `Double-click to reset ${text}`;
                    caption.style.cursor = "pointer";
                    caption.addEventListener("dblclick", (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        onReset();
                    });
                    label.append(caption, input);
                    return label;
                };
                const gain = numberInput(track.gain_db, -100, 24, 0.1, (value) => setClipValue(index, "gain_db", value));
                const offset = numberInput(track.offset, -86400, 86400, 0.01, (value) => setClipValue(index, "offset", value));
                const fadeIn = numberInput(track.fade_in, 0, 86400, 0.01, (value) => { setFade(index, "fade_in", value); saveSettings(); });
                const fadeOut = numberInput(track.fade_out, 0, 86400, 0.01, (value) => { setFade(index, "fade_out", value); saveSettings(); });
                row.append(
                    color,
                    name,
                    mute,
                    solo,
                    labeled("dB", gain, () => { gain.value = "0"; setClipValue(index, "gain_db", 0); }),
                    labeled("Pos", offset, () => { offset.value = "0"; setClipValue(index, "offset", 0); }),
                    labeled("Fade In", fadeIn, () => { fadeIn.value = "0"; setClipValue(index, "fade_in", 0); }),
                    labeled("Fade Out", fadeOut, () => { fadeOut.value = "0"; setClipValue(index, "fade_out", 0); }),
                );
                rows.append(row);
                return { row, name, mute, solo, gain, offset, fadeIn, fadeOut, updateButtons };
            });

            function resetTrackValues() {
                settings.forEach((track, index) => {
                    track.gain_db = 0;
                    track.offset = 0;
                    track.source_start = 0;
                    track.timeline_duration = null;
                    track.fade_in = 0;
                    track.fade_out = 0;
                    delete track.clips;
                    const controls = rowElements[index];
                    controls.gain.value = "0";
                    controls.offset.value = "0";
                    controls.fadeIn.value = "0";
                    controls.fadeOut.value = "0";
                });
                // Copy/Paste and rendered waveforms are transient edit
                // buffers. A reset run must rebuild them from the inputs that
                // are connected now, not from the preceding execution.
                clipClipboard = null;
                clipboardVisual = null;
                selectedClip = null;
                visualClips.clear();
                closeContextMenu();
                renderData = { duration: 0, peak: 0, tracks: [], mix_peaks: [] };
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
                    syncRowToSelection(index);
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
                    const show = connected(index) || allClipData(index).length > 0;
                    row.style.display = show ? "grid" : "none";
                    if (show) count++;
                });
                title.textContent = `Audio tracks (${count}/${TRACK_COUNT})`;
                if (!count) status.textContent = "Connect AUDIO inputs";
                draw();
            }

            function drawPeaks(ctx, data, track, x, y, width, height, color) {
                const peaks = data.source_peaks?.length ? data.source_peaks : data.peaks;
                if (!peaks?.length || width <= 0) return;
                ctx.strokeStyle = color;
                ctx.lineWidth = 1;
                ctx.beginPath();
                // A zoomed clip can be much wider than the canvas. Draw only
                // the visible pixels while retaining their source positions.
                const firstPixel = Math.max(0, Math.floor(-x));
                const lastPixel = Math.min(Math.ceil(width), Math.ceil(ctx.canvas.clientWidth - x));
                const duration = clipDuration(data);
                const sourceDuration = Number(data.source_duration ?? data.duration) || duration;
                const sourceStart = clipSourceStart(data);
                for (let px = firstPixel; px < lastPixel; px++) {
                    const localTime = (px + 0.5) / width * duration;
                    const sourceTime = sourceStart + localTime;
                    if (sourceTime < 0 || sourceTime >= sourceDuration) continue;
                    const peak = peaks[Math.min(peaks.length - 1, Math.floor(sourceTime / sourceDuration * peaks.length))] || 0;
                    let fade = 1;
                    if (track.fade_in > 0 && localTime < track.fade_in) fade = Math.min(fade, localTime / track.fade_in);
                    if (track.fade_out > 0 && localTime > duration - track.fade_out) fade = Math.min(fade, (duration - localTime) / track.fade_out);
                    const amplitude = peak * Math.max(0, fade) * height * 0.42;
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
                const tracks = Array.from({ length: TRACK_COUNT }, (_, index) => laneData(index) || ({ index, enabled: true, clips: [] }));
                const view = viewBounds();
                const rowHeight = rect.height / TRACK_COUNT;
                tracks.forEach((lane, rowIndex) => {
                    const index = lane.index;
                    const track = settings[index];
                    const y = rowIndex * rowHeight;
                    ctx.fillStyle = rowIndex % 2 ? "#151b22" : "#12171d";
                    ctx.fillRect(0, y, rect.width, rowHeight);
                    ctx.strokeStyle = "#303844";
                    ctx.beginPath();
                    ctx.moveTo(0, y + rowHeight / 2);
                    ctx.lineTo(rect.width, y + rowHeight / 2);
                    ctx.stroke();
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
                    allClipData(index).forEach((clip) => {
                        const x = (clip.offset - view.start) / view.span * rect.width;
                        const duration = clipDuration(clip);
                        const width = duration / view.span * rect.width;
                        ctx.globalAlpha = lane.enabled ? 1 : 0.28;
                        ctx.fillStyle = `${track.color}24`;
                        ctx.fillRect(x, y + 1, width, rowHeight - 2);
                        drawPeaks(ctx, clip, clip, x, y, width, rowHeight, track.color);
                        ctx.globalAlpha = 1;
                        if (selectedClip?.trackIndex === index && selectedClip.id === clip.id) {
                            ctx.strokeStyle = "#ffd166";
                            ctx.lineWidth = 2;
                            ctx.strokeRect(x, y + 1, width, rowHeight - 2);
                        }
                        if (clip.fade_in > 0) {
                            ctx.strokeStyle = "#e7edf3";
                            ctx.beginPath(); ctx.moveTo(x, y + rowHeight); ctx.lineTo(x + clip.fade_in / view.span * rect.width, y); ctx.stroke();
                        }
                        if (clip.fade_out > 0) {
                            const end = x + width;
                            ctx.strokeStyle = "#e7edf3";
                            ctx.beginPath(); ctx.moveTo(end - clip.fade_out / view.span * rect.width, y); ctx.lineTo(end, y + rowHeight); ctx.stroke();
                        }
                        const fadeInX = x + clip.fade_in / view.span * rect.width;
                        const fadeOutX = x + width - clip.fade_out / view.span * rect.width;
                        ctx.fillStyle = "#f2f5f8";
                        ctx.beginPath();
                        ctx.moveTo(fadeInX, y + 1); ctx.lineTo(fadeInX + 8, y + 1); ctx.lineTo(fadeInX, y + 9); ctx.closePath(); ctx.fill();
                        ctx.beginPath();
                        ctx.moveTo(fadeOutX, y + 1); ctx.lineTo(fadeOutX - 8, y + 1); ctx.lineTo(fadeOutX, y + 9); ctx.closePath(); ctx.fill();
                        ctx.strokeStyle = "#f2f5f8";
                        ctx.lineWidth = 2;
                        ctx.beginPath();
                        ctx.moveTo(x, y + 13); ctx.lineTo(x, y + rowHeight - 2);
                        ctx.moveTo(x + width, y + 13); ctx.lineTo(x + width, y + rowHeight - 2);
                        ctx.stroke();
                    });
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
                if (["fade_in", "fade_out", "resize_left", "resize_right"].includes(drag?.mode)) {
                    const canvasBounds = canvas.getBoundingClientRect();
                    const resizing = drag.mode.startsWith("resize_");
                    const state = activeClipState(drag.index, false);
                    const value = resizing ? Number(state?.timeline_duration) : state?.[drag.mode];
                    const label = resizing
                        ? `Clip ${value.toFixed(3)}s`
                        : `${drag.mode === "fade_in" ? "Fade In" : "Fade Out"} ${value.toFixed(3)}s`;
                    ctx.font = "bold 12px sans-serif";
                    const labelWidth = ctx.measureText(label).width + 12;
                    const labelX = Math.max(3, Math.min(rect.width - labelWidth - 3, drag.currentX - canvasBounds.left + 10));
                    const labelY = Math.max(18, drag.currentY - canvasBounds.top - 8);
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
                const rect = canvas.getBoundingClientRect();
                const index = Math.max(0, Math.min(TRACK_COUNT - 1, Math.floor((event.clientY - rect.top) / rect.height * TRACK_COUNT)));
                return laneData(index) || { index, enabled: true, clips: [] };
            }

            function eventTime(event) {
                const rect = canvas.getBoundingClientRect();
                const view = viewBounds();
                return view.start + Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width))) * view.span;
            }

            function clipAt(event) {
                const lane = trackAt(event);
                const rect = canvas.getBoundingClientRect();
                const view = viewBounds();
                const pointerX = event.clientX - rect.left;
                return [...allClipData(lane.index)].reverse().find((clip) => {
                    const x = (clip.offset - view.start) / view.span * rect.width;
                    const width = clipDuration(clip) / view.span * rect.width;
                    return pointerX >= x - 10 && pointerX <= x + width + 10;
                }) || null;
            }

            function handleAt(event) {
                const lane = trackAt(event);
                const data = clipAt(event);
                if (!data) return null;
                const rect = canvas.getBoundingClientRect();
                const view = viewBounds();
                const x = (data.offset - view.start) / view.span * rect.width;
                const trackWidth = clipDuration(data) / view.span * rect.width;
                const pointerX = event.clientX - rect.left;
                const rowHeight = rect.height / TRACK_COUNT;
                const localY = event.clientY - rect.top - lane.index * rowHeight;
                const fadeHandles = [
                    { mode: "fade_in", x: x + data.fade_in / view.span * rect.width },
                    { mode: "fade_out", x: x + trackWidth - data.fade_out / view.span * rect.width },
                ];
                const closestFade = fadeHandles.sort((a, b) => Math.abs(pointerX - a.x) - Math.abs(pointerX - b.x))[0];
                if (localY <= 13 && Math.abs(pointerX - closestFade.x) <= 10) return { ...closestFade, data, lane };
                const resizeHandles = [
                    { mode: "resize_left", x },
                    { mode: "resize_right", x: x + trackWidth },
                ];
                const closestResize = resizeHandles.sort((a, b) => Math.abs(pointerX - a.x) - Math.abs(pointerX - b.x))[0];
                return Math.abs(pointerX - closestResize.x) <= 10 ? { ...closestResize, data, lane } : null;
            }

            function copyClip(index, id) {
                const visual = findClip(index, id);
                const state = clipState(index, id);
                if (!visual) {
                    clipClipboard = null;
                    clipboardVisual = null;
                    status.textContent = "Copy failed: clip state was not found";
                    status.style.color = "#ff7474";
                    return;
                }
                // Always replace the clipboard with a new immutable snapshot.
                // Falling back to the rendered clip prevents a failed lookup
                // from silently reusing a clip copied from another track.
                clipClipboard = clipStateFromData({ ...visual, ...(state || {}) }, index);
                clipboardVisual = { ...visual };
                status.textContent = `Track ${index + 1} clip copied · right-click a timeline position to paste`;
                status.style.color = "#9eabb8";
            }

            function removeClip(index, id) {
                const clips = ensureLaneClips(index);
                const position = clips.findIndex((clip) => clip.id === id);
                if (position < 0) return;
                clips.splice(position, 1);
                visualClips.delete(id);
                if (selectedClip?.trackIndex === index && selectedClip.id === id) selectedClip = null;
                saveSettings();
                refreshRows();
            }

            function pasteClip(index, time, source = clipClipboard, visual = clipboardVisual) {
                if (!source) return;
                const id = newClipId();
                const state = {
                    ...source,
                    id,
                    offset: Math.round(Math.max(0, time) * 1000) / 1000,
                };
                ensureLaneClips(index).push(state);
                if (visual) visualClips.set(id, { ...visual, ...state });
                selectedClip = { trackIndex: index, id };
                syncRowToSelection(index);
                saveSettings();
                refreshRows();
            }

            function duplicateClip(index, id) {
                const source = clipState(index, id);
                const visual = findClip(index, id);
                if (!source || !visual) return;
                pasteClip(index, source.offset + clipDuration(visual), source, visual);
            }

            function closeContextMenu() {
                if (contextMenuDismiss) {
                    document.removeEventListener("pointerdown", contextMenuDismiss);
                    contextMenuDismiss = null;
                }
                contextMenu?.remove();
                contextMenu = null;
            }

            function showContextMenu(event) {
                event.preventDefault();
                event.stopPropagation();
                if (rightDragMoved) {
                    rightDragMoved = false;
                    return;
                }
                closeContextMenu();
                const lane = trackAt(event);
                const clicked = clipAt(event);
                const time = Math.max(0, eventTime(event));
                if (clicked) {
                    ensureLaneClips(lane.index);
                    selectedClip = { trackIndex: lane.index, id: clicked.id };
                    syncRowToSelection(lane.index);
                    refreshRows();
                }
                const actions = [];
                if (clicked) {
                    actions.push(
                        ["Copy", () => copyClip(lane.index, clicked.id)],
                        ["Cut", () => { copyClip(lane.index, clicked.id); removeClip(lane.index, clicked.id); }],
                        ["Duplicate", () => duplicateClip(lane.index, clicked.id)],
                        ["Delete", () => removeClip(lane.index, clicked.id)],
                    );
                }
                if (clipClipboard) actions.push(["Paste", () => pasteClip(lane.index, time)]);
                if (!actions.length) return;

                contextMenu = document.createElement("div");
                contextMenu.style.cssText = "position:fixed;z-index:100000;min-width:130px;padding:4px;background:#20252c;border:1px solid #566170;border-radius:5px;box-shadow:0 6px 18px rgba(0,0,0,.45);font:12px sans-serif";
                actions.forEach(([label, action]) => {
                    const item = document.createElement("button");
                    item.type = "button";
                    item.textContent = label;
                    item.style.cssText = "display:block;width:100%;padding:6px 12px;text-align:left;color:#e7edf3;background:transparent;border:0;border-radius:3px;cursor:pointer";
                    item.addEventListener("mouseenter", () => { item.style.background = "#35404d"; });
                    item.addEventListener("mouseleave", () => { item.style.background = "transparent"; });
                    item.addEventListener("click", (clickEvent) => {
                        clickEvent.stopPropagation();
                        closeContextMenu();
                        action();
                    });
                    contextMenu.append(item);
                });
                document.body.append(contextMenu);
                const menuRect = contextMenu.getBoundingClientRect();
                contextMenu.style.left = `${Math.max(4, Math.min(window.innerWidth - menuRect.width - 4, event.clientX))}px`;
                contextMenu.style.top = `${Math.max(4, Math.min(window.innerHeight - menuRect.height - 4, event.clientY))}px`;
                // Do not remove the menu on its own pointerdown. Removing the
                // clicked button before its click event fires prevents every
                // command, including Copy, from running.
                const openedMenu = contextMenu;
                contextMenuDismiss = (pointerEvent) => {
                    if (openedMenu && !openedMenu.contains(pointerEvent.target)) closeContextMenu();
                };
                setTimeout(() => {
                    if (contextMenuDismiss) document.addEventListener("pointerdown", contextMenuDismiss);
                }, 0);
            }

            canvas.addEventListener("pointerdown", (event) => {
                const lane = trackAt(event);
                if (event.button === 2) {
                    const view = viewBounds();
                    rightDragMoved = false;
                    drag = { mode: "pan", x: event.clientX, start: view.start, span: view.span };
                    canvas.setPointerCapture(event.pointerId);
                    canvas.style.cursor = "grabbing";
                    return;
                }
                if (event.button !== 0) return;
                const clicked = clipAt(event);
                if (!clicked) {
                    selectedClip = null;
                    draw();
                    return;
                }
                // Keep the time scale fixed while editing. When Show All is
                // active, changing a clip edge or position changes the full
                // timeline extent; continuously refitting that extent makes a
                // fixed-length clip appear to shrink near either canvas edge.
                const editView = viewBounds();
                viewStart = editView.start;
                viewEnd = editView.end;
                showFullTimeline = false;
                const handle = handleAt(event);
                ensureLaneClips(lane.index);
                const state = clipState(lane.index, clicked.id);
                if (!state) return;
                selectedClip = { trackIndex: lane.index, id: state.id };
                syncRowToSelection(lane.index);
                if (handle) {
                    drag = {
                        mode: handle.mode,
                        index: lane.index,
                        clipId: state.id,
                        x: event.clientX,
                        initial: handle.mode.startsWith("resize_")
                            ? clipDuration(clicked)
                            : state[handle.mode],
                        initialOffset: state.offset,
                        initialSourceStart: clipSourceStart(state),
                        span: editView.span,
                        currentX: event.clientX,
                        currentY: event.clientY,
                    };
                } else {
                    drag = {
                        mode: "offset",
                        index: lane.index,
                        clipId: state.id,
                        x: event.clientX,
                        offset: state.offset,
                        span: editView.span,
                    };
                }
                canvas.setPointerCapture(event.pointerId);
                canvas.style.cursor = handle ? "ew-resize" : "grabbing";
            });
            canvas.addEventListener("pointermove", (event) => {
                if (!drag) {
                    canvas.style.cursor = handleAt(event) ? "ew-resize" : "grab";
                    return;
                }
                const width = canvas.getBoundingClientRect().width;
                const view = viewBounds();
                if (drag.mode === "pan") {
                    if (Math.abs(event.clientX - drag.x) >= 4) rightDragMoved = true;
                    const extent = timelineExtent();
                    const nextStart = drag.start - (event.clientX - drag.x) / width * drag.span;
                    viewStart = Math.max(extent.start, Math.min(extent.end - drag.span, nextStart));
                    viewEnd = viewStart + drag.span;
                } else if (drag.mode === "offset") {
                    const state = clipState(drag.index, drag.clipId);
                    state.offset = Math.round((drag.offset + (event.clientX - drag.x) / width * drag.span) * 1000) / 1000;
                    rowElements[drag.index].offset.value = String(state.offset);
                } else if (drag.mode === "resize_right") {
                    const state = clipState(drag.index, drag.clipId);
                    const delta = (event.clientX - drag.x) / width * drag.span;
                    state.timeline_duration = Math.round(Math.max(0.001, Math.min(86400, drag.initial + delta)) * 1000) / 1000;
                    clampFades(drag.index, state.timeline_duration);
                    drag.currentX = event.clientX;
                    drag.currentY = event.clientY;
                } else if (drag.mode === "resize_left") {
                    const state = clipState(drag.index, drag.clipId);
                    const requested = (event.clientX - drag.x) / width * drag.span;
                    const minimumDelta = Math.max(
                        -(86400 - drag.initial),
                        -86400 - drag.initialOffset,
                        -86400 - drag.initialSourceStart,
                    );
                    const maximumDelta = Math.min(
                        drag.initial - 0.001,
                        86400 - drag.initialOffset,
                        86400 - drag.initialSourceStart,
                    );
                    const delta = Math.max(minimumDelta, Math.min(maximumDelta, requested));
                    state.offset = Math.round((drag.initialOffset + delta) * 1000) / 1000;
                    state.source_start = Math.round((drag.initialSourceStart + delta) * 1000) / 1000;
                    state.timeline_duration = Math.round((drag.initial - delta) * 1000) / 1000;
                    clampFades(drag.index, state.timeline_duration);
                    rowElements[drag.index].offset.value = String(state.offset);
                    drag.currentX = event.clientX;
                    drag.currentY = event.clientY;
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
                const handle = handleAt(event);
                if (!handle || !["fade_in", "fade_out"].includes(handle.mode)) return;
                selectedClip = { trackIndex: handle.lane.index, id: handle.data.id };
                ensureLaneClips(handle.lane.index);
                setFade(handle.lane.index, handle.mode, 0);
                saveSettings();
                event.preventDefault();
                event.stopPropagation();
            });
            canvas.addEventListener("contextmenu", showContextMenu);
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

            chainCallback(node, "onConnectionsChange", function (type) {
                // LiteGraph uses 1 for input connections and 2 for outputs.
                // Changing an Audio Out connection must not clear the Mixer.
                if (type === 1) clearConnectionRenderState();
                else refreshRows();
            });
            chainCallback(node, "onConfigure", function () {
                setTimeout(restoreVisibleSettings, 0);
                schedulePanelFit();
            });
            chainCallback(node, "onExecuted", function (message) {
                try {
                    const value = message?.alice_lab_audio_tools_mixer?.[0] ?? message?.alice_lab_audio_tools_mixer;
                    renderData = typeof value === "string" ? JSON.parse(value) : value;
                    // Render results are now authoritative. Discard transient
                    // visuals and selection from the preceding execution so a
                    // stale clip is not left highlighted or used for hit tests.
                    visualClips.clear();
                    selectedClip = null;
                    drag = null;
                    closeContextMenu();
                    pruneDisconnectedClips();
                    if (showFullTimeline || viewEnd <= viewStart) {
                        const extent = timelineExtent();
                        viewStart = extent.start;
                        viewEnd = extent.end;
                    } else {
                        const view = viewBounds();
                        viewStart = view.start;
                        viewEnd = view.end;
                    }
                    refreshRows();
                } catch (error) {
                    status.textContent = `Mixer display error: ${error.message}`;
                }
            });
            chainCallback(masterWidget, "callback", draw);
            chainCallback(clippingWidget, "callback", draw);
            const resizeObserver = new ResizeObserver(draw);
            resizeObserver.observe(canvas);
            chainCallback(node, "onRemoved", function () {
                closeContextMenu();
                resizeObserver.disconnect();
            });
            setTimeout(refreshRows, 0);
        });
    },
});

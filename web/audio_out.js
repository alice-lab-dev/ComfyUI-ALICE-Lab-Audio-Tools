import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { drawTimeAxis } from "./time_axis.js";

function chainCallback(target, key, callback) {
    const original = target[key];
    target[key] = function (...args) {
        const result = original?.apply(this, args);
        callback.apply(this, args);
        return result;
    };
}

app.registerExtension({
    name: "ALICE_Lab.AudioOut",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "AliceLabOutputWaveform") return;

        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            const node = this;
            const colorWidget = node.widgets.find((widget) => widget.name === "waveform_color");
            let data = { duration: 0, sample_rate: 0, channels: 0, peak: 0, peaks: [] };

            // ComfyUI/LiteGraph treats converted-widget as a non-drawing widget.
            // A generic "hidden" type can still be painted by some frontends.
            colorWidget.type = "converted-widget";
            colorWidget.hidden = true;
            colorWidget.computeSize = () => [0, -4];

            const minimumPanelHeight = 190;
            let panelHeight = minimumPanelHeight;
            const root = document.createElement("div");
            root.style.cssText = `height:${minimumPanelHeight}px;display:flex;flex-direction:column;gap:5px;padding:5px;box-sizing:border-box;background:#15191f;color:#dce3ea;font:12px sans-serif;overflow:hidden`;
            const header = document.createElement("div");
            header.style.cssText = "display:flex;align-items:center;gap:10px;padding:5px 7px;background:#242830;border:1px solid #39424e;border-radius:6px;flex:none";
            const title = document.createElement("strong");
            title.textContent = "Audio preview";
            const status = document.createElement("span");
            status.style.cssText = "margin-left:auto;color:#9eabb8";
            status.textContent = "Run to load audio";
            const color = document.createElement("input");
            color.type = "color";
            color.value = /^#[0-9a-f]{6}$/i.test(colorWidget.value) ? colorWidget.value : "#67c5e8";
            color.title = "Waveform color";
            color.style.cssText = "width:25px;height:22px;padding:0;border:1px solid #48515e;border-radius:4px;background:transparent;cursor:pointer;flex:none";
            color.addEventListener("input", () => {
                colorWidget.value = color.value;
                colorWidget.callback?.(colorWidget.value);
                node.graph?.setDirtyCanvas(true, true);
                draw();
            });
            const autoColor = document.createElement("button");
            autoColor.textContent = "Auto";
            autoColor.title = "Inherit the color from ALICE Audio Mixer";
            autoColor.style.cssText = "height:22px;padding:1px 6px;border:1px solid #48515e;border-radius:4px;color:#dce3ea;background:#303641;cursor:pointer;flex:none";
            autoColor.addEventListener("click", () => {
                colorWidget.value = "auto";
                colorWidget.callback?.(colorWidget.value);
                syncAppearance();
                node.graph?.setDirtyCanvas(true, true);
                draw();
            });
            header.append(title, status, autoColor, color);

            const player = document.createElement("audio");
            player.controls = true;
            player.preload = "metadata";
            player.style.cssText = "display:none;width:100%;height:32px;flex:none";

            const canvas = document.createElement("canvas");
            canvas.tabIndex = 0;
            canvas.style.cssText = "width:100%;height:auto;min-height:55px;flex:1 1 55px;background:#101419;border:1px solid #39424e;box-sizing:border-box;cursor:pointer;touch-action:none";
            root.append(header, player, canvas);

            function mixerAppearance() {
                const input = node.inputs?.find((item) => item.name === "audio");
                const link = input?.link != null ? node.graph?.links?.[input.link] : null;
                const source = link ? node.graph?.getNodeById(link.origin_id) : null;
                if (!source || source.type !== "AliceLabAudioMixer" || link.origin_slot < 1) return null;
                const settingsWidget = source.widgets?.find((item) => item.name === "track_settings");
                try {
                    const settings = JSON.parse(settingsWidget?.value || "[]");
                    const track = settings[link.origin_slot - 1];
                    if (!track) return null;
                    return {
                        name: String(track.name || `Track ${link.origin_slot}`),
                        color: /^#[0-9a-f]{6}$/i.test(track.color) ? track.color : "#67c5e8",
                    };
                } catch {
                    return null;
                }
            }

            function syncAppearance() {
                const inherited = mixerAppearance();
                const inheritedName = inherited?.name || data.track_name || "";
                title.textContent = inheritedName ? `Audio preview · ${inheritedName}` : "Audio preview";
                if (colorWidget.value === "auto") {
                    const inheritedColor = inherited?.color || data.waveform_color;
                    if (/^#[0-9a-f]{6}$/i.test(inheritedColor)) color.value = inheritedColor;
                }
            }

            function draw() {
                syncAppearance();
                // DOM widgets can be CSS-transformed with the ComfyUI canvas.
                // Draw in the untransformed CSS coordinate space so playback
                // redraws do not apply the node zoom to the bitmap twice.
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
                const plotLeft = 43;
                const plotRight = Math.max(plotLeft + 1, rect.width - 8);
                const plotWidth = plotRight - plotLeft;
                ctx.font = "9px sans-serif";
                ctx.textAlign = "right";
                ctx.textBaseline = "middle";
                for (const [value, label] of [[1, "+1 FS"], [0.5, "+0.5"], [0, "0"], [-0.5, "−0.5"], [-1, "−1 FS"]]) {
                    const y = rect.height / 2 - value * rect.height * 0.42;
                    ctx.strokeStyle = value === 0 ? "#48515c" : "#29313a";
                    ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(plotRight, y); ctx.stroke();
                    ctx.fillStyle = "#8795a3";
                    ctx.fillText(label, plotLeft - 4, y);
                }
                ctx.textAlign = "left";
                ctx.textBaseline = "alphabetic";
                if (data.peaks?.length) {
                    ctx.strokeStyle = color.value;
                    ctx.beginPath();
                    for (let x = plotLeft; x < plotRight; x++) {
                        const peak = data.peaks[Math.min(data.peaks.length - 1, Math.floor((x - plotLeft) / plotWidth * data.peaks.length))] || 0;
                        const amplitude = Math.min(1, peak) * rect.height * 0.42;
                        ctx.moveTo(x + 0.5, rect.height / 2 - amplitude);
                        ctx.lineTo(x + 0.5, rect.height / 2 + amplitude);
                    }
                    ctx.stroke();
                }
                if (data.duration) {
                    drawTimeAxis(ctx, {
                        left: plotLeft,
                        right: plotRight,
                        top: 0,
                        bottom: rect.height,
                        start: 0,
                        end: data.duration,
                    });
                }
                if (data.duration && Number.isFinite(player.currentTime)) {
                    const x = Math.max(plotLeft, Math.min(plotRight, plotLeft + player.currentTime / data.duration * plotWidth));
                    ctx.strokeStyle = "#ffd166";
                    ctx.lineWidth = 2;
                    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, rect.height); ctx.stroke();
                }
            }

            canvas.addEventListener("pointerdown", (event) => {
                if (!data.duration || !player.src) return;
                const rect = canvas.getBoundingClientRect();
                const plotLeft = 43;
                const plotWidth = Math.max(1, rect.width - plotLeft - 8);
                const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left - plotLeft) / plotWidth));
                player.currentTime = ratio * data.duration;
                draw();
            });
            player.addEventListener("timeupdate", draw);
            player.addEventListener("seeked", draw);
            player.addEventListener("ended", draw);

            const widget = node.addDOMWidget("alice_lab_audio_out_ui", "ALICE_LAB_AUDIO_OUT", root, {
                serialize: false,
                hideOnZoom: true,
            });
            // Report the minimum only.  Any height the user gives the node is
            // consumed by this DOM panel, so the waveform canvas grows with it
            // without feeding a new size back into LiteGraph.
            widget.computeSize = (width) => [width, minimumPanelHeight];
            function fitPanel() {
                const computedHeight = Number(node.computeSize?.()[1]) || minimumPanelHeight;
                const chromeHeight = Math.max(0, computedHeight - minimumPanelHeight);
                panelHeight = Math.max(minimumPanelHeight, node.size[1] - chromeHeight);
                root.style.width = `${Math.max(120, node.size[0] - 20)}px`;
                root.style.height = `${panelHeight}px`;
                draw();
            }
            chainCallback(node, "onResize", () => requestAnimationFrame(fitPanel));
            node.setSize([Math.max(node.size[0], 590), Math.max(node.size[1], 270)]);

            chainCallback(node, "onConfigure", function () {
                setTimeout(() => {
                    fitPanel();
                    color.value = /^#[0-9a-f]{6}$/i.test(colorWidget.value)
                        ? colorWidget.value
                        : (/^#[0-9a-f]{6}$/i.test(data.waveform_color) ? data.waveform_color : "#67c5e8");
                    syncAppearance();
                    draw();
                }, 0);
            });
            requestAnimationFrame(fitPanel);
            chainCallback(node, "onConnectionsChange", function () {
                setTimeout(() => {
                    syncAppearance();
                    draw();
                }, 0);
            });

            chainCallback(node, "onExecuted", function (message) {
                try {
                    const payload = message?.alice_lab_audio_out?.[0] ?? message?.alice_lab_audio_out;
                    data = typeof payload === "string" ? JSON.parse(payload) : payload;
                    syncAppearance();
                    const audio = message?.audio?.[0];
                    if (!audio) throw new Error("No preview file was returned");
                    const query = new URLSearchParams({
                        filename: audio.filename,
                        type: audio.type || "temp",
                        subfolder: audio.subfolder || "",
                        cache: Date.now().toString(),
                    });
                    player.pause();
                    player.src = api.apiURL(`/view?${query}`);
                    player.style.display = "block";
                    player.load();
                    const peakDb = data.peak > 0 ? 20 * Math.log10(data.peak) : -Infinity;
                    status.textContent = `${data.duration.toFixed(3)}s · ${data.sample_rate} Hz · ${data.channels}ch · peak ${Number.isFinite(peakDb) ? peakDb.toFixed(1) : "−∞"} dBFS`;
                    status.style.color = data.peak > 1 ? "#ff7474" : "#9eabb8";
                    draw();
                } catch (error) {
                    status.textContent = `Audio Out: ${error.message}`;
                }
            });
            new ResizeObserver(draw).observe(canvas);
        });
    },
});

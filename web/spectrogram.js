import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "alice_lab.audio_spectrogram",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "AliceLabSpectrogram") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                onNodeCreated?.apply(this, arguments);

                const node = this;
                let data = null; // The JSON payload from backend
                let width = 320;
                let height = 200;

                // Create DOM structure
                const root = document.createElement("div");
                root.style.cssText = "display:flex;flex-direction:column;gap:8px;min-width:0;overflow:hidden;background:#181e25;padding:12px;border-radius:8px;box-sizing:border-box;";
                
                const heading = document.createElement("div");
                heading.style.cssText = "font-weight:bold;color:#e2e8f0;font-size:13px;display:flex;justify-content:space-between;";
                heading.innerHTML = '<span>Spectrogram Analysis</span><span class="spec-time">00:00.000</span>';
                const timeSpan = heading.querySelector('.spec-time');

                const spectrumContainer = document.createElement("div");
                spectrumContainer.style.cssText = "position:relative;width:100%;min-width:0;min-height:0;flex:1 1 200px;overflow:hidden;";
                const spectrum = document.createElement("canvas");
                // Absolute positioning keeps the canvas bitmap's intrinsic
                // dimensions out of the flex minimum-size calculation. Without
                // this, each HiDPI redraw can make the graph taller again.
                spectrum.style.cssText = "position:absolute;inset:0;display:block;width:100%;height:100%;min-width:0;min-height:0;background:#080b10;border:1px solid #39424e;box-sizing:border-box;cursor:crosshair;touch-action:none";
                
                const spectrumTooltip = document.createElement("div");
                spectrumTooltip.style.cssText = "position:absolute;background:rgba(16,20,25,0.9);color:#e2e8f0;padding:4px 8px;border-radius:4px;font:11px sans-serif;pointer-events:none;display:none;z-index:100;border:1px solid #39424e;white-space:nowrap;box-shadow:0 4px 6px rgba(0,0,0,0.3);";
                
                spectrumContainer.append(spectrum, spectrumTooltip);
                root.append(heading, spectrumContainer);

                // Add DOM Widget to node
                const widget = node.addDOMWidget("audio_spectrogram", "audio_spectrogram", root, {
                    serialize: false,
                    hideOnZoom: false,
                });
                const minimumPanelHeight = 260;
                widget.computeSize = (targetWidth) => {
                    return [
                        Math.max(120, targetWidth || width),
                        minimumPanelHeight
                    ];
                };
                let panelHeight = minimumPanelHeight;
                let fitPending = false;
                function fitPanel() {
                    fitPending = false;
                    root.style.width = `${Math.max(120, node.size[0] - 20)}px`;
                    const computedHeight = Number(node.computeSize?.()[1]) || minimumPanelHeight;
                    const chromeHeight = Math.max(0, computedHeight - minimumPanelHeight);
                    panelHeight = Math.max(minimumPanelHeight, node.size[1] - chromeHeight);
                    root.style.height = `${panelHeight}px`;
                    draw();
                    node.graph?.setDirtyCanvas(true, true);
                }

                function schedulePanelFit() {
                    if (fitPending) return;
                    fitPending = true;
                    requestAnimationFrame(fitPanel);
                }

                function chainCallback(object, property, callback) {
                    const original = object[property];
                    object[property] = function () {
                        original?.apply(this, arguments);
                        return callback.apply(this, arguments);
                    };
                }

                widget.updateData = function(newData) {
                    data = newData;
                    draw();
                };

                chainCallback(node, "onResize", function (size) {
                    width = size[0];
                    height = size[1];
                    schedulePanelFit();
                });
                chainCallback(node, "onConfigure", schedulePanelFit);
                
                // Initialize node size to fit the widget properly on creation
                setTimeout(() => {
                    node.setSize([Math.max(node.size[0], 340), Math.max(node.size[1], minimumPanelHeight + 80)]);
                    // A saved workflow can restore an old partial selection.
                    // Spectrogram ranges are session-local UI state, so always
                    // start a newly initialized node with the full-range request
                    // sentinel. The first execution replaces end=0 with the
                    // audio's actual duration returned by the backend.
                    updateWidgets(0, 0);
                }, 0);
                schedulePanelFit();

                // State for drawing and interaction
                const spectrumCtx = spectrum.getContext("2d");
                
                function formatTime(s) {
                    const m = Math.floor(s / 60);
                    return `${m.toString().padStart(2, "0")}:${(s % 60).toFixed(3).padStart(6, "0")}`;
                }

                function draw() {
                    // ComfyUI applies a CSS transform to DOM widgets while the
                    // graph is zoomed. Draw in the untransformed CSS coordinate
                    // space so node zoom is not applied to the bitmap twice.
                    const rect = {
                        width: spectrum.clientWidth,
                        height: spectrum.clientHeight,
                    };
                    const dpr = window.devicePixelRatio || 1;
                    spectrum.width = Math.max(1, Math.round(rect.width * dpr));
                    spectrum.height = Math.max(1, Math.round(rect.height * dpr));
                    spectrumCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

                    if (!data || !data.spectrum || !data.spectrum.matrix) {
                        spectrumCtx.fillStyle = "#080b10";
                        spectrumCtx.fillRect(0, 0, rect.width, rect.height);
                        return;
                    }

                    const matrix = data.spectrum.matrix;
                    const bins = matrix.length;
                    const cols = matrix[0].length;
                    const w = rect.width / cols;
                    const h = rect.height / bins;
                    const minDb = data.spectrum_min_db ?? -100;
                    const maxDb = data.spectrum_max_db ?? 0;

                    for (let x = 0; x < cols; x++) {
                        for (let y = 0; y < bins; y++) {
                            const db = matrix[y][x];
                            let ratio = (db - minDb) / (maxDb - minDb);
                            if (ratio < 0) ratio = 0;
                            if (ratio > 1) ratio = 1;

                            // HSL scale identical to the backend
                            const hue = (1.0 - ratio) * 240;
                            spectrumCtx.fillStyle = `hsl(${hue}, 70%, ${ratio * 100}%)`;
                            // Draw from bottom to top
                            spectrumCtx.fillRect(x * w, rect.height - (y + 1) * h, Math.ceil(w), Math.ceil(h));
                        }
                    }
                    
                    timeSpan.textContent = formatTime(data.duration || 0);
                }

                // Redraw after flex layout has settled. The canvas is absolute,
                // so updating its backing bitmap cannot resize the container.
                const spectrumResizeObserver = new ResizeObserver(draw);
                spectrumResizeObserver.observe(spectrumContainer);
                chainCallback(node, "onRemoved", () => spectrumResizeObserver.disconnect());

                // Interactive Range Selection
                let isDragging = false;
                let dragStartX = 0;

                function updateWidgets(startSec, endSec) {
                    const startWidget = node.widgets.find(w => w.name === "start_seconds");
                    const endWidget = node.widgets.find(w => w.name === "end_seconds");
                    function updateWidget(target, value) {
                        if (!target || !Number.isFinite(value)) return;
                        const rounded = Number(value.toFixed(3));
                        target.value = rounded;
                        target.callback?.(rounded);
                    }
                    updateWidget(startWidget, startSec);
                    updateWidget(endWidget, endSec);
                    app.graph.setDirtyCanvas(true);
                }

                function eventPoint(e) {
                    const bounds = spectrum.getBoundingClientRect();
                    const localWidth = Math.max(1, spectrum.clientWidth);
                    const localHeight = Math.max(1, spectrum.clientHeight);
                    // Mouse coordinates are in transformed viewport pixels,
                    // while tooltip offsets are untransformed CSS pixels.
                    // Convert them before using the values for either data
                    // lookup or absolute positioning inside the container.
                    const x = (e.clientX - bounds.left) * localWidth / Math.max(1, bounds.width);
                    const y = (e.clientY - bounds.top) * localHeight / Math.max(1, bounds.height);
                    return {
                        x: Math.max(0, Math.min(x, localWidth)),
                        y: Math.max(0, Math.min(y, localHeight)),
                        width: localWidth,
                        height: localHeight,
                    };
                }

                function getEventTime(e) {
                    if (!data) return 0;
                    const point = eventPoint(e);
                    return (point.x / point.width) * (data.duration || 0);
                }

                spectrum.addEventListener("mousedown", (e) => {
                    if (e.button !== 0) return;
                    isDragging = true;
                    dragStartX = eventPoint(e).x;
                    e.preventDefault();
                    // Reset widgets for a new selection point
                    const t = getEventTime(e);
                    updateWidgets(t, t);
                });

                window.addEventListener("mousemove", (e) => {
                    if (!isDragging || !data) return;
                    const point = eventPoint(e);
                    const startX = dragStartX;
                    const currentX = point.x;
                    
                    let startT = (startX / point.width) * data.duration;
                    let endT = (currentX / point.width) * data.duration;
                    
                    if (startT > endT) {
                        const temp = startT;
                        startT = endT;
                        endT = temp;
                    }
                    updateWidgets(startT, endT);
                });

                window.addEventListener("mouseup", () => {
                    isDragging = false;
                });

                // Hover Tooltips
                spectrum.addEventListener("mousemove", (e) => {
                    if (!data || !data.spectrum || !data.spectrum.matrix || isDragging) return;
                    const point = eventPoint(e);
                    const { x, y, width, height } = point;
                    
                    const cols = data.spectrum.matrix[0].length;
                    const bins = data.spectrum.matrix.length;
                    
                    const col = Math.floor((x / width) * cols);
                    const bin = Math.floor((1.0 - (y / height)) * bins);
                    
                    if (col >= 0 && col < cols && bin >= 0 && bin < bins) {
                        const db = data.spectrum.matrix[bin][col];
                        const time = (col / cols) * data.duration;
                        const freq = (bin / bins) * (data.sample_rate / 2);
                        
                        spectrumTooltip.style.display = "block";
                        spectrumTooltip.innerHTML = `
                            <div style="font-weight:600">${db.toFixed(1)} dBFS</div>
                            <div style="color:#8fa0b1">${formatTime(time)}</div>
                            <div style="color:#8fa0b1">${Math.round(freq)} Hz</div>
                        `;
                        
                        // Positioning
                        const tooltipWidth = spectrumTooltip.offsetWidth;
                        const tooltipHeight = spectrumTooltip.offsetHeight;
                        let left = x + 12;
                        let top = y + 12;
                        if (left + tooltipWidth > width) left = x - tooltipWidth - 12;
                        if (top + tooltipHeight > height) top = y - tooltipHeight - 12;
                        
                        spectrumTooltip.style.left = `${left}px`;
                        spectrumTooltip.style.top = `${top}px`;
                    }
                });

                spectrum.addEventListener("mouseleave", () => {
                    spectrumTooltip.style.display = "none";
                });
            };

            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExecuted?.apply(this, arguments);
                if (message?.alice_lab_audio_spectrogram?.[0]) {
                    try {
                        const payload = JSON.parse(message.alice_lab_audio_spectrogram[0]);
                        const widget = this.widgets?.find((w) => w.name === "audio_spectrogram");
                        const startWidget = this.widgets?.find((w) => w.name === "start_seconds");
                        const endWidget = this.widgets?.find((w) => w.name === "end_seconds");
                        const actualStart = Number(payload.start_seconds);
                        const actualEnd = Number(payload.end_seconds);
                        const totalDuration = Number(payload.total_duration);
                        const requestedFullRange = Boolean(
                            startWidget && endWidget
                            && Number(startWidget.value) === 0
                            && Number(endWidget.value) <= 0
                        );
                        const displayedStart = requestedFullRange ? 0 : actualStart;
                        const displayedEnd = requestedFullRange ? totalDuration : actualEnd;
                        function updateWidget(target, value) {
                            if (!target || !Number.isFinite(value)) return;
                            const rounded = Number(value.toFixed(3));
                            target.value = rounded;
                            target.callback?.(rounded);
                        }
                        updateWidget(startWidget, displayedStart);
                        updateWidget(endWidget, displayedEnd);
                        this.graph?.setDirtyCanvas(true, true);
                        if (widget && widget.element) {
                            // Find the node instance by looping or directly setting
                            // Actually we attached `draw` and `data` in onNodeCreated context, 
                            // but how to access it from onExecuted? 
                            // We can trigger an event or attach `setSpectrogramData` to the widget
                            if(widget.updateData) {
                                widget.updateData(payload);
                            }
                        }
                    } catch (e) {
                        console.error("Spectrogram parse error:", e);
                    }
                }
            };
        }
    }
});

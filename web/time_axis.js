export function timelineTickStep(span, width) {
    const target = span / Math.max(2, width / 90);
    const magnitude = 10 ** Math.floor(Math.log10(Math.max(target, 0.001)));
    const normalized = target / magnitude;
    const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    return factor * magnitude;
}

export function formatTimelineTime(seconds, span) {
    const value = Math.abs(seconds) < 0.0005 ? 0 : seconds;
    const sign = value < 0 ? "−" : "";
    const absolute = Math.abs(value);
    if (absolute >= 3600) {
        const hours = Math.floor(absolute / 3600);
        const minutes = Math.floor((absolute % 3600) / 60);
        const secs = Math.floor(absolute % 60);
        return `${sign}${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
    }
    if (absolute >= 60) {
        const minutes = Math.floor(absolute / 60);
        const secs = Math.floor(absolute % 60);
        return `${sign}${minutes}:${String(secs).padStart(2, "0")}`;
    }
    return `${value.toFixed(span < 10 ? 2 : 1)}s`;
}

export function drawTimeAxis(context, options) {
    const { left, right, top, bottom, start, end } = options;
    const width = Math.max(1, right - left);
    const span = Math.max(0.001, end - start);
    const step = timelineTickStep(span, width);
    const first = Math.ceil(start / step) * step;
    const drawGrid = options.drawGrid !== false;
    const drawLabels = options.drawLabels !== false;
    const labelY = options.labelY ?? bottom - 2;
    context.font = "10px sans-serif";
    context.textAlign = "center";
    context.textBaseline = "bottom";
    for (let time = first, count = 0; time <= end + step * 0.001 && count < 200; time += step, count++) {
        const x = left + (time - start) / span * width;
        if (drawGrid) {
            context.strokeStyle = "rgba(135, 149, 163, 0.28)";
            context.lineWidth = 1;
            context.beginPath();
            context.moveTo(x, top);
            context.lineTo(x, bottom);
            context.stroke();
        }
        if (drawLabels) {
            const label = formatTimelineTime(time, span);
            const labelWidth = context.measureText(label).width + 6;
            const labelX = Math.max(left + labelWidth / 2, Math.min(right - labelWidth / 2, x));
            context.fillStyle = "rgba(8, 10, 13, 0.82)";
            context.fillRect(labelX - labelWidth / 2, labelY - 12, labelWidth, 13);
            context.fillStyle = "#c5ced7";
            context.fillText(label, labelX, labelY);
        }
    }
    context.textAlign = "left";
    context.textBaseline = "alphabetic";
}

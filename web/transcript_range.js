import { app } from "../../../scripts/app.js";

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
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const secs = value % 60;
    return `[${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${secs
        .toFixed(3)
        .padStart(6, "0")}]`;
}

function truncate(text, maxLength = 120) {
    const value = String(text ?? "").replace(/\r?\n/g, " ⏎ ");
    if (value.length <= maxLength) return value;
    return `${value.slice(0, maxLength - 1)}…`;
}

function optionLabel(segment, index) {
    return `${index + 1}. ${formatTime(segment.start)} ${truncate(segment.text)}`;
}

function hideBackingWidget(widget) {
    if (!widget) return;
    widget.computeSize = () => [0, -4];
    widget.hidden = true;
}

app.registerExtension({
    name: "ALICE_Lab.TranscriptRangeSelector",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "AliceLabTranscriptRangeSelector") return;

        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            const node = this;
            const startBacking = node.widgets?.find(
                (widget) => widget.name === "start_segment"
            );
            const endBacking = node.widgets?.find(
                (widget) => widget.name === "end_segment"
            );
            if (!startBacking || !endBacking) return;

            hideBackingWidget(startBacking);
            hideBackingWidget(endBacking);

            let segments = [];
            let labels = ["Run once to load transcript"];

            const startCombo = node.addWidget(
                "combo",
                "Start",
                labels[0],
                (value) => {
                    const index = labels.indexOf(value);
                    if (index >= 0) {
                        startBacking.value = index;
                        if (endBacking.value < index) {
                            endBacking.value = index;
                            endCombo.value = labels[index];
                        }
                    }
                },
                { values: () => labels }
            );

            const endCombo = node.addWidget(
                "combo",
                "End",
                labels[0],
                (value) => {
                    const index = labels.indexOf(value);
                    if (index >= 0) {
                        endBacking.value = index;
                        if (index < startBacking.value) {
                            startBacking.value = index;
                            startCombo.value = labels[index];
                        }
                    }
                },
                { values: () => labels }
            );

            function applySelection() {
                if (!segments.length) {
                    startCombo.value = labels[0];
                    endCombo.value = labels[0];
                    return;
                }

                const startIndex = Math.max(
                    0,
                    Math.min(segments.length - 1, Number(startBacking.value) || 0)
                );
                const endIndex = Math.max(
                    startIndex,
                    Math.min(segments.length - 1, Number(endBacking.value) || 0)
                );

                startBacking.value = startIndex;
                endBacking.value = endIndex;
                startCombo.value = labels[startIndex];
                endCombo.value = labels[endIndex];
            }

            node._aliceTranscriptRangeApplyPayload = (rawPayload) => {
                let payload = rawPayload;
                if (Array.isArray(payload)) payload = payload[0];
                if (typeof payload === "string") {
                    try {
                        payload = JSON.parse(payload);
                    } catch {
                        return;
                    }
                }

                const incoming = Array.isArray(payload?.segments)
                    ? payload.segments
                    : [];
                segments = incoming;
                labels = incoming.length
                    ? incoming.map(optionLabel)
                    : ["Transcript contains no segments"];

                startCombo.options.values = () => labels;
                endCombo.options.values = () => labels;

                if (
                    Number.isInteger(payload?.start_segment) &&
                    payload.start_segment >= 0 &&
                    payload.start_segment < segments.length
                ) {
                    startBacking.value = payload.start_segment;
                } else if (segments.length) {
                    startBacking.value = 0;
                }

                if (
                    Number.isInteger(payload?.end_segment) &&
                    payload.end_segment >= 0 &&
                    payload.end_segment < segments.length
                ) {
                    endBacking.value = payload.end_segment;
                } else if (segments.length) {
                    endBacking.value = Math.max(0, Number(startBacking.value) || 0);
                }

                if (typeof payload?.transcript_fingerprint === "string") {
                    node.properties ??= {};
                    node.properties.alice_transcript_fingerprint =
                        payload.transcript_fingerprint;
                }

                applySelection();
                node.setDirtyCanvas(true, true);
            };

            chainCallback(node, "onConfigure", function () {
                applySelection();
            });

            const minimum = node.computeSize();
            node.size[0] = Math.max(node.size[0], 420, minimum[0]);
        });

        chainCallback(nodeType.prototype, "onExecuted", function (message) {
            const payload = message?.alice_lab_transcript_range;
            if (payload !== undefined) {
                this._aliceTranscriptRangeApplyPayload?.(payload);
            }
        });
    },
});

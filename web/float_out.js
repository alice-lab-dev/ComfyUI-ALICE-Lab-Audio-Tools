import { app } from "../../../scripts/app.js";

function chainCallback(target, key, callback) {
    const original = target[key];
    target[key] = function (...args) {
        const result = original?.apply(this, args);
        callback.apply(this, args);
        return result;
    };
}

app.registerExtension({
    name: "ALICE_Lab.FloatOut",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "AliceLabOutputFloat") return;

        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            const node = this;
            const labelWidget = node.widgets.find((widget) => widget.name === "label");
            const precisionWidget = node.widgets.find((widget) => widget.name === "precision");
            for (const nativeWidget of [labelWidget, precisionWidget]) {
                if (!nativeWidget) continue;
                nativeWidget.hidden = true;
                nativeWidget.draw = () => {};
                nativeWidget.computeSize = () => [0, -4];
            }

            function restoreValueInputLabel() {
                const valueInput = node.inputs?.find((input) => input.name === "value" || input.type === "FLOAT");
                if (!valueInput) return;
                valueInput.name = "value";
                valueInput.label = "value";
            }
            restoreValueInputLabel();
            chainCallback(node, "onConnectionsChange", restoreValueInputLabel);

            function restoreSerializedSettings(info) {
                restoreValueInputLabel();
                const saved = Array.isArray(info?.widgets_values) ? info.widgets_values : [];
                // Original workflows stored [value, label, precision]. Current
                // workflows store [label, precision]. onConfigure runs after
                // LiteGraph assigns widget values, so repair the live widgets.
                let savedLabel;
                let savedPrecision;
                if (typeof saved[0] === "number" && typeof saved[1] === "string") {
                    savedLabel = saved[1];
                    savedPrecision = saved[2];
                } else {
                    savedLabel = saved[0];
                    savedPrecision = saved[1];
                }
                const nextLabel = typeof savedLabel === "string" ? savedLabel : "Value";
                const numericPrecision = Number(savedPrecision);
                const nextPrecision = Number.isFinite(numericPrecision)
                    ? Math.max(0, Math.min(12, Math.round(numericPrecision)))
                    : 6;
                labelWidget.value = nextLabel;
                precisionWidget.value = nextPrecision;
                labelInput.value = nextLabel;
                precisionInput.value = String(nextPrecision);
            }
            chainCallback(node, "onConfigure", restoreSerializedSettings);

            let settingsExpanded = false;
            const root = document.createElement("div");
            root.style.cssText = "height:82px;display:flex;flex-direction:column;padding:8px 12px;box-sizing:border-box;background:#15191f;border:1px solid #39424e;border-radius:6px;color:#dce3ea;font:12px sans-serif;overflow:hidden";
            const header = document.createElement("div");
            header.style.cssText = "display:flex;align-items:center;gap:8px;min-height:18px";
            const label = document.createElement("div");
            label.textContent = "Run to inspect value";
            label.style.cssText = "color:#9eabb8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0";
            const settingsButton = document.createElement("button");
            settingsButton.textContent = "⚙⌄";
            settingsButton.title = "Show label and precision settings";
            settingsButton.setAttribute("aria-expanded", "false");
            settingsButton.style.cssText = "width:34px;min-width:34px;height:22px;padding:0;border:1px solid #48515e;border-radius:4px;background:#303641;color:#dce3ea;cursor:pointer;white-space:nowrap";
            header.append(label, settingsButton);
            const value = document.createElement("div");
            value.textContent = "—";
            value.style.cssText = "margin-top:4px;color:#72d6ff;font:600 24px ui-monospace,SFMono-Regular,Consolas,monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis";
            const settings = document.createElement("div");
            settings.style.cssText = "display:none;grid-template-columns:auto minmax(80px,1fr) auto 58px;align-items:center;gap:6px;margin-top:8px;padding-top:7px;border-top:1px solid #343c46;color:#aeb9c5";
            const labelInput = document.createElement("input");
            labelInput.type = "text";
            labelInput.value = String(labelWidget?.value || "Value");
            labelInput.style.cssText = "min-width:0;width:100%;box-sizing:border-box;background:#171a20;color:#e7edf3;border:1px solid #48515e;border-radius:4px;padding:3px 5px";
            const precisionInput = document.createElement("input");
            precisionInput.type = "number";
            precisionInput.min = "0";
            precisionInput.max = "12";
            precisionInput.step = "1";
            precisionInput.value = String(precisionWidget?.value ?? 6);
            precisionInput.style.cssText = "width:58px;box-sizing:border-box;background:#171a20;color:#e7edf3;border:1px solid #48515e;border-radius:4px;padding:3px 5px";
            settings.append(document.createTextNode("Label"), labelInput, document.createTextNode("Precision"), precisionInput);
            root.append(header, value, settings);

            const widget = node.addDOMWidget("alice_lab_audio_tools_float_out_ui", "ALICE_LAB_FLOAT_OUT", root, {
                serialize: false,
                hideOnZoom: true,
            });
            widget.computeSize = (width) => [width, settingsExpanded ? 126 : 86];
            node.setSize([Math.max(node.size[0], 300), Math.max(node.size[1], 150)]);

            function updateNativeWidget(nativeWidget, nextValue) {
                if (!nativeWidget) return;
                nativeWidget.value = nextValue;
                nativeWidget.callback?.(nextValue);
                node.graph?.setDirtyCanvas(true, true);
            }

            function currentSettings() {
                const nextLabel = labelInput.value || "Value";
                const numericPrecision = Number(precisionInput.value);
                const nextPrecision = Number.isFinite(numericPrecision)
                    ? Math.max(0, Math.min(12, Math.round(numericPrecision)))
                    : 6;
                return [nextLabel, nextPrecision];
            }

            function syncSettingsToWidgets() {
                const [nextLabel, nextPrecision] = currentSettings();
                precisionInput.value = String(nextPrecision);
                updateNativeWidget(labelWidget, nextLabel);
                updateNativeWidget(precisionWidget, nextPrecision);
            }

            settingsButton.addEventListener("click", () => {
                settingsExpanded = !settingsExpanded;
                settings.style.display = settingsExpanded ? "grid" : "none";
                root.style.height = settingsExpanded ? "122px" : "82px";
                settingsButton.textContent = settingsExpanded ? "⚙⌃" : "⚙⌄";
                settingsButton.title = settingsExpanded ? "Hide label and precision settings" : "Show label and precision settings";
                settingsButton.setAttribute("aria-expanded", String(settingsExpanded));
                node.setSize([node.size[0], Math.max(settingsExpanded ? 190 : 150, node.size[1] + (settingsExpanded ? 40 : -40))]);
            });
            labelInput.addEventListener("input", syncSettingsToWidgets);
            precisionInput.addEventListener("input", syncSettingsToWidgets);

            // Hidden native widgets are not serialized consistently across
            // ComfyUI frontend versions. Persist this node's stable current
            // layout explicitly, including edits saved before an input blur.
            chainCallback(node, "onSerialize", function (info) {
                const [nextLabel, nextPrecision] = currentSettings();
                labelWidget.value = nextLabel;
                precisionWidget.value = nextPrecision;
                info.widgets_values = [nextLabel, nextPrecision];
            });

            chainCallback(node, "onResize", function () {
                root.style.width = `${Math.max(120, node.size[0] - 20)}px`;
            });

            chainCallback(node, "onExecuted", function (message) {
                try {
                    const raw = message?.alice_lab_audio_tools_float_out?.[0] ?? message?.alice_lab_audio_tools_float_out;
                    const data = typeof raw === "string" ? JSON.parse(raw) : raw;
                    if (!data || !Number.isFinite(Number(data.value))) {
                        throw new Error("No FLOAT value was returned");
                    }
                    const precision = Math.max(0, Math.min(12, Number(data.precision) || 0));
                    label.textContent = String(data.label || "Value");
                    value.textContent = Number(data.value).toFixed(precision);
                    value.title = String(data.value);
                } catch (error) {
                    label.textContent = "ALICE Lab Audio Tools Float Out";
                    value.textContent = error.message;
                    value.style.fontSize = "12px";
                    value.style.color = "#ff7474";
                }
            });
        });
    },
});

import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

function chainCallback(target, key, callback) {
    const original = target[key];
    target[key] = function (...args) {
        const result = original?.apply(this, args);
        callback.apply(this, args);
        return result;
    };
}

app.registerExtension({
    name: "ALICE_Lab.VideoOut",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "AliceLabOutputFFmpeg") return;

        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            const node = this;
            const filenameWidget = node.widgets.find((widget) => widget.name === "filename");
            let media = null;

            const root = document.createElement("div");
            root.style.cssText = "height:300px;display:flex;flex-direction:column;gap:5px;padding:5px;box-sizing:border-box;background:#15191f;color:#dce3ea;font:12px sans-serif;overflow:hidden";
            const header = document.createElement("div");
            header.style.cssText = "display:flex;align-items:center;gap:8px;padding:5px 7px;background:#242830;border:1px solid #39424e;border-radius:6px;flex:none";
            const title = document.createElement("strong");
            title.textContent = "Video preview";
            const status = document.createElement("span");
            status.textContent = "Run to load video";
            status.style.cssText = "margin-left:auto;color:#9eabb8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
            const save = document.createElement("button");
            save.textContent = "Save";
            save.disabled = true;
            save.style.cssText = "height:23px;padding:1px 8px;border:1px solid #48515e;border-radius:4px;color:#dce3ea;background:#303641;cursor:pointer;flex:none";
            header.append(title, status, save);

            const video = document.createElement("video");
            video.controls = true;
            video.preload = "metadata";
            video.style.cssText = "display:block;width:100%;height:100%;min-height:120px;flex:1 1 auto;object-fit:contain;background:#080a0d;border:1px solid #39424e;box-sizing:border-box";
            root.append(header, video);

            function mediaUrl(cache = false) {
                if (!media) return "";
                const query = new URLSearchParams({
                    filename: media.filename,
                    subfolder: media.subfolder || "",
                    type: media.type || "temp",
                });
                if (cache) query.set("cache", Date.now().toString());
                return api.apiURL(`/view?${query}`);
            }

            function downloadName() {
                let name = String(filenameWidget?.value || "ALICE_Lab_video")
                    .split(/[\\/]/).pop()
                    .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
                    .replace(/[ .]+$/g, "");
                if (name.toLowerCase().endsWith(".mp4")) name = name.slice(0, -4);
                return `${name || "ALICE_Lab_video"}.mp4`;
            }

            save.addEventListener("click", () => {
                if (!media) return;
                const query = new URLSearchParams({
                    filename: media.filename,
                    download_name: downloadName(),
                });
                const anchor = document.createElement("a");
                anchor.href = api.apiURL(`/alice_lab_audio_tools/video_out_download?${query}`);
                // Mark this as a download so the browser does not navigate away
                // from ComfyUI and trigger its unsaved-workflow warning.
                anchor.download = downloadName();
                document.body.append(anchor);
                anchor.click();
                requestAnimationFrame(() => anchor.remove());
            });

            const widget = node.addDOMWidget("alice_lab_video_out_ui", "ALICE_LAB_VIDEO_OUT", root, {
                serialize: false,
                hideOnZoom: true,
            });
            const minimumPanelHeight = 180;
            let panelHeight = 300;
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
                node.graph?.setDirtyCanvas(true, true);
            }

            function schedulePanelFit() {
                if (fitPending) return;
                fitPending = true;
                requestAnimationFrame(fitPanelToNode);
            }

            chainCallback(node, "onResize", schedulePanelFit);
            chainCallback(node, "onConfigure", schedulePanelFit);
            node.setSize([Math.max(node.size[0], 590), Math.max(node.size[1], 380)]);
            schedulePanelFit();

            chainCallback(node, "onExecuted", function (message) {
                try {
                    const value = message?.alice_lab_video_out?.[0] ?? message?.alice_lab_video_out;
                    media = typeof value === "string" ? JSON.parse(value) : value;
                    if (!media?.filename) throw new Error("No preview file was returned");
                    video.pause();
                    video.src = mediaUrl(true);
                    video.load();
                    const encoder = media.video_encoder ? ` · ${media.video_encoder}` : "";
                    status.textContent = `${media.width} × ${media.height} · ${media.duration.toFixed(3)}s${encoder}`;
                    save.disabled = false;
                } catch (error) {
                    status.textContent = `Video Out: ${error.message}`;
                    save.disabled = true;
                }
            });

            chainCallback(node, "onRemoved", function () {
                video.pause();
                video.removeAttribute("src");
                video.load();
            });
        });
    },
});

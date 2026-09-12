/* Render one frame at a time; FFmpeg receives the original recording separately. */
let videoTrack = null;
let videoExport = null;

function videoBusy(busy) {
  $("#render-video").disabled = busy;
  $("#cancel-video").hidden = !busy;
  $$("#video-options input").forEach(input => input.disabled = busy);
}

async function openVideoExport() {
  if (!selected) return;
  videoTrack = selected;
  $("#video-title").textContent = videoTrack.title;
  $("#video-progress").hidden = true;
  $("#video-error").hidden = true;
  $("#save-video").hidden = true;
  $("#video-status").textContent = "MP4 · animated artwork and audio";
  const canvas = $("#video-preview");
  drawSeedArtwork(canvas.getContext("2d"), canvas.width, canvas.height, videoTrack.recipe.seed);
  $("#video-dialog").showModal();
}

async function cancelVideoExport() {
  const operation = videoExport;
  if (!operation) return;
  operation.cancelled = true;
  operation.controller.abort();
  if (operation.id) {
    operation.stopping ||= api(`/api/video-exports/${operation.id}/cancel`, "POST", {});
    await operation.stopping;
  }
}

async function checkVideoResponse(response) {
  if (response.ok) return;
  const body = await response.json().catch(() => ({}));
  const error = new Error(body.error || "This video frame could not be saved.");
  error.retryable = response.status === 429 || response.status >= 500;
  throw error;
}

async function sendVideoFrame(operation, job, index, frame) {
  let confirm = false;
  for (let attempt = 0; ; attempt++) {
    try {
      if (operation.cancelled) throw new DOMException("Export cancelled.", "AbortError");
      if (confirm) {
        const response = await fetch(`/api/video-exports/${job.id}`, { signal: operation.controller.signal });
        await checkVideoResponse(response);
        const saved = await response.json();
        if (saved.status !== "rendering") throw new Error("This video export has stopped.");
        // A lost response may follow a successful write. Never append that frame twice.
        if (saved.received === index + 1) return;
        if (saved.received !== index) throw new Error("The video frame sequence changed. Export again to restart.");
        confirm = false;
      }
      const response = await fetch(`/api/video-exports/${job.id}/frames`, {
        method: "POST", headers: { "Content-Type": "image/png", "X-Riff-Request": "1", "X-Riff-Frame": String(index) },
        body: frame, signal: operation.controller.signal,
      });
      await checkVideoResponse(response);
      return;
    } catch (error) {
      if (operation.cancelled || operation.controller.signal.aborted || attempt >= 3 ||
          !(error instanceof TypeError || error.retryable)) throw error;
      confirm = true;
      $("#video-status").textContent = "Reconnecting…";
      await new Promise(resolve => setTimeout(resolve, 250 * 2 ** attempt));
    }
  }
}

async function createVideo(event) {
  event.preventDefault();
  if (videoExport || !videoTrack) return;
  const track = videoTrack;
  const operation = { controller: new AbortController(), cancelled: false, id: null };
  videoExport = operation;
  videoBusy(true);
  $("#video-error").hidden = true;
  $("#save-video").hidden = true;
  $("#video-progress").hidden = false;
  $("#video-progress").value = 0;
  $("#video-status").textContent = "Preparing the artwork…";
  let complete = false;
  try {
    const motion = await visualizationFor(track.id);
    if (operation.cancelled) return;
    const job = await api(`/api/tracks/${track.id}/video-exports`, "POST", {
        width: Number($("#video-width").value), height: Number($("#video-height").value),
        fps: Number($("#video-fps").value),
    });
    operation.id = job.id;
    if (operation.cancelled) return;
    const canvas = document.createElement("canvas");
    canvas.width = job.width;
    canvas.height = job.height;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("The browser could not open a drawing canvas.");
    const preview = $("#video-preview"), previewContext = preview.getContext("2d");
    for (let index = 0; index < job.frames; index++) {
      if (operation.cancelled) return;
      const seconds = index / job.fps;
      drawSeedArtwork(context, job.width, job.height, track.recipe.seed, seconds, motionAt(motion, seconds));
      const frame = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
      if (!frame) throw new Error("The browser could not draw this video frame.");
      await sendVideoFrame(operation, job, index, frame);
      // Reuse the completed frame for the progress preview; no parallel audio playback.
      previewContext.setTransform(1, 0, 0, 1, 0, 0);
      previewContext.drawImage(canvas, 0, 0, preview.width, preview.height);
      const progress = (index + 1) / job.frames;
      $("#video-progress").value = progress;
      $("#video-status").textContent = `Drawing your video · ${Math.round(progress * 100)}% · Keep this window open`;
    }
    if (operation.cancelled) return;
    $("#video-status").textContent = "Finishing your MP4…";
    const result = await api(`/api/video-exports/${job.id}/finish`, "POST", {});
    if (operation.cancelled || result.status !== "done") return;
    complete = true;
    const download = $("#save-video");
    download.href = result.download_url;
    download.download = `${track.title}.mp4`;
    download.hidden = false;
    $("#video-status").textContent = "Your video is ready.";
    download.click();
  } catch (error) {
    if (!operation.cancelled) {
      $("#video-error").textContent = error.message;
      $("#video-error").hidden = false;
      $("#video-status").textContent = "Export stopped.";
    }
  } finally {
    if (!complete && operation.id) {
      try {
        operation.stopping ||= api(`/api/video-exports/${operation.id}/cancel`, "POST", {});
        await operation.stopping;
      }
      catch (error) { if (!operation.cancelled) notify(error.message); }
    }
    if (operation.cancelled) $("#video-status").textContent = "Export cancelled.";
    if (videoExport === operation) videoExport = null;
    videoBusy(false);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#download-video").addEventListener("click", openVideoExport);
  $("#video-form").addEventListener("submit", createVideo);
  $("#cancel-video").addEventListener("click", () => cancelVideoExport().catch(error => notify(error.message)));
  $("#video-dialog").addEventListener("close", () => cancelVideoExport().catch(error => notify(error.message)));
});

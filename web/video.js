/* Render one frame at a time; FFmpeg receives the original recording separately. */
let videoTrack = null;
let videoExport = null;
let videoPreviewRevision = 0;
const videoPresets = { "4k": [3840, 2160, 60], studio: [2560, 1980, 60], portrait: [2160, 3840, 60], square: [2160, 2160, 60] };

async function createVideoFrameEncoder(canvas, signal) {
  let worker = null, pending = null, stopped = null, failure = null, sequence = 0;
  const cancelled = () => new DOMException("Export cancelled.", "AbortError");
  const settle = (id, value, error) => {
    if (pending?.id !== id) return;
    const current = pending; pending = null;
    if (error) current.reject(error); else current.resolve(value);
  };
  const wait = start => {
    if (stopped) return Promise.reject(stopped);
    if (pending) return Promise.reject(new Error("A video frame is still being drawn."));
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      pending = { id, resolve, reject };
      try { start(id); } catch (error) { settle(id, null, error); }
    });
  };
  const releaseWorker = () => { worker?.terminate(); worker = null; };
  const close = () => {
    stopped ||= cancelled();
    if (pending) settle(pending.id, null, stopped);
    releaseWorker();
    signal.removeEventListener("abort", close);
  };
  signal.addEventListener("abort", close, { once: true });
  if (signal.aborted) close();
  try {
    if (typeof Worker === "function" && typeof OffscreenCanvas === "function" &&
        typeof createImageBitmap === "function" && OffscreenCanvas.prototype.convertToBlob) {
      if (stopped) throw stopped;
      worker = new Worker("/video-encoder.js");
      worker.onmessage = ({ data }) => {
        const error = data.error ? new Error("The browser could not encode this video frame.") : null;
        settle(data.id, data, error);
      };
      worker.onerror = event => {
        event.preventDefault();
        failure = new Error("The browser could not open its video encoder.");
        if (pending) settle(pending.id, null, failure);
      };
      worker.onmessageerror = () => {
        failure = new Error("The browser could not read this video frame.");
        if (pending) settle(pending.id, null, failure);
      };
      const ready = await wait(id => worker.postMessage({ type: "init", id, width: canvas.width, height: canvas.height }));
      if (!ready.ready) releaseWorker();
    }
  } catch (error) {
    releaseWorker();
    if (stopped) { close(); throw stopped; }
    // Older browsers or an unavailable worker can still use the canvas encoder.
  }
  if (stopped) throw stopped;
  return {
    async encode() {
      if (stopped) throw stopped;
      if (!worker) {
        return wait(id => canvas.toBlob(async image => {
          if (pending?.id !== id) return;
          try {
            if (!image) throw new Error("The browser could not draw this video frame.");
            // Keep one request in byte storage, never a growing browser Blob store.
            settle(id, await image.arrayBuffer());
          } catch (error) { settle(id, null, error); }
        }, "image/png"));
      }
      if (failure) throw failure;
      const bitmap = await wait(id => createImageBitmap(canvas).then(value => {
        if (pending?.id !== id) { value.close(); return; }
        settle(id, value);
      }, error => settle(id, null, error)));
      if (stopped) { bitmap.close(); throw stopped; }
      try {
        const result = await wait(id => worker.postMessage({ type: "frame", id, bitmap }, [bitmap]));
        if (!(result.frame instanceof ArrayBuffer)) throw new Error("The browser could not encode this video frame.");
        return result.frame;
      } finally { bitmap.close(); }
    },
    close,
  };
}

function updateVideoPicture() {
  const values = ["width", "height", "fps"].map(key => Number($("#video-" + key).value));
  $("#video-preset").value = Object.keys(videoPresets).find(key => videoPresets[key].every((value, i) => value === values[i])) || "custom";
  const canvas = $("#video-preview");
  if (values[0] > 0 && values[1] > 0) {
    canvas.width = 880;
    canvas.height = Math.round(880 * values[1] / values[0]);
  }
  updateVideoPassage();
}

function videoTime(seconds) {
  const ticks = Math.round(seconds * 1000), fraction = ticks % 1000;
  return formatTime(Math.floor(ticks / 1000)) + (fraction ? "." + String(fraction).padStart(3, "0").replace(/0+$/, "") : "");
}

function parseVideoTime(text) {
  const parts = text.trim().split(":");
  if (parts.some(part => !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(part)) || parts.slice(1).some(part => Number(part) >= 60)) return NaN;
  return parts.reduce((seconds, part) => seconds * 60 + Number(part), 0);
}

function videoPassage() {
  const duration = videoTrack.audio.duration;
  if ($("#video-selection").value === "whole") return { start_seconds: 0, end_seconds: duration };
  const end = $("#video-end").value.trim();
  return { start_seconds: parseVideoTime($("#video-start").value),
    // The displayed endpoint may be rounded to milliseconds; retain the exact ending.
    end_seconds: end === videoTime(duration) ? duration : parseVideoTime(end) };
}

async function updateVideoPassage() {
  const revision = ++videoPreviewRevision, track = videoTrack;
  $("#video-passage").hidden = $("#video-selection").value !== "passage";
  $("#video-use-passage").hidden = !window.RiffCompare?.passage().valid;
  if (!track || videoExport) return;
  const start = videoPassage().start_seconds;
  if (!Number.isFinite(start) || start < 0 || start >= track.audio.duration) return;
  try {
    const motion = await visualizationFor(track.id);
    if (revision !== videoPreviewRevision || videoExport || videoTrack !== track) return;
    const canvas = $("#video-preview");
    drawSeedArtwork(canvas.getContext("2d"), canvas.width, canvas.height, track.recipe.seed, start, motionAt(motion, start), artworkAppearance());
  } catch { /* The seed poster remains available while motion data is unavailable. */ }
}

function videoBusy(busy) {
  $("#render-video").disabled = busy;
  $("#cancel-video").hidden = !busy;
  $$("#video-form input, #video-form select, #video-use-passage").forEach(input => input.disabled = busy);
  window.RiffUpdate?.render(state);
}

async function openVideoExport() {
  if (!selected) return;
  videoTrack = selected;
  $("#video-title").textContent = videoTrack.title;
  $("#video-progress").hidden = true;
  $("#video-error").hidden = true;
  $("#save-video").hidden = true;
  $("#video-status").textContent = "MP4 · animated artwork and audio";
  $("#video-selection").value = "whole";
  $("#video-start").value = videoTime(0);
  $("#video-end").value = videoTime(videoTrack.audio.duration);
  const canvas = $("#video-preview");
  drawSeedArtwork(canvas.getContext("2d"), canvas.width, canvas.height, videoTrack.recipe.seed, 0, [], artworkAppearance());
  $("#video-dialog").showModal();
  updateVideoPassage();
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
      await response.arrayBuffer();
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
  const appearance = artworkAppearance();
  const operation = { controller: new AbortController(), cancelled: false, id: null };
  videoExport = operation;
  videoBusy(true);
  $("#video-error").hidden = true;
  $("#save-video").hidden = true;
  $("#video-progress").hidden = false;
  $("#video-progress").value = 0;
  $("#video-status").textContent = "Preparing the artwork…";
  let complete = false, encoder = null;
  try {
    const range = videoPassage();
    if (!Number.isFinite(range.start_seconds) || !Number.isFinite(range.end_seconds) ||
        range.start_seconds < 0 || range.start_seconds >= range.end_seconds || range.end_seconds > track.audio.duration) {
      throw new Error("Choose a passage within this recording, with its start before its end.");
    }
    const motion = await visualizationFor(track.id);
    if (operation.cancelled) return;
    const job = await api(`/api/tracks/${track.id}/video-exports`, "POST", {
        width: Number($("#video-width").value), height: Number($("#video-height").value),
        fps: Number($("#video-fps").value),
        ...range,
    });
    operation.id = job.id;
    if (operation.cancelled) return;
    const canvas = document.createElement("canvas");
    canvas.width = job.width;
    canvas.height = job.height;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("The browser could not open a drawing canvas.");
    encoder = await createVideoFrameEncoder(canvas, operation.controller.signal);
    const preview = $("#video-preview"), previewContext = preview.getContext("2d");
    for (let index = 0; index < job.frames; index++) {
      if (operation.cancelled) return;
      const seconds = job.source_start + index / job.fps;
      drawSeedArtwork(context, job.width, job.height, track.recipe.seed, seconds, motionAt(motion, seconds), appearance);
      const frame = await encoder.encode();
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
    const passage = job.source_start > 0 || job.source_end < track.audio.duration;
    download.download = `${track.title}${passage ? " — passage" : ""}.mp4`;
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
    encoder?.close();
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
  $("#video-preset").addEventListener("change", () => {
    const values = videoPresets[$("#video-preset").value];
    if (values) for (const [i, key] of ["width", "height", "fps"].entries()) $("#video-" + key).value = values[i];
    else $("#video-options").open = true;
    updateVideoPicture();
  });
  for (const key of ["width", "height", "fps"]) $("#video-" + key).addEventListener("input", updateVideoPicture);
  updateVideoPicture();
  $("#download-video").addEventListener("click", openVideoExport);
  $("#video-form").addEventListener("submit", createVideo);
  for (const id of ["video-selection", "video-start", "video-end"]) $("#" + id).addEventListener("input", updateVideoPassage);
  for (const id of ["video-start", "video-end"]) $("#" + id).addEventListener("blur", event => {
    const seconds = parseVideoTime(event.target.value);
    if (Number.isFinite(seconds)) event.target.value = videoTime(seconds);
  });
  $("#video-use-passage").addEventListener("click", () => {
    const passage = window.RiffCompare?.passage();
    if (!passage?.valid) return;
    $("#video-start").value = videoTime(passage.start);
    $("#video-end").value = videoTime(Math.min(passage.end, videoTrack.audio.duration));
    updateVideoPassage();
  });
  $("#cancel-video").addEventListener("click", () => cancelVideoExport().catch(error => notify(error.message)));
  $("#video-dialog").addEventListener("close", () => cancelVideoExport().catch(error => notify(error.message)));
});

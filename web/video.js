/* Render one frame at a time; FFmpeg receives the original recording separately. */
let videoTrack = null;
let videoExport = null;
let videoPreviewRevision = 0;
const videoPresets = { "4k": [3840, 2160, 60], studio: [2560, 1980, 60], portrait: [2160, 3840, 60], square: [2160, 2160, 60] };

// A frame upload resolves through a microtask. Yield one ordinary browser task
// after each acknowledged frame so progress and cancellation can paint without
// depending on pointer events. This is deliberately independent of audio time,
// requestAnimationFrame and idle scheduling.
function yieldVideoProgress() {
  if (typeof MessageChannel === "function") {
    return new Promise(resolve => {
      const channel = new MessageChannel();
      channel.port1.onmessage = () => { channel.port1.close(); channel.port2.close(); resolve(); };
      channel.port2.postMessage(0);
    });
  }
  return new Promise(resolve => setTimeout(resolve, 0));
}

function videoBitrate(width, height, fps, quality = { max_bitrate: 32_000_000, min_bitrate: 8_000_000, bits_per_pixel: .055 }) {
  return Math.round(Math.min(quality.max_bitrate, Math.max(quality.min_bitrate, width * height * fps * quality.bits_per_pixel)));
}

async function preferredVideoTransport(width, height, fps, bitrate = videoBitrate(width, height, fps)) {
  // Small exports keep the exact PNG path used for previews and compatibility
  // checks. The browser codec path is reserved for the expensive high-resolution
  // case where PNG encoding and FFmpeg video encoding dominate wall time.
  if (width * height < 1_000_000 || typeof VideoEncoder !== "function" || typeof VideoFrame !== "function") return "png";
  for (const codec of ["avc1.640034", "avc1.640033", "avc1.64002A"]) {
    try {
      const support = await VideoEncoder.isConfigSupported({
        codec, width, height, framerate: fps, bitrate,
        bitrateMode: "variable", latencyMode: "quality", hardwareAcceleration: "no-preference",
        avc: { format: "annexb" },
      });
      if (support.supported) return "h264";
    } catch { /* The PNG path remains available when this browser cannot encode H.264. */ }
  }
  return "png";
}

async function createVideoFrameEncoder(canvas, signal, options = {}) {
  const requestedTransport = options.transport || "png";
  const fps = Number(options.fps) || 30;
  const bitrate = Number(options.bitrate) || videoBitrate(canvas.width, canvas.height, fps);
  const timings = { snapshot_seconds: 0, encode_seconds: 0 };
  let worker = null, pending = null, stopped = null, failure = null, sequence = 0;
  const cancelled = () => new DOMException("Export cancelled.", "AbortError");
  const settle = (id, value, error) => {
    if (pending?.id !== id) return;
    const current = pending; pending = null;
    clearTimeout(current.timer);
    if (error) current.reject(error); else current.resolve(value);
  };
  const wait = start => {
    if (stopped) return Promise.reject(stopped);
    if (pending) return Promise.reject(new Error("A video frame is still being drawn."));
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => {
        stopped = new Error("The browser video encoder stopped responding. Try exporting again.");
        close();
      }, 60000);
      pending = { id, resolve, reject, timer };
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
      const ready = await wait(id => worker.postMessage({
        type: "init", id, width: canvas.width, height: canvas.height,
        fps, bitrate, transport: requestedTransport,
      }));
      if (!ready.ready || (requestedTransport === "h264" && ready.transport !== "h264")) {
        releaseWorker();
      }
    }
  } catch (error) {
    releaseWorker();
    if (stopped) { close(); throw stopped; }
    // Older browsers or an unavailable worker can still use the canvas encoder.
  }
  if (stopped) throw stopped;
  if (requestedTransport === "h264" && !worker) {
    close();
    throw new Error("The browser could not open its accelerated video encoder.");
  }
  return {
    timings,
    async encode(index = 0) {
      if (stopped) throw stopped;
      if (!worker) {
        const started = performance.now();
        const bytes = await wait(id => canvas.toBlob(async image => {
          if (pending?.id !== id) return;
          try {
            if (!image) throw new Error("The browser could not draw this video frame.");
            // Keep one request in byte storage, never a growing browser Blob store.
            settle(id, await image.arrayBuffer());
          } catch (error) { settle(id, null, error); }
        }, "image/png"));
        timings.encode_seconds += (performance.now() - started) / 1000;
        return bytes;
      }
      if (failure) throw failure;
      const snapshotStarted = performance.now();
      const bitmap = await wait(id => createImageBitmap(canvas).then(value => {
        if (pending?.id !== id) { value.close(); return; }
        settle(id, value);
      }, error => settle(id, null, error)));
      timings.snapshot_seconds += (performance.now() - snapshotStarted) / 1000;
      if (stopped) { bitmap.close(); throw stopped; }
      try {
        const encodingStarted = performance.now();
        const result = await wait(id => worker.postMessage({
          type: "frame", id, bitmap,
          timestamp: Math.round(index * 1_000_000 / fps),
          duration: Math.max(1, Math.round(1_000_000 / fps)),
          keyFrame: index === 0 || (requestedTransport === "h264" && index % Math.max(1, Math.round(fps * 2)) === 0),
        }, [bitmap]));
        if (!(result.frame instanceof ArrayBuffer)) throw new Error("The browser could not encode this video frame.");
        timings.encode_seconds += (performance.now() - encodingStarted) / 1000;
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
  $("#save-video-audio").hidden = true;
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
        method: "POST", headers: {
          "Content-Type": job.transport === "h264" ? "video/h264" : "image/png",
          "X-Riff-Request": "1", "X-Riff-Frame": String(index),
        },
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
  $("#save-video-audio").hidden = true;
  $("#video-progress").hidden = false;
  $("#video-progress").value = 0;
  $("#video-status").textContent = "Preparing the artwork…";
  let complete = false, encoder = null;
  const started = performance.now();
  const timings = { prepare_seconds: 0, draw_seconds: 0, snapshot_seconds: 0, encode_seconds: 0,
    upload_seconds: 0, preview_seconds: 0, yield_seconds: 0, before_finish_seconds: 0 };
  try {
    const range = videoPassage();
    if (!Number.isFinite(range.start_seconds) || !Number.isFinite(range.end_seconds) ||
        range.start_seconds < 0 || range.start_seconds >= range.end_seconds || range.end_seconds > track.audio.duration) {
      throw new Error("Choose a passage within this recording, with its start before its end.");
    }
    const motion = await visualizationFor(track.id);
    if (operation.cancelled) return;
    const width = Number($("#video-width").value), height = Number($("#video-height").value),
      fps = Number($("#video-fps").value);
    const capabilities = await api("/api/capabilities");
    const quality = capabilities.video_encoding;
    if (!quality) throw new Error("Export settings are unavailable. Reload Riff and try again.");
    const bitrate = videoBitrate(width, height, fps, quality);
    let transport = await preferredVideoTransport(width, height, fps, bitrate);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("The browser could not open a drawing canvas.");
    try {
      encoder = await createVideoFrameEncoder(canvas, operation.controller.signal, { transport, fps, bitrate });
    } catch (error) {
      // A codec can be advertised on the window but unavailable in a worker.
      // Fall back before starting the server job so its pipe format stays exact.
      if (transport !== "h264") throw error;
      transport = "png";
      encoder = await createVideoFrameEncoder(canvas, operation.controller.signal, { transport, fps });
    }
    const job = await api(`/api/tracks/${track.id}/video-exports`, "POST", {
        width, height, fps, transport,
        ...range,
    });
    operation.id = job.id;
    if (operation.cancelled) return;
    if (job.transport !== transport) throw new Error("The studio selected a different video path. Export again.");
    timings.prepare_seconds = (performance.now() - started) / 1000;
    const preview = $("#video-preview"), previewContext = preview.getContext("2d");
    for (let index = 0; index < job.frames; index++) {
      if (operation.cancelled) return;
      const seconds = job.source_start + index / job.fps;
      let stageStarted = performance.now();
      drawSeedArtwork(context, job.width, job.height, track.recipe.seed, seconds, motionAt(motion, seconds), appearance);
      timings.draw_seconds += (performance.now() - stageStarted) / 1000;
      const frame = await encoder.encode(index);
      stageStarted = performance.now();
      await sendVideoFrame(operation, job, index, frame);
      timings.upload_seconds += (performance.now() - stageStarted) / 1000;
      stageStarted = performance.now();
      // Reuse the completed frame for the progress preview; no parallel audio playback.
      previewContext.setTransform(1, 0, 0, 1, 0, 0);
      previewContext.drawImage(canvas, 0, 0, preview.width, preview.height);
      const progress = (index + 1) / job.frames;
      $("#video-progress").value = progress;
      $("#video-status").textContent = `Drawing your video · ${Math.round(progress * 100)}% · Keep this window open`;
      timings.preview_seconds += (performance.now() - stageStarted) / 1000;
      stageStarted = performance.now();
      await yieldVideoProgress();
      timings.yield_seconds += (performance.now() - stageStarted) / 1000;
    }
    if (operation.cancelled) return;
    $("#video-status").textContent = "Finishing your MP4…";
    Object.assign(timings, encoder.timings);
    timings.before_finish_seconds = (performance.now() - started) / 1000;
    const result = await api(`/api/video-exports/${job.id}/finish`, "POST", { timings });
    if (operation.cancelled || result.status !== "done") return;
    complete = true;
    const download = $("#save-video");
    download.href = result.download_url;
    const passage = job.source_start > 0 || job.source_end < track.audio.duration;
    download.download = `${track.title}${passage ? " — passage" : ""}.mp4`;
    download.hidden = false;
    const originalAudio = $("#save-video-audio");
    originalAudio.href = result.original_audio.download_url;
    originalAudio.download = `${track.title}.wav`;
    originalAudio.hidden = false;
    $("#video-status").textContent = "Your video is ready. The original WAV is the full recording.";
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

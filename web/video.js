/* Render one frame at a time; FFmpeg receives the original recording separately. */
let videoTrack = null;
let videoExport = null;
let videoPreviewRevision = 0;
const videoPresets = { "4k": [3840, 2160, 60], studio: [2560, 1980, 60], portrait: [2160, 3840, 60], square: [2160, 2160, 60] };

// A frame upload resolves through a microtask. Yield one ordinary browser task
// at the progress cadence so progress and cancellation can paint without
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

function videoBitrate(width, height, fps, quality = { max_bitrate: 64_000_000, min_bitrate: 8_000_000, bits_per_pixel: .11 }) {
  return Math.round(Math.min(quality.max_bitrate, Math.max(quality.min_bitrate, width * height * fps * quality.bits_per_pixel)));
}

// The server makes the final libx264 encode, so browser H.264 is an
// intermediate: a fixed quantizer keeps it near lossless (QP 14 measured
// 52.8 dB RGB PSNR on 4K artwork); VBR serves browsers without that mode.
async function h264EncoderConfig(width, height, fps, bitrate, quantizer) {
  if (typeof VideoEncoder !== "function" || typeof VideoFrame !== "function") return null;
  const modes = quantizer ? [{ bitrateMode: "quantizer" }] : [];
  modes.push({ bitrate, bitrateMode: "variable", latencyMode: "quality" });
  for (const codec of ["avc1.640034", "avc1.640033", "avc1.64002A"]) {
    for (const mode of modes) {
      try {
        const support = await VideoEncoder.isConfigSupported({
          codec, width, height, framerate: fps, hardwareAcceleration: "no-preference",
          avc: { format: "annexb" }, ...mode,
        });
        if (support.supported) return support.config;
      } catch { /* The next mode or the PNG path remains available. */ }
    }
  }
  return null;
}

async function preferredVideoTransport(width, height, fps, bitrate = videoBitrate(width, height, fps), quantizer = 14) {
  // Small exports keep the exact PNG path used for previews and compatibility
  // checks. The browser codec path is reserved for the expensive high-resolution
  // case where PNG encoding and transfer dominate wall time.
  if (width * height < 1_000_000) return "png";
  return await h264EncoderConfig(width, height, fps, bitrate, quantizer) ? "h264" : "png";
}

// H.264 frames are taken straight from the canvas on this thread: no bitmap
// copy and no per-frame flush, so drawing, encoding and upload overlap.
function canvasH264Encoder(canvas, signal, config, fps, quantizer) {
  const timings = { snapshot_seconds: 0, encode_seconds: 0 };
  const outputs = [];
  let stopped = null;
  const fail = error => {
    stopped ||= error;
    for (const pending of outputs.splice(0)) pending.reject(stopped);
  };
  const encoder = new VideoEncoder({
    output(chunk) {
      const pending = outputs.shift();
      if (!pending) return fail(new Error("The browser video encoder produced an unexpected frame."));
      const bytes = new ArrayBuffer(chunk.byteLength);
      chunk.copyTo(bytes);
      pending.resolve(bytes);
    },
    error() { fail(new Error("The browser could not encode this video frame.")); },
  });
  encoder.configure(config);
  const close = () => {
    fail(new DOMException("Export cancelled.", "AbortError"));
    if (encoder.state !== "closed") encoder.close();
    signal.removeEventListener("abort", close);
  };
  signal.addEventListener("abort", close, { once: true });
  if (signal.aborted) close();
  const fixed = config.bitrateMode === "quantizer", keyInterval = Math.max(1, Math.round(fps * 2));
  const accepted = () => new Promise(resolve => "ondequeue" in encoder
    ? encoder.addEventListener("dequeue", resolve, { once: true }) : setTimeout(resolve, 0));
  // An encoder may hold frames until more arrive. Draining releases them
  // whenever the export stops feeding it, within a bounded wait.
  const drain = async () => {
    if (stopped) throw stopped;
    if (!outputs.length) return;
    let timer;
    try {
      await Promise.race([encoder.flush(), new Promise((_, reject) => { timer = setTimeout(() =>
        reject(new Error("The browser video encoder stopped responding. Try exporting again.")), 60000); })]);
    } catch (error) { fail(error); throw stopped; }
    finally { clearTimeout(timer); }
  };
  const submit = async index => {
    if (stopped) throw stopped;
    const started = performance.now();
    // The frame holds the canvas's current pixels; the next frame may be drawn at once.
    const frame = new VideoFrame(canvas, { timestamp: Math.round(index * 1_000_000 / fps),
      duration: Math.max(1, Math.round(1_000_000 / fps)) });
    timings.snapshot_seconds += (performance.now() - started) / 1000;
    const output = new Promise((resolve, reject) => outputs.push({ resolve, reject }));
    output.catch(() => {});
    try {
      encoder.encode(frame, { keyFrame: index % keyInterval === 0, ...(fixed ? { avc: { quantizer } } : {}) });
    } catch (error) { fail(error); throw stopped; }
    finally { frame.close(); }
    const waiting = performance.now();
    // One frame may wait behind the one being encoded; that keeps the encoder busy.
    while (encoder.encodeQueueSize > 1 && !stopped) await accepted();
    timings.encode_seconds += (performance.now() - waiting) / 1000;
    if (stopped) throw stopped;
    return { frame: output };
  };
  return {
    timings, submit, drain, close,
    async encode(index = 0) {
      const { frame } = await submit(index);
      await drain();
      return frame;
    },
    flush: drain,
  };
}

async function createVideoFrameEncoder(canvas, signal, options = {}) {
  const requestedTransport = options.transport || "png";
  const fps = Number(options.fps) || 30;
  const bitrate = Number(options.bitrate) || videoBitrate(canvas.width, canvas.height, fps);
  if (requestedTransport === "h264") {
    if (signal.aborted) throw new DOMException("Export cancelled.", "AbortError");
    const quantizer = options.quantizer === undefined ? 14 : Number(options.quantizer);
    const config = await h264EncoderConfig(canvas.width, canvas.height, fps, bitrate, quantizer);
    if (signal.aborted) throw new DOMException("Export cancelled.", "AbortError");
    if (!config) throw new Error("The browser could not open its accelerated video encoder.");
    try { return canvasH264Encoder(canvas, signal, config, fps, quantizer); }
    catch { throw new Error("The browser could not open its accelerated video encoder."); }
  }
  const timings = { snapshot_seconds: 0, encode_seconds: 0 };
  let worker = null, pending = null, stopped = null, failure = null, sequence = 0;
  let usesWorker = false;
  // Use the upload budget where advertised; standalone encoders use one raw
  // RGBA frame's size. This bounds retained worker Blobs, not export length.
  const byteBudget = options.maxBytes > 0 ? options.maxBytes : canvas.width * canvas.height * 4;
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
  let previous = Promise.resolve(), inFlight = 0;
  const slotWaiters = [];
  const wakeSlot = () => { for (const resolve of slotWaiters.splice(0)) resolve(); };
  const close = () => {
    stopped ||= cancelled();
    if (pending) settle(pending.id, null, stopped);
    releaseWorker();
    wakeSlot();
    signal.removeEventListener("abort", close);
  };
  signal.addEventListener("abort", close, { once: true });
  if (signal.aborted) close();
  const openWorker = async () => {
    if (stopped) throw stopped;
    worker = new Worker("/video-encoder.js", options.trace ? { name: "riff-video-profile" } : undefined);
    worker.onmessage = ({ data }) => {
      // Optional benchmark progress packets do not complete a frame or change
      // the existing encoder deadline.
      if (data.phase) return;
      let error = null;
      if (data.error) {
        error = new Error(data.error.message || "The browser could not encode this video frame.");
        error.name = data.error.name || "Error";
      }
      settle(data.id, data, error);
      if (data.retire) releaseWorker();
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
      type: "init", id, width: canvas.width, height: canvas.height, byteBudget,
    }));
    if (!ready.ready) {
      releaseWorker();
      throw new Error("The browser could not open its video encoder.");
    }
  };
  try {
    if (typeof Worker === "function" && typeof OffscreenCanvas === "function" &&
        typeof createImageBitmap === "function" && OffscreenCanvas.prototype.convertToBlob) {
      await openWorker();
      usesWorker = true;
    }
  } catch (error) {
    releaseWorker();
    if (stopped) { close(); throw stopped; }
    // Older browsers or an unavailable worker can still use the canvas encoder.
  }
  if (stopped) throw stopped;
  // A cancelled export rejects immediately even if the browser is still
  // producing a snapshot; a late bitmap is closed as soon as it arrives.
  const snapshot = () => {
    if (stopped) return Promise.reject(stopped);
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (value, error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal.removeEventListener("abort", aborted);
        if (error) reject(error); else resolve(value);
      };
      const aborted = () => finish(null, stopped || cancelled());
      const timer = setTimeout(() => {
        stopped = new Error("The browser video encoder stopped responding. Try exporting again.");
        finish(null, stopped);
      }, 60000);
      signal.addEventListener("abort", aborted, { once: true });
      createImageBitmap(canvas).then(bitmap => {
        if (stopped) { bitmap.close(); finish(null, stopped); }
        else finish(bitmap);
      }, error => finish(null, error));
    });
  };
  const encodeOnMainThread = async () => {
    if (stopped) throw stopped;
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
  };
  const png = {
    timings,
    async encode(index = 0) {
      const { frame } = await png.submit(index);
      return frame;
    },
    // The snapshot is captured before this returns; the PNG worker encodes the
    // previous frame while the next one is drawn. Two frames may be in flight
    // so a snapshot never has to wait for the encoder unless it is actually
    // behind, while the retained bitmap count stays explicitly bounded.
    async submit(index = 0) {
      if (stopped) throw stopped;
      if (!usesWorker) {
        try { return { frame: Promise.resolve(await encodeOnMainThread()) }; }
        catch (error) { throw videoFrameError(index, error); }
      }
      while (inFlight >= 2) {
        await new Promise(resolve => slotWaiters.push(resolve));
        if (stopped) throw stopped;
      }
      if (failure) throw failure;
      const snapshotStarted = performance.now();
      let bitmap;
      try { bitmap = await snapshot(); }
      catch (error) { throw videoFrameError(index, error); }
      timings.snapshot_seconds += (performance.now() - snapshotStarted) / 1000;
      if (stopped) { bitmap.close(); throw stopped; }
      if (failure) { bitmap.close(); throw failure; }
      inFlight++;
      let released = false;
      const release = () => { if (!released) { released = true; inFlight--; wakeSlot(); } };
      const prior = previous;
      const frame = (async () => {
        await prior.catch(() => {});
        if (stopped) { bitmap.close(); throw stopped; }
        if (failure) { bitmap.close(); throw failure; }
        const encodingStarted = performance.now();
        try {
          if (!worker) await openWorker();
          const result = await wait(id => worker.postMessage({ type: "frame", id, bitmap }, [bitmap]));
          if (!(result.frame instanceof ArrayBuffer)) throw new Error("The browser could not encode this video frame.");
          return result.frame;
        } catch (error) {
          throw videoFrameError(index, error);
        } finally {
          bitmap.close();
          timings.encode_seconds += (performance.now() - encodingStarted) / 1000;
        }
      })();
      previous = frame.catch(() => {});
      frame.then(release, release);
      return { frame };
    },
    async drain() {},
    async flush() {},
    close,
  };
  return png;
}

function videoFrameError(index, cause) {
  if (cause.name === "AbortError") return cause;
  const detail = `${cause.name || "Error"}: ${cause.message || "PNG encoding failed."}`;
  const advice = cause.name === "NotReadableError"
    ? "The browser could not read the PNG data. Check available memory, temporary disk space and storage permissions, then try exporting again."
    : "Try exporting again.";
  return new Error(`The browser could not encode video frame ${index + 1} (${detail}). ${advice}`, { cause });
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

async function sendVideoFrames(operation, job, index, frames) {
  let first = index;
  const end = index + frames.length;
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
        if (saved.received === end) return;
        if (saved.received < first || saved.received >= end) throw new Error("The video frame sequence changed. Export again to restart.");
        // A server can have accepted a prefix before a connection is lost.
        // Resume at its acknowledged frame rather than replaying that prefix.
        frames = frames.slice(saved.received - first);
        first = saved.received;
        confirm = false;
      }
      const batch = frames.length > 1;
      const body = batch ? new Blob(frames.flatMap(bytes => {
        const length = new ArrayBuffer(4);
        new DataView(length).setUint32(0, bytes.byteLength);
        return [length, bytes];
      })) : frames[0];
      const response = await fetch(`/api/video-exports/${job.id}/frames`, {
        method: "POST", headers: {
          "Content-Type": batch ? "application/x-riff-video-frames" : job.transport === "h264" ? "video/h264" : "image/png",
          "X-Riff-Request": "1", "X-Riff-Frame": String(first),
        },
        body, signal: operation.controller.signal,
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
    const batching = capabilities.video_frame_batch;
    const batchLimit = Number.isInteger(batching?.max_frames) && batching.max_frames > 1 &&
      batching.content_type === "application/x-riff-video-frames" && batching.max_bytes > 4
      ? Math.min(16, batching.max_frames) : 1;
    const batchBytes = Math.min(32 * 1024 * 1024, batching?.max_bytes || 0);
    if (!quality) throw new Error("Export settings are unavailable. Reload Riff and try again.");
    const bitrate = videoBitrate(width, height, fps, quality);
    let transport = await preferredVideoTransport(width, height, fps, bitrate, quality.quantizer);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("The browser could not open a drawing canvas.");
    try {
      encoder = await createVideoFrameEncoder(canvas, operation.controller.signal,
        { transport, fps, bitrate, quantizer: quality.quantizer, maxBytes: batchBytes });
    } catch (error) {
      // A codec can be advertised on the window but unavailable in a worker.
      // Fall back before starting the server job so its pipe format stays exact.
      if (transport !== "h264") throw error;
      transport = "png";
      encoder = await createVideoFrameEncoder(canvas, operation.controller.signal, { transport, fps, maxBytes: batchBytes });
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
    // Batches upload strictly in order while later frames are drawn and
    // encoded. Keep the queue bounded in addition to the batch byte limit.
    const capacity = transport === "h264" ? Math.max(2, Math.ceil(job.fps)) : Math.max(2, batchLimit);
    const queued = [];
    let uploaded = 0, queuedBytes = 0, wakeUploader = () => {}, wakeDrawer = () => {}, stopped = false;
    const queueFull = () => queued.length >= capacity ||
      (transport === "png" && batchLimit > 1 && queuedBytes >= batchBytes);
    // Progress and cancellation only need a human-scale cadence. Yielding after
    // every frame adds a task round trip to each of the thousands of frames.
    const reportFrames = Math.max(1, Math.round(job.fps / 10));
    let reported = -reportFrames, lastYield = performance.now();
    const upload = (async () => {
      for (let index = 0; index < job.frames;) {
        const frames = [];
        let bytes = 0;
        while (frames.length < batchLimit && index + frames.length < job.frames) {
          while (!queued.length) {
            if (stopped) return;
            await new Promise(resolve => wakeUploader = resolve);
          }
          const frame = await queued[0];
          if (frames.length && bytes + 4 + frame.byteLength > batchBytes) break;
          queued.shift();
          if (transport === "png") queuedBytes -= frame.byteLength;
          wakeDrawer();
          frames.push(frame);
          bytes += 4 + frame.byteLength;
          // A frame larger than the batch budget retains the single-frame path.
          if (bytes >= batchBytes) break;
        }
        let stageStarted = performance.now();
        await sendVideoFrames(operation, job, index, frames);
        timings.upload_seconds += (performance.now() - stageStarted) / 1000;
        index += frames.length;
        uploaded = index;
        wakeDrawer();
        if (uploaded === job.frames || uploaded - reported >= reportFrames ||
            performance.now() - lastYield >= 100) {
          reported = uploaded;
          stageStarted = performance.now();
          const progress = uploaded / job.frames;
          $("#video-progress").value = progress;
          $("#video-status").textContent = `Drawing your video · ${Math.round(progress * 100)}% · Keep this window open`;
          timings.preview_seconds += (performance.now() - stageStarted) / 1000;
          stageStarted = performance.now();
          await yieldVideoProgress();
          lastYield = performance.now();
          timings.yield_seconds += (lastYield - stageStarted) / 1000;
        }
      }
    })();
    // A never-settling shared Promise.race input retains every winning result
    // (including its frame bytes) until the whole export ends. Subscribe only
    // while a draw/encode wait is active and release that subscription at once.
    let uploadError = null;
    const uploadWaiters = new Set();
    upload.catch(error => {
      uploadError = error;
      for (const reject of uploadWaiters) reject(error);
    });
    const duringUpload = async start => {
      if (uploadError) throw uploadError;
      let rejectFailure;
      const failure = new Promise((_, reject) => { rejectFailure = reject; });
      uploadWaiters.add(rejectFailure);
      try { return await Promise.race([start(), failure]); }
      finally { uploadWaiters.delete(rejectFailure); }
    };
    let lastPreview = 0;
    try {
      for (let index = 0; index < job.frames; index++) {
        if (queueFull()) {
          await duringUpload(() => encoder.drain());
          while (queueFull()) await duringUpload(() => new Promise(resolve => wakeDrawer = resolve));
        }
        if (operation.cancelled) return;
        const seconds = job.source_start + index / job.fps;
        let stageStarted = performance.now();
        drawSeedArtwork(context, job.width, job.height, track.recipe.seed, seconds, motionAt(motion, seconds), appearance);
        timings.draw_seconds += (performance.now() - stageStarted) / 1000;
        const { frame } = await duringUpload(() => encoder.submit(index));
        frame.catch(() => {}); // The uploader reports it in frame order.
        queued.push(frame);
        if (transport === "png") frame.then(bytes => {
          queuedBytes += bytes.byteLength;
          if (queueFull()) wakeDrawer();
        }, () => {});
        wakeUploader();
        // The drawn frame doubles as the progress preview; no parallel audio
        // playback. Preview copies are throttled independently of the video.
        const now = performance.now();
        if (index === job.frames - 1 || now - lastPreview >= 100) {
          lastPreview = now;
          stageStarted = performance.now();
          previewContext.setTransform(1, 0, 0, 1, 0, 0);
          previewContext.drawImage(canvas, 0, 0, preview.width, preview.height);
          timings.preview_seconds += (performance.now() - stageStarted) / 1000;
        }
      }
      await duringUpload(() => encoder.flush());
      await upload;
    } finally {
      stopped = true;
      wakeUploader();
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

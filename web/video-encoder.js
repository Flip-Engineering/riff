/*
 * Lossless PNG frames for small exports and browsers without WebCodecs H.264.
 * H.264 frames are encoded on the drawing thread straight from the canvas.
 * One transferred bitmap in, one encoded frame out; no frame queue.
 */
let canvas = null, context = null, encoding = false, encodedBytes = 0, byteBudget = 0;
const tracing = self.name === "riff-video-profile";
const phase = (id, name) => {
  if (tracing) self.postMessage({ id, phase: name, worker_ms: performance.now(), worker_origin_ms: performance.timeOrigin });
};
phase(undefined, "script_evaluated");

self.onmessage = async ({ data }) => {
  if (data.type === "init") {
    try {
      phase(data.id, "canvas_create_start");
      canvas = new OffscreenCanvas(data.width, data.height);
      phase(data.id, "context_create_start");
      context = canvas.getContext("2d", { alpha: false });
      phase(data.id, "context_ready");
      byteBudget = data.byteBudget;
      self.postMessage({ id: data.id, ready: Boolean(context && canvas.convertToBlob), transport: "png" });
    } catch { self.postMessage({ id: data.id, ready: false }); }
    return;
  }
  if (data.type !== "frame") return;
  const bitmap = data.bitmap;
  try {
    if (!canvas || encoding || bitmap.width !== canvas.width || bitmap.height !== canvas.height) {
      throw new Error("Video encoder is not ready.");
    }
    encoding = true;
    context.drawImage(bitmap, 0, 0);
    bitmap.close();
    phase(data.id, "bitmap_drawn");
    let image = await canvas.convertToBlob({ type: "image/png" });
    phase(data.id, "png_encoded");
    const frame = await image.arrayBuffer();
    phase(data.id, "png_read");
    image = null;
    encodedBytes += frame.byteLength;
    // Chromium can retain the Blob wrappers until this worker is collected,
    // even after their bytes have been transferred. Retire at the caller's
    // byte budget, so long exports do not depend on worker GC scheduling.
    const retire = encodedBytes >= byteBudget;
    self.postMessage({ id: data.id, frame, transport: "png", retire }, [frame]);
    if (retire) self.close();
  } catch (error) {
    self.postMessage({ id: data.id, error: {
      name: error.name || "Error", message: error.message || "PNG encoding failed.",
    } });
  }
  finally {
    bitmap?.close();
    encoding = false;
  }
};

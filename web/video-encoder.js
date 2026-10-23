/*
 * Lossless PNG frames for small exports and browsers without WebCodecs H.264.
 * H.264 frames are encoded on the drawing thread straight from the canvas.
 * One transferred bitmap in, one encoded frame out; no frame queue.
 */
let canvas = null, context = null, encoding = false, encodedBytes = 0, byteBudget = 0;

self.onmessage = async ({ data }) => {
  if (data.type === "init") {
    try {
      canvas = new OffscreenCanvas(data.width, data.height);
      context = canvas.getContext("2d", { alpha: false });
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
    let image = await canvas.convertToBlob({ type: "image/png" });
    const frame = await image.arrayBuffer();
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

/* PNG encoding runs here because main-thread canvas encoding can wait for
 * browser idle tasks whose cadence changes with pointer/compositor activity.
 * One transferred bitmap in, one transferred PNG buffer out; no frame queue. */
let canvas = null, context = null, encoding = false;

self.onmessage = async ({ data }) => {
  if (data.type === "init") {
    try {
      canvas = new OffscreenCanvas(data.width, data.height);
      context = canvas.getContext("2d", { alpha: false });
      self.postMessage({ id: data.id, ready: Boolean(context && canvas.convertToBlob) });
    } catch { self.postMessage({ id: data.id, ready: false }); }
    return;
  }
  if (data.type !== "frame") return;
  const bitmap = data.bitmap;
  try {
    if (!context || encoding || bitmap.width !== canvas.width || bitmap.height !== canvas.height) {
      throw new Error("Video encoder is not ready.");
    }
    encoding = true;
    context.drawImage(bitmap, 0, 0);
    bitmap.close();
    let image = await canvas.convertToBlob({ type: "image/png" });
    const frame = await image.arrayBuffer();
    image = null;
    self.postMessage({ id: data.id, frame }, [frame]);
  } catch { self.postMessage({ id: data.id, error: true }); }
  finally { bitmap?.close(); encoding = false; }
};

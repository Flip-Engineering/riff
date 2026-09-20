/*
 * Prefer a browser H.264 encoder for large exports. It removes the expensive
 * PNG encode/decode round trip and lets FFmpeg mux the already encoded frames.
 * PNG remains the compatibility path and the exact-pixel test path.
 * One transferred bitmap in, one encoded frame out; no frame queue.
 */
let canvas = null, context = null, encoding = false;
let videoEncoder = null, encodedFrame = null, encoderFailure = null, transport = "png";

async function configureH264(data) {
  if (typeof VideoEncoder !== "function" || typeof VideoFrame !== "function") return false;
  const candidates = ["avc1.640034", "avc1.640033", "avc1.64002A"];
  for (const codec of candidates) {
    try {
      const support = await VideoEncoder.isConfigSupported({
        codec, width: data.width, height: data.height, framerate: data.fps,
        bitrate: data.bitrate, bitrateMode: "variable", latencyMode: "quality",
        hardwareAcceleration: "no-preference", avc: { format: "annexb" },
      });
      if (!support.supported) continue;
      videoEncoder = new VideoEncoder({
        output(chunk) {
          if (encodedFrame) { encoderFailure = new Error("Unexpected extra encoded frame."); return; }
          const frame = new ArrayBuffer(chunk.byteLength);
          chunk.copyTo(frame);
          encodedFrame = frame;
        },
        error() {
          encoderFailure = new Error("Video encoding failed.");
        },
      });
      videoEncoder.configure(support.config);
      transport = "h264";
      return true;
    } catch {
      videoEncoder?.close();
      videoEncoder = null;
    }
  }
  return false;
}

self.onmessage = async ({ data }) => {
  if (data.type === "init") {
    try {
      canvas = new OffscreenCanvas(data.width, data.height);
      if (data.transport === "h264" && await configureH264(data)) {
        self.postMessage({ id: data.id, ready: true, transport });
        return;
      }
      context = canvas.getContext("2d", { alpha: false });
      transport = "png";
      self.postMessage({ id: data.id, ready: Boolean(context && canvas.convertToBlob), transport });
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
    if (transport === "h264") {
      encodedFrame = null;
      const frame = new VideoFrame(bitmap, { timestamp: data.timestamp, duration: data.duration });
      bitmap.close();
      videoEncoder.encode(frame, { keyFrame: Boolean(data.keyFrame) });
      frame.close();
      // Quality mode cannot drop frames to meet a bitrate. Drain this one-frame
      // batch so codec lookahead cannot deadlock our bounded upload pipeline.
      await videoEncoder.flush();
      if (encoderFailure || !encodedFrame) throw encoderFailure || new Error("Missing encoded frame.");
      const bytes = encodedFrame; encodedFrame = null;
      self.postMessage({ id: data.id, frame: bytes, transport: "h264" }, [bytes]);
      return;
    }
    context.drawImage(bitmap, 0, 0);
    bitmap.close();
    let image = await canvas.convertToBlob({ type: "image/png" });
    const frame = await image.arrayBuffer();
    image = null;
    self.postMessage({ id: data.id, frame, transport: "png" }, [frame]);
  } catch { self.postMessage({ id: data.id, error: true }); }
  finally {
    bitmap?.close();
    encoding = false;
  }
};

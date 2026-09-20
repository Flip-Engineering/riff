import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1365, height: 1050 }, reducedMotion: "reduce" });
const base = process.env.RIFF_URL;
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const workerRoute = `${base}/video-encoder.js`;
const fixtureURL = `${base}/?recording=${process.env.RIFF_TEST_TRACK}#studio`;

async function fresh() {
  await page.goto(fixtureURL);
  await page.reload();
  await page.waitForFunction(() => typeof createVideoFrameEncoder === "function" && !document.querySelector("#download-video").disabled);
  await page.evaluate(() => {
    window.encoderWorkers = [];
    const OriginalWorker = Worker;
    window.Worker = class extends OriginalWorker {
      constructor(...args) {
        super(...args);
        const record = { stopped: false }; encoderWorkers.push(record);
        const stop = this.terminate.bind(this);
        this.terminate = () => { record.stopped = true; stop(); };
        this.addEventListener("message", ({ data }) => { if (data.started) window.encoderStarted = true; });
        this.addEventListener("error", () => { window.encoderFailed = true; });
      }
    };
    window.encoderCanvas = () => {
      const canvas = document.createElement("canvas"); canvas.width = 321; canvas.height = 247;
      const context = canvas.getContext("2d", { alpha: false });
      const gradient = context.createLinearGradient(0, 0, canvas.width, canvas.height);
      gradient.addColorStop(0, "#b45165"); gradient.addColorStop(.5, "#badbcb"); gradient.addColorStop(1, "#4652ad");
      context.fillStyle = gradient; context.fillRect(0, 0, canvas.width, canvas.height);
      return canvas;
    };
    window.encoderPixelsEqual = async (canvas, bytes) => {
      const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
      const expected = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
      const other = document.createElement("canvas"); other.width = bitmap.width; other.height = bitmap.height;
      const context = other.getContext("2d"); context.drawImage(bitmap, 0, 0); bitmap.close();
      const actual = context.getImageData(0, 0, other.width, other.height).data;
      return other.width === canvas.width && other.height === canvas.height &&
        expected.length === actual.length && expected.every((value, index) => value === actual[index]);
    };
  });
}

try {
  await fresh();
  const asset = await page.request.get(workerRoute);
  assert.equal(asset.status(), 200);
  assert.match(asset.headers()["content-security-policy"], /(?:^|;)\s*worker-src 'self'(?:;|$)/);
  assert.deepEqual(await asset.body(), readFileSync("web/video-encoder.js"));
  assert.deepEqual(await (await page.request.get(`${base}/video.js`)).body(), readFileSync("web/video.js"));
  const exact = await page.evaluate(async () => {
    const canvas = encoderCanvas(), controller = new AbortController();
    const original = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = () => { throw new Error("Main-thread PNG encoder used."); };
    const raf = window.requestAnimationFrame, idle = window.requestIdleCallback;
    window.requestAnimationFrame = () => { throw new Error("Export waited for a paint."); };
    window.requestIdleCallback = () => { throw new Error("Export waited for idle time."); };
    let encoder;
    try {
      encoder = await createVideoFrameEncoder(canvas, controller.signal);
      const matches = [];
      for (const color of ["#cad0f0", "#573450", "#121312"]) {
        const context = canvas.getContext("2d"); context.fillStyle = color; context.fillRect(13, 29, 84, 53);
        matches.push(await encoderPixelsEqual(canvas, await encoder.encode()));
      }
      return matches;
    } finally {
      encoder?.close(); HTMLCanvasElement.prototype.toBlob = original;
      window.requestAnimationFrame = raf; window.requestIdleCallback = idle;
    }
  });
  assert.deepEqual(exact, [true, true, true]);
  assert.deepEqual(await page.evaluate(() => encoderWorkers.map(worker => worker.stopped)), [true]);
  console.log("PASS Same-origin packaged worker encodes exact pixels and frame order without main-thread PNG, animation-frame or idle callbacks");

  await fresh();
  const encoded = await page.evaluate(async () => {
    if (typeof VideoEncoder !== "function" || typeof VideoFrame !== "function") return { supported: false };
    const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 248;
    const context = canvas.getContext("2d", { alpha: false }); context.fillStyle = "#4265a8"; context.fillRect(0, 0, 320, 248);
    let encoder;
    try {
      encoder = await createVideoFrameEncoder(canvas, new AbortController().signal, { transport: "h264", fps: 12 });
    } catch (error) {
      return { supported: false, error: error.message };
    }
    try {
      const frame = new Uint8Array(await encoder.encode(0));
      return { supported: true, annexB: frame[0] === 0 && frame[1] === 0 && frame[2] === 0 && frame[3] === 1, bytes: frame.byteLength };
    } finally { encoder?.close(); }
  });
  if (encoded.supported) {
    assert(encoded.annexB && encoded.bytes > 32, JSON.stringify(encoded));
    assert(await page.evaluate(() => encoderWorkers.every(worker => worker.stopped)));
    console.log("PASS Worker WebCodecs H.264 path emits Annex-B frames for the accelerated export transport");
  } else {
    console.log(`SKIP WebCodecs H.264 path: ${encoded.error || "browser support unavailable"}`);
  }

  for (const support of ["absent", "load-failed", "context-unavailable"]) {
    await fresh();
    if (support === "absent") await page.evaluate(() => { window.OffscreenCanvas = undefined; });
    if (support === "load-failed") await page.route(workerRoute, route => route.fulfill({ status: 404, body: "Missing fixture worker" }));
    if (support === "context-unavailable") await page.route(workerRoute, route => route.fulfill({ contentType: "text/javascript", body: "self.onmessage=({data})=>self.postMessage({id:data.id,ready:true,transport:'png'});" }));
    const result = await page.evaluate(async () => {
      let encoder;
      try {
        encoder = await createVideoFrameEncoder(encoderCanvas(), new AbortController().signal, { transport: "h264" });
        return "accepted";
      } catch (error) { return error.message; }
      finally { encoder?.close(); }
    });
    assert.match(result, /accelerated video encoder/, support);
    assert(await page.evaluate(() => encoderWorkers.every(worker => worker.stopped)), support);
    await page.unroute(workerRoute);
  }
  console.log("PASS Unavailable H.264 workers reject initialization before a server job can receive mislabeled PNG bytes");

  for (const support of ["absent", "load-failed", "context-unavailable"]) {
    await fresh();
    if (support === "absent") await page.evaluate(() => { window.OffscreenCanvas = undefined; });
    if (support === "load-failed") await page.route(workerRoute, route => route.fulfill({ status: 404, body: "Missing fixture worker" }));
    if (support === "context-unavailable") await page.route(workerRoute, route => route.fulfill({ contentType: "text/javascript", body: "self.onmessage=({data})=>self.postMessage({id:data.id,ready:false});" }));
    const fallback = await page.evaluate(async () => {
      const canvas = encoderCanvas(), original = canvas.toBlob.bind(canvas); let used = 0;
      canvas.toBlob = (...args) => { used++; return original(...args); };
      const encoder = await createVideoFrameEncoder(canvas, new AbortController().signal);
      try { return { used: await encoderPixelsEqual(canvas, await encoder.encode()) && used }; }
      finally { encoder.close(); }
    });
    assert.equal(fallback.used, 1, support);
    assert(await page.evaluate(() => encoderWorkers.every(worker => worker.stopped)), support);
    await page.unroute(workerRoute);
  }
  console.log("PASS Missing worker support, failed worker loading and unavailable worker canvas retain pixel-exact fallback and release workers");

  await fresh();
  const lateBitmap = await page.evaluate(async () => {
    const canvas = encoderCanvas(), controller = new AbortController();
    const encoder = await createVideoFrameEncoder(canvas, controller.signal), original = createImageBitmap;
    let arrived, release, closed = false;
    const ready = new Promise(resolve => { arrived = resolve; });
    window.createImageBitmap = async (...args) => {
      const bitmap = await original(...args), close = bitmap.close.bind(bitmap);
      bitmap.close = () => { closed = true; close(); };
      return new Promise(resolve => { release = () => resolve(bitmap); arrived(); });
    };
    try {
      const result = encoder.encode().then(() => "resolved", error => error.name);
      await ready; controller.abort();
      const outcome = await result;
      release(); await new Promise(resolve => setTimeout(resolve, 0));
      return { outcome, closed, stopped: encoderWorkers.every(worker => worker.stopped) };
    } finally { window.createImageBitmap = original; encoder.close(); }
  });
  assert.deepEqual(lateBitmap, { outcome: "AbortError", closed: true, stopped: true });

  await fresh();
  const fallbackCancel = await page.evaluate(async () => {
    window.OffscreenCanvas = undefined;
    const canvas = encoderCanvas(), controller = new AbortController(); let callback;
    canvas.toBlob = value => { callback = value; };
    const encoder = await createVideoFrameEncoder(canvas, controller.signal);
    const result = encoder.encode().then(() => "resolved", error => error.name);
    controller.abort(); const outcome = await result;
    callback(new Blob()); encoder.close();
    return outcome;
  });
  assert.equal(fallbackCancel, "AbortError");
  console.log("PASS Cancellation rejects pending snapshots/fallback immediately, closes late bitmaps and terminates only the export worker");

  for (const phase of ["init", "frame"]) {
    await fresh();
    await page.route(workerRoute, route => route.fulfill({ contentType: "text/javascript", body: phase === "init"
      ? "self.onmessage=()=>{};"
      : "self.onmessage=({data})=>self.postMessage(data.type==='init'?{id:data.id,ready:true}:{id:-1,started:true});" }));
    await page.evaluate(phase => {
      window.encoderController = new AbortController();
      window.pendingEncoder = createVideoFrameEncoder(encoderCanvas(), encoderController.signal).then(async encoder => {
        try { if (phase === "frame") await encoder.encode(); return "resolved"; } finally { encoder.close(); }
      }).catch(error => error.name);
    }, phase);
    await page.waitForFunction(phase => phase === "init" ? encoderWorkers.length > 0 : window.encoderStarted, phase);
    await page.evaluate(() => encoderController.abort());
    assert.equal(await page.evaluate(() => pendingEncoder), "AbortError");
    assert(await page.evaluate(() => encoderWorkers.every(worker => worker.stopped)));
    await page.unroute(workerRoute);
  }
  console.log("PASS Cancel during worker initialization or an outstanding encode releases the worker and pending result");

  for (const failure of ["message", "crash", "between-frames"]) {
    await fresh();
    const errorSource = failure === "message" ? "self.postMessage({id:data.id,error:true});"
      : failure === "crash" ? "throw new Error('Owned worker failure fixture');"
      : "const frame=new ArrayBuffer(1);self.postMessage({id:data.id,frame},[frame]);setTimeout(()=>{throw new Error('Owned worker failure fixture')},0);";
    await page.route(workerRoute, route => route.fulfill({ contentType: "text/javascript", body:
      `self.onmessage=({data})=>{if(data.type==='init')self.postMessage({id:data.id,ready:true});else{${errorSource}}};` }));
    await page.evaluate(async failure => {
      window.failingEncoder = await createVideoFrameEncoder(encoderCanvas(), new AbortController().signal);
      window.failedEncoding = failingEncoder.encode().then(() => "resolved", error => error.message);
      if (failure === "between-frames") await failedEncoding;
    }, failure);
    if (failure === "between-frames") {
      await page.waitForFunction(() => window.encoderFailed);
      await page.evaluate(() => { window.failedEncoding = failingEncoder.encode().then(() => "resolved", error => error.message); });
    }
    assert.match(await page.evaluate(() => failedEncoding), /browser could not/);
    await page.evaluate(() => failingEncoder.close());
    assert(await page.evaluate(() => encoderWorkers.every(worker => worker.stopped)));
    await page.unroute(workerRoute);
  }
  console.log("PASS Explicit worker errors, crashes and failures between frames stop encoding with a useful error and no fallback replay");

  await fresh();
  await page.route(workerRoute, route => route.fulfill({ contentType: "text/javascript", body:
    "self.onmessage=({data})=>self.postMessage(data.type==='init'?{id:data.id,ready:true}:{id:-1,started:true});" }));
  await page.locator("#download-video").click();
  await page.locator("#video-options summary").click();
  await page.locator("#video-width").fill("320"); await page.locator("#video-height").fill("248");
  await page.locator("#video-fps").fill("12");
  await page.locator("#video-selection").selectOption("passage");
  await page.locator("#video-end").fill("0:00.25");
  await page.locator("#render-video").click();
  await page.waitForFunction(() => videoExport?.id && window.encoderStarted);
  const cancelledId = await page.evaluate(() => videoExport.id);
  await page.locator("#cancel-video").click();
  await page.waitForFunction(() => videoExport === null);
  const cancelled = await (await page.request.get(`${base}/api/video-exports/${cancelledId}`)).json();
  assert.equal(cancelled.status, "cancelled"); assert.equal(cancelled.received, 0);
  assert(await page.evaluate(() => encoderWorkers.every(worker => worker.stopped)));
  await page.unroute(workerRoute);
  const downloaded = page.waitForEvent("download", { timeout: 30000 }); downloaded.catch(() => {});
  await page.locator("#render-video").click();
  const download = await downloaded;
  assert(download.suggestedFilename().endsWith(".mp4"));
  await page.waitForFunction(() => videoExport === null);
  assert(await page.evaluate(() => encoderWorkers.every(worker => worker.stopped)));
  assert.deepEqual(errors, []);
  console.log("PASS Actual studio cancellation during PNG encoding cancels the owned FFmpeg job; a fresh export completes through the real worker");
} finally { await browser.close(); }

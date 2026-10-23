// Export through the real Studio UI with CDP attached only to its page.
// Do not auto-attach/debug workers or intercept uploads. Requires built-in WebSocket.
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, writeFileSync, createReadStream, createWriteStream, renameSync, rmSync, statSync } from "node:fs";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import { resolve } from "node:path";
import { cpus, platform, release, loadavg } from "node:os";
import { chromium } from "playwright"; // Executable discovery only; never launch/connect with Playwright.
import { diskSpace, resourceSample, sampleNvidia } from "./video-benchmark-resources.mjs";

assert.equal(typeof WebSocket, "function", "This driver requires Node's built-in WebSocket API.");
class CDP {
  constructor(socket) {
    this.socket = socket; this.sequence = 0; this.pending = new Map(); this.listeners = new Map(); this.commands = new Set(); this.waiters = new Set();
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      if (message.id) {
        const pending = this.pending.get(message.id); this.pending.delete(message.id);
        if (pending) message.error ? pending.reject(new Error(JSON.stringify(message.error))) : pending.resolve(message.result);
      } else for (const listener of this.listeners.get(message.method) || []) listener(message.params, message.sessionId);
    });
    socket.addEventListener("close", () => {
      for (const pending of this.pending.values()) pending.reject(new Error("Browser debugging connection closed."));
      this.pending.clear();
      for (const waiter of this.waiters) waiter.reject(new Error("Browser debugging connection closed."));
      this.waiters.clear();
    });
  }
  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true });
    });
    return new CDP(socket);
  }
  on(method, listener) {
    const values = this.listeners.get(method) || []; values.push(listener); this.listeners.set(method, values);
    return () => { const index = values.indexOf(listener); if (index >= 0) values.splice(index, 1); };
  }
  waitForEvent(method, sessionId) {
    return new Promise((resolve, reject) => {
      const waiter = { reject: error => { remove(); reject(error); } };
      const remove = this.on(method, (params, eventSession) => {
        if (eventSession !== sessionId) return;
        remove(); this.waiters.delete(waiter); resolve(params);
      });
      this.waiters.add(waiter);
    });
  }
  send(method, params = {}, sessionId) {
    this.commands.add(method);
    return new Promise((resolve, reject) => {
      const id = ++this.sequence; this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
  async evaluate(session, fn, argument) {
    const result = await this.send("Runtime.evaluate", { expression: `(${fn.toString()})(${JSON.stringify(argument) ?? "undefined"})`,
      awaitPromise: true, returnByValue: true }, session);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  }
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function hashStream(stream) { const hash = createHash("sha256"); for await (const chunk of stream) hash.update(chunk); return hash.digest("hex"); }
async function fetchHash(url) { const response = await fetch(url); assert(response.ok); return hashStream(response.body); }
async function childExit(child) { if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return; await once(child, "exit"); }
async function frameHashes(file, target) {
  const child = spawn("ffmpeg", ["-v", "error", "-y", "-threads", "2", "-i", file, "-map", "0:v:0", "-f", "framemd5", target], { stdio: ["ignore", "ignore", "pipe"] });
  let log = ""; child.stderr.on("data", bytes => { log = (log + bytes).slice(-16000); });
  const [code] = await once(child, "exit"); assert.equal(code, 0, log);
  let tail = "", count = 0;
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(target)) {
    hash.update(chunk); const lines = (tail + chunk.toString()).split("\n"); tail = lines.pop();
    count += lines.filter(line => line && !line.startsWith("#")).length;
  }
  if (tail && !tail.startsWith("#")) count++;
  return { frames: count, sha256: hash.digest("hex") };
}
const output = resolve(process.argv[2] || `test-results/video-cdp-${Date.now()}`);
mkdirSync(output);
const temporary = mkdtempSync(`${output}/temporary-`);
const fixtureEnv = { ...process.env, TMPDIR: temporary, TEMP: temporary, TMP: temporary };
const dimensions = (process.env.RIFF_BENCH_DIMENSIONS || "2160x2160x60").split(",").map(value => value.split("x").map(Number));
const durations = (process.env.RIFF_BENCH_DURATIONS || "2,10").split(",").map(Number);
const transports = (process.env.RIFF_BENCH_TRANSPORTS || "png").split(",");
assert(dimensions.every(x => x.length === 3 && x.every(n => Number.isFinite(n) && n > 0)));
assert(durations.every(n => Number.isFinite(n) && n > 0));
assert(transports.every(x => ["png", "h264"].includes(x)));
const sourceRevision = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
const sourceDirty = Boolean(execFileSync("git", ["status", "--porcelain", "--untracked-files=no"], { encoding: "utf8" }).trim());
const sampleResources = process.env.RIFF_BENCH_SAMPLE_RESOURCES !== "0";
const browserBinary = process.env.RIFF_BROWSER_EXECUTABLE || chromium.executablePath();
const browserArgs = ["--headless", "--no-sandbox", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
  `--user-data-dir=${temporary}/browser-profile`, "--no-first-run", "--no-default-browser-check", "--disable-dev-shm-usage",
  "--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-default-apps",
  "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
  "--force-color-profile=srgb", ...(process.platform === "darwin" ? ["--use-angle=metal"] : ["--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader"]),
  ...JSON.parse(process.env.RIFF_BENCH_BROWSER_ARGS || "[]")];
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"], { env: fixtureEnv });
let diagnostics = "", browserLog = "", browser, cdp, session, monitor, sampler, gpu, interrupted = false, activeRun;
let samplingErrors = [];
const results = [];
server.stderr.on("data", bytes => { diagnostics = (diagnostics + bytes).slice(-8000); });
const interrupt = () => { interrupted = true; browser?.kill("SIGTERM"); server.kill("SIGTERM"); };
process.once("SIGINT", interrupt); process.once("SIGTERM", interrupt);
try {
  const fixture = await new Promise((resolve, reject) => {
    let line = "";
    server.stdout.on("data", bytes => { line += bytes; if (line.includes("\n")) { try { resolve(JSON.parse(line.split("\n")[0])); } catch (error) { reject(error); } } });
    server.once("error", reject); server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
  });
  browser = spawn(browserBinary, browserArgs, { env: fixtureEnv, stdio: ["ignore", "ignore", "pipe"] });
  const websocket = await new Promise((resolve, reject) => {
    browser.stderr.on("data", bytes => { browserLog = (browserLog + bytes).slice(-16000); const match = browserLog.match(/DevTools listening on (ws:\/\/\S+)/); if (match) resolve(match[1]); });
    browser.once("error", reject); browser.once("exit", code => reject(new Error(`Browser exited ${code}: ${browserLog}`)));
  });
  cdp = await CDP.connect(websocket);
  const version = await cdp.send("Browser.getVersion"), graphics = await cdp.send("SystemInfo.getInfo");
  const incognito = process.env.RIFF_BENCH_CONTEXT === "incognito";
  const browserContextId = incognito ? (await cdp.send("Target.createBrowserContext", { disposeOnDetach: true })).browserContextId : undefined;
  mkdirSync(`${temporary}/downloads`);
  await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: `${temporary}/downloads`, ...(browserContextId ? { browserContextId } : {}) });
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank", ...(browserContextId ? { browserContextId } : {}) });
  ({ sessionId: session } = await cdp.send("Target.attachToTarget", { targetId, flatten: true }));
  await cdp.send("Page.enable", {}, session); await cdp.send("Runtime.enable", {}, session);
  // Raw network events have no Playwright RequestDispatcher/body cache and do
  // not pause requests. Never enable Fetch or Target worker auto-attachment.
  await cdp.send("Network.enable", { maxPostDataSize: 0 }, session);
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1365, height: 1050, deviceScaleFactor: 1, mobile: false }, session);
  let workerAssets = [], protocolPostDataCharacters = 0, protocolFramePostDataCharacters = 0;
  const assetIds = new Map();
  cdp.on("Network.requestWillBeSent", event => {
    const bodyCharacters = (event.request.postData?.length || 0) + (event.request.postDataEntries || []).reduce((n, x) => n + (x.bytes?.length || 0), 0);
    protocolPostDataCharacters += bodyCharacters;
    if (/\/frames$/.test(event.request.url)) protocolFramePostDataCharacters += bodyCharacters;
    if (!event.request.url.endsWith("/video-encoder.js")) return;
    const record = { request_id: event.requestId, request_wall_seconds: event.wallTime, request_monotonic_seconds: event.timestamp, routed: false };
    workerAssets.push(record); assetIds.set(event.requestId, record);
  });
  cdp.on("Network.responseReceived", event => { const record = assetIds.get(event.requestId); if (record) { record.response_monotonic_seconds = event.timestamp; record.status = event.response.status; record.timing = event.response.timing; } });
  const sourceAudioHash = await fetchHash(`${fixture.url}/api/tracks/${fixture.track_id}/audio`);
  const environment = { source_revision: sourceRevision, source_dirty: sourceDirty, browser: version.product, browser_binary: browserBinary,
    browser_args: browserArgs, platform: platform(), release: release(), cpu: cpus()[0].model, graphics: graphics.gpu,
    worker_auto_attach: false, upload_interception: false, driver: "minimal CDP, page target only", harness_time_origin_ms: performance.timeOrigin,
    source_audio_sha256: sourceAudioHash, load_average_start: loadavg(), disk_start: diskSpace(output),
    cpu_note: "Cumulative process/worker CPU and process-lifetime ps percentages; sampling/harness overhead included.",
    memory_note: "500 ms sampled sum of descendants RSS, shared pages counted; not physical footprint.",
    gpu_note: "System-wide NVIDIA counters, not export GPU cost.",
    temporary_note: "Scoped browser/fixture files including owned unlinked files on Linux, sampled every 500 ms." };
  const flush = () => writeFileSync(`${output}/results.json`, JSON.stringify({ environment, results }, null, 2) + "\n");
  flush(); console.log(JSON.stringify({ sourceRevision, sourceDirty, driver: environment.driver, browser: version.product, disk: environment.disk_start }));
  let navigated = false;
  for (const [width, height, fps] of dimensions) for (const duration of durations) for (const transport of transports) {
    workerAssets = []; assetIds.clear(); protocolPostDataCharacters = 0; protocolFramePostDataCharacters = 0; samplingErrors = [];
    const loaded = cdp.waitForEvent("Page.loadEventFired", session);
    if (navigated) await cdp.send("Page.reload", {}, session);
    else await cdp.send("Page.navigate", { url: `${fixture.url}/?recording=${fixture.track_id}#studio` }, session);
    await loaded; navigated = true;
    while (!await cdp.evaluate(session, () => typeof createVideoFrameEncoder === "function" && Boolean(document.querySelector("#download-video")) && !document.querySelector("#download-video").disabled)) await pause(50);
    await cdp.evaluate(session, async ({ width, height, fps, duration, transport }) => {
      await visualizationFor(selected.id);
      preferredVideoTransport = async () => transport;
      await openVideoExport(); document.querySelector("#video-options").open = true;
      for (const [key, value] of Object.entries({ width, height, fps })) { const input = document.querySelector(`#video-${key}`); input.value = String(value); input.dispatchEvent(new Event("input", { bubbles: true })); }
      document.querySelector("#video-selection").value = "passage";
      document.querySelector("#video-start").value = "0"; document.querySelector("#video-end").value = String(duration); updateVideoPassage();
      window.benchmarkWorkers = []; window.benchmarkFrameUploads = []; window.benchmarkJob = null;
      window.benchmarkResourceTimings = [];
      new PerformanceObserver(list => {
        for (const entry of list.getEntries()) if (entry.name.endsWith("/video-encoder.js")) benchmarkResourceTimings.push(entry.toJSON());
      }).observe({ type: "resource", buffered: true });
      const factory = createVideoFrameEncoder;
      createVideoFrameEncoder = (canvas, signal, options = {}) => factory(canvas, signal, { ...options, trace: true });
      const NativeWorker = Worker;
      window.Worker = class extends NativeWorker {
        constructor(...args) {
          const opened = performance.now(); super(...args);
          const record = { index: benchmarkWorkers.length, opened_ms: opened, constructed_ms: performance.now(), ready_ms: null,
            stopped: false, retired_ms: null, encoded_bytes: 0, requests: [], phases: [] };
          benchmarkWorkers.push(record);
          const post = this.postMessage.bind(this), stop = this.terminate.bind(this);
          this.postMessage = (...values) => { const data = values[0]; record.requests.push({ id: data.id, type: data.type, requested_ms: performance.now(), posted_ms: performance.now() }); return post(...values); };
          this.terminate = () => { record.stopped = true; record.stopped_ms = performance.now(); stop(); };
          this.addEventListener("message", ({ data }) => {
            if (data.phase) { record.phases.push({ phase: data.phase, request: data.id, received_ms: performance.now(), worker_ms: data.worker_ms, worker_origin_ms: data.worker_origin_ms }); return; }
            const request = record.requests.find(value => value.id === data.id); if (request) { request.completed_ms = performance.now(); request.error = data.error; }
            if (data.ready) record.ready_ms = performance.now() - record.opened_ms;
            if (data.frame) record.encoded_bytes += data.frame.byteLength;
            if (data.retire) record.retired_ms = performance.now();
          });
        }
      };
      const nativeFetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const url = typeof args[0] === "string" ? args[0] : args[0].url;
        const frames = /\/api\/video-exports\/[a-f0-9]+\/frames$/.test(url);
        const record = frames ? { requested_ms: performance.now(), index: Number(args[1]?.headers?.["X-Riff-Frame"]) } : null;
        if (record) benchmarkFrameUploads.push(record);
        const response = await nativeFetch(...args);
        if (record) { record.response_ms = performance.now(); record.status = response.status; void response.clone().json().then(value => { record.received = value.received; record.error = value.error; }).catch(() => {}); }
        if (args[1]?.method === "POST" && /\/video-exports$/.test(url)) void response.clone().json().then(value => { window.benchmarkJob = value; });
        return response;
      };
      window.benchmarkProgress = { samples: [], longest_task_gap_ms: 0 };
      let previous = performance.now();
      window.benchmarkTimer = setInterval(() => { const now = performance.now(); benchmarkProgress.longest_task_gap_ms = Math.max(benchmarkProgress.longest_task_gap_ms, now - previous); previous = now;
        benchmarkProgress.samples.push({ milliseconds: now, progress: document.querySelector("#video-progress").value, visibility: document.visibilityState }); }, 100);
    }, { width, height, fps, duration, transport });
    const samples = [], polls = [], workerTargets = [];
    const sample = () => { if (sampleResources) samples.push({ ...resourceSample(temporary), harness_memory: process.memoryUsage() }); };
    sample(); gpu = sampleResources ? sampleNvidia() : null;
    sampler = sampleResources ? setInterval(() => { try { sample(); } catch (error) { samplingErrors.push({ wall_time: new Date().toISOString(), error: String(error) }); clearInterval(sampler); sampler = null; } }, 500) : null;
    activeRun = { width, height, fps, duration, transport, samples, polls, worker_assets: workerAssets, sampling_errors: samplingErrors };
    const began = performance.now();
    await cdp.evaluate(session, () => { document.querySelector("#video-form").requestSubmit(); });
    let job, terminalFailure;
    monitor = setInterval(() => {
      if (!job || terminalFailure) return;
      void fetch(`${fixture.url}/api/video-exports/${job.id}`).then(response => response.json()).then(value => {
        if (["failed", "interrupted", "cancelled"].includes(value.status)) { terminalFailure = value; activeRun.final_job = value; browser.kill("SIGTERM"); }
      }).catch(() => {});
    }, 1000);
    while (true) {
      const state = await cdp.evaluate(session, () => ({ active: videoExport !== null, job: benchmarkJob,
        error: document.querySelector("#video-error").hidden ? null : document.querySelector("#video-error").textContent,
        observed_ms: performance.now(), time_origin_ms: performance.timeOrigin, progress: document.querySelector("#video-progress").value,
        workers: benchmarkWorkers.slice(-2).map(value => ({ index: value.index, ready_ms: value.ready_ms, retired_ms: value.retired_ms,
          stopped: value.stopped, phase: value.phases.at(-1), request: value.requests.at(-1) })) }));
      polls.push(state); if (state.job) { job = state.job; activeRun.job = job; }
      if (state.error) throw new Error(state.error);
      if (job && !state.active) break;
      const info = await cdp.send("Target.getTargets");
      workerTargets.push({ seconds: performance.now() / 1000, targets: info.targetInfos.filter(value => value.type === "worker").map(value => ({ attached: value.attached, url: value.url })) });
      await pause(100);
    }
    clearInterval(monitor); monitor = null;
    const elapsed = (performance.now() - began) / 1000;
    if (sampler) clearInterval(sampler); sampler = null; sample(); gpu?.stop();
    assert.equal(job.transport, transport);
    assert(workerTargets.every(sample => sample.targets.every(target => !target.attached)), "No debugger may attach to a worker");
    const browserState = await cdp.evaluate(session, () => { clearInterval(benchmarkTimer); benchmarkProgress.final_value = document.querySelector("#video-progress").value;
      return { workers: benchmarkWorkers, progress: benchmarkProgress, frame_uploads: benchmarkFrameUploads, worker_resource_timing: benchmarkResourceTimings, time_origin_ms: performance.timeOrigin }; });
    const completed = await (await fetch(`${fixture.url}/api/video-exports/${job.id}`)).json();
    assert.equal(completed.status, "done"); assert(completed.receipt); assert(browserState.workers.every(value => value.stopped));
    assert.equal(browserState.progress.final_value, 1, "Completed progress is visible without pointer input");
    browserState.progress.intermediate_observed = browserState.progress.samples.some(value => value.progress > 0 && value.progress < 1);
    const name = `${width}x${height}-${fps}fps-${duration}s-${transport}`;
    const file = `${output}/${name}.mp4`, response = await fetch(`${fixture.url}${completed.download_url}`); assert(response.ok);
    await pipeline(Readable.fromWeb(response.body), createWriteStream(`${file}.part`)); renameSync(`${file}.part`, file);
    assert.equal(await fetchHash(`${fixture.url}${completed.original_audio.download_url}`), sourceAudioHash);
    const streams = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-of", "json", file])).streams;
    const visual = streams.find(value => value.codec_type === "video"), sound = streams.find(value => value.codec_type === "audio");
    assert.equal(Number(visual.nb_frames), job.frames); assert.equal(visual.width, width); assert.equal(visual.height, height);
    const [rate, divisor] = visual.avg_frame_rate.split("/").map(Number); assert.equal(rate / divisor, fps);
    assert(Math.abs(Number(visual.duration) - duration) < 1 / fps); assert(Math.abs(Number(sound.duration) - duration) < .04);
    const hashes = await frameHashes(file, `${output}/${name}.framemd5`); assert.equal(hashes.frames, job.frames);
    const result = { name, width, height, fps, duration, transport, wall_seconds: elapsed, frames_per_second: job.frames / elapsed,
      final_bytes: statSync(file).size, final_sha256: await hashStream(createReadStream(file)), decoded_frames_sha256: hashes.sha256,
      original_audio_sha256: sourceAudioHash, receipt: completed.receipt, ...browserState, resources: samples, sampling_errors: samplingErrors,
      sampled_peak_rss_bytes: samples.length ? samples.reduce((n, x) => Math.max(n, x.rss), 0) : null,
      sampled_peak_temporary_allocated_bytes: samples.length ? samples.reduce((n, x) => Math.max(n, x.temporary_allocated_bytes), 0) : null,
      worker_assets: workerAssets, worker_targets: workerTargets, retirements: browserState.workers.filter(value => value.retired_ms !== null).length,
      system_gpu_samples: gpu?.samples || null, system_gpu_note: gpu?.note() || "Sampling disabled", protocol_post_data_characters: protocolPostDataCharacters, protocol_frame_post_data_characters: protocolFramePostDataCharacters,
      cdp_commands: [...cdp.commands], polls };
    assert(!result.cdp_commands.some(method => method === "Target.setAutoAttach" || method.startsWith("Fetch.")));
    results.push(result); activeRun = null; gpu = null; flush();
    console.log(JSON.stringify({ name, wall_seconds: elapsed, frames: job.frames, worker_count: result.workers.length, retirements: result.retirements,
      init_ms: result.workers.map(value => value.ready_ms), protocol_post_data_characters: protocolPostDataCharacters }));
  }
} catch (error) {
  writeFileSync(`${output}/failure.json`, JSON.stringify({ error: String(error), interrupted, observed_at: new Date().toISOString(),
    harness_time_origin_ms: performance.timeOrigin, active_run: activeRun, diagnostics, browser_log: browserLog }, null, 2) + "\n");
  throw error;
} finally {
  if (sampler) clearInterval(sampler); if (monitor) clearInterval(monitor); gpu?.stop();
  browser?.kill("SIGTERM"); await childExit(browser || { exitCode: 0 });
  if (server.exitCode === null && server.signalCode === null) { server.kill("SIGTERM"); await childExit(server); }
  rmSync(temporary, { recursive: true, force: true });
  process.removeListener("SIGINT", interrupt); process.removeListener("SIGTERM", interrupt);
}
